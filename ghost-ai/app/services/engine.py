"""Business-message pipeline.

Order of gates (each one stops processing): structure -> own/bot message -> dedup -> flood ->
privacy commands -> paused -> chat allowlist -> connection rights -> consent -> (store, debounce)
-> takeover -> rate limit -> Claude -> repetition guard -> memory -> send / shadow draft.
"""
import asyncio
import difflib
import logging
from datetime import timedelta

from sqlalchemy import delete, select, update
from sqlalchemy.exc import IntegrityError

from app.ctx import Ctx
from app.models import Chat, Connection, Message, SeenMessage, Usage, as_utc, utcnow
from app.ratelimit import SlidingWindow
from app.services.ai import AIInput
from app.services.connections import ConnectionService
from app.services.consent import ConsentService
from app.services.events import count_events, log_event
from app.services.memory import MemoryService, merge_profile
from app.services.outbox import Outbox
from app.texts import parse_answer, pick_lang

log = logging.getLogger("ghost.engine")

GLOBAL_MAX_REPLIES_PER_HOUR = 200
MAX_REPLY_CHARS = 1500
REPEAT_RATIO = 0.85


def clean_reply(text: str) -> str:
    text = (text or "").strip().replace("**", "")
    if len(text) >= 2 and text[0] == text[-1] and text[0] in "\"'«":
        text = text[1:-1].strip()
    return text[:MAX_REPLY_CHARS]


def is_repeat(reply: str, previous: list[str]) -> bool:
    r = reply.casefold()
    return any(difflib.SequenceMatcher(None, r, p.casefold()).ratio() >= REPEAT_RATIO for p in previous)


class Engine:
    def __init__(self, ctx: Ctx, memory: MemoryService, consent: ConsentService, outbox: Outbox, conns: ConnectionService):
        self.ctx, self.memory, self.consent, self.outbox, self.conns = ctx, memory, consent, outbox, conns
        self.flood = SlidingWindow(limit=20, window_seconds=60)
        self._locks: dict[int, asyncio.Lock] = {}
        self._sleeping: dict[int, asyncio.Task] = {}
        self._tasks: set[asyncio.Task] = set()
        self._last_ai_alert: float | None = None

    # ---------------------------------------------------------------- intake
    async def on_business_message(self, msg: dict) -> None:
        ctx = self.ctx
        conn_id = msg.get("business_connection_id")
        chat_info = msg.get("chat") or {}
        sender = msg.get("from") or {}
        if not conn_id or chat_info.get("type") != "private" or "id" not in chat_info or "message_id" not in msg:
            return
        tg_chat_id = chat_info["id"]

        async with ctx.sm() as s:
            conn = await s.get(Connection, conn_id)
        if conn is None:
            conn = await self._recover_connection(conn_id)
            if conn is None:
                return

        # Never react to our own / another bot's business messages -> no reply loops.
        if msg.get("sender_business_bot"):
            return
        if sender.get("id") == conn.owner_user_id:
            await self._mark_owner_activity(conn_id, tg_chat_id)
            return
        if sender.get("is_bot") or sender.get("id") != tg_chat_id:
            return

        async with ctx.sm() as s:
            s.add(SeenMessage(connection_id=conn_id, tg_chat_id=tg_chat_id, tg_message_id=msg["message_id"]))
            try:
                await s.flush()
            except IntegrityError:
                return  # duplicate delivery
            chat = (
                await s.execute(select(Chat).where(Chat.connection_id == conn_id, Chat.tg_chat_id == tg_chat_id))
            ).scalar_one_or_none()
            if chat is None:
                chat = Chat(connection_id=conn_id, tg_chat_id=tg_chat_id, lang=pick_lang(sender.get("language_code")))
                s.add(chat)
            name = " ".join(x for x in (sender.get("first_name"), sender.get("last_name")) if x)
            if name:
                chat.name_enc = ctx.crypto.enc(name)  # identity metadata only, encrypted
            chat.last_incoming_at = utcnow()
            await s.flush()
            chat_pk, enabled, status, lang, notice_at = chat.id, chat.enabled, chat.consent_status, chat.lang, chat.notice_sent_at
            await log_event(s, "incoming", chat_pk)
            await s.commit()

        if not self.flood.allow(f"{conn_id}:{tg_chat_id}"):
            async with ctx.sm() as s:
                await log_event(s, "flood_dropped", chat_pk)
                await s.commit()
            return

        text = (msg.get("text") or msg.get("caption") or "").strip()
        cmd = text.lower().split()[0] if text else ""

        # Privacy commands always work, even when paused or chat not allow-listed.
        if cmd == "/ai_off":
            await self.consent.revoke(conn, chat_pk)
            return
        if cmd == "/ai_forget":
            await self.consent.forget(conn, chat_pk)
            return

        st = await ctx.state.all()
        reason = None
        if st["paused"]:
            reason = "skipped_paused"
        elif not enabled:
            reason = "skipped_not_allowed"
        elif not Outbox.can_send(conn):
            reason = "skipped_no_rights"
        if reason:
            async with ctx.sm() as s:
                await log_event(s, reason, chat_pk)
                await s.commit()
            return

        if cmd == "/ai_on":
            if status in ("revoked", "declined", "none", "pending"):
                await self.consent.send_notice(conn, chat_pk)
            return

        # ---- consent gate: before this point NO content was stored or sent anywhere ----
        if status == "none":
            await self.consent.send_notice(conn, chat_pk)
            return
        if status == "pending":
            ans = parse_answer(text)
            if ans:
                await self.consent.answer(conn, chat_pk, ans == "y")
            elif notice_at is None or utcnow() - as_utc(notice_at) > timedelta(hours=24):
                await self.consent.send_notice(conn, chat_pk)
            return
        if status != "granted":
            return  # declined / revoked: stay silent

        if not text:
            async with ctx.sm() as s:
                await log_event(s, "non_text_skipped", chat_pk)
                await s.commit()
            return
        async with ctx.sm() as s:
            await self.memory.add_message(s, chat_pk, msg["message_id"], "in", text[:4000])
            await s.commit()
        self._schedule(chat_pk, st["debounce_seconds"])

    async def on_deleted(self, upd: dict) -> None:
        """Interlocutor deleted messages -> drop our stored copies."""
        conn_id = upd.get("business_connection_id")
        tg_chat_id = (upd.get("chat") or {}).get("id")
        ids = upd.get("message_ids") or []
        if not conn_id or tg_chat_id is None or not ids:
            return
        async with self.ctx.sm() as s:
            chat = (
                await s.execute(select(Chat).where(Chat.connection_id == conn_id, Chat.tg_chat_id == tg_chat_id))
            ).scalar_one_or_none()
            if chat is None:
                return
            await s.execute(
                delete(Message).where(Message.chat_id == chat.id, Message.tg_message_id.in_(ids)).execution_options(synchronize_session=False)
            )
            await s.commit()

    async def _recover_connection(self, conn_id: str) -> Connection | None:
        try:
            bc = await self.ctx.tg.get_business_connection(conn_id)
        except Exception:
            return None
        conn, _ = await self.conns.upsert(bc)
        return conn

    async def _mark_owner_activity(self, conn_id: str, tg_chat_id: int) -> None:
        async with self.ctx.sm() as s:
            await s.execute(
                update(Chat)
                .where(Chat.connection_id == conn_id, Chat.tg_chat_id == tg_chat_id)
                .values(last_owner_message_at=utcnow())
                .execution_options(synchronize_session=False)
            )
            await s.commit()

    # ------------------------------------------------------------ scheduling
    def _lock(self, chat_pk: int) -> asyncio.Lock:
        return self._locks.setdefault(chat_pk, asyncio.Lock())

    def _schedule(self, chat_pk: int, delay: int) -> None:
        old = self._sleeping.pop(chat_pk, None)
        if old and not old.done():
            old.cancel()  # only ever a task that is still debouncing, never one mid-reply
        task = asyncio.create_task(self._debounced(chat_pk, delay))
        self._sleeping[chat_pk] = task
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def _debounced(self, chat_pk: int, delay: int) -> None:
        try:
            if delay:
                await asyncio.sleep(delay)
            self._sleeping.pop(chat_pk, None)
            async with self._lock(chat_pk):
                await self.process_chat(chat_pk)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            log.error("process_chat failed: %s", type(e).__name__)

    async def drain(self) -> None:
        while self._tasks:
            await asyncio.gather(*list(self._tasks), return_exceptions=True)

    async def shutdown(self) -> None:
        for t in list(self._tasks):
            t.cancel()
        await asyncio.gather(*list(self._tasks), return_exceptions=True)

    # ---------------------------------------------------------------- reply
    async def process_chat(self, chat_pk: int) -> None:
        ctx = self.ctx
        st = await ctx.state.all()
        async with ctx.sm() as s:
            chat = await s.get(Chat, chat_pk)
            conn = await s.get(Connection, chat.connection_id) if chat else None
            if not chat or not conn:
                return
            if st["paused"] or not chat.enabled or chat.consent_status != "granted" or not Outbox.can_send(conn):
                return

            lo = as_utc(chat.last_owner_message_at)
            if lo and st["takeover_minutes"] > 0 and utcnow() - lo < timedelta(minutes=st["takeover_minutes"]):
                await log_event(s, "takeover_skip", chat_pk)
                await s.commit()
                return

            since = utcnow() - timedelta(hours=1)
            per_chat = await count_events(s, "reply_sent", since, chat_pk) + await count_events(s, "draft_sent", since, chat_pk)
            global_n = await count_events(s, "reply_sent", since)
            if per_chat >= st["max_replies_per_hour"] or global_n >= GLOBAL_MAX_REPLIES_PER_HOUR:
                await log_event(s, "rate_limited", chat_pk)
                await s.commit()
                return

            history = await self.memory.history(s, chat_pk, ctx.settings.history_window)
            if not history or history[-1][0] != "in":
                return  # nothing awaiting an answer
            profile = await self.memory.load_profile(s, chat_pk)
            tg_chat_id, owner_name = chat.tg_chat_id, conn.owner_name
            display = ctx.crypto.dec(chat.name_enc) if chat.name_enc else str(tg_chat_id)

        inp = AIInput(owner_name=owner_name, style=st["style_prompt"], profile=profile, history=history, now=utcnow())
        previous = [t for r, t, _ in history if r == "out"][-8:]
        result = None
        reply = ""
        for attempt in range(2):
            try:
                result = await ctx.ai.decide(inp)
            except Exception as e:
                await self._ai_failed(chat_pk, type(e).__name__)
                return
            await self._record_usage(result)
            reply = clean_reply(result.decision.reply) if result.decision.should_reply else ""
            if reply and is_repeat(reply, previous):
                inp.avoid = reply
                reply = ""
                continue
            break
        dec = result.decision
        repeated = dec.should_reply and not reply and inp.avoid

        async with ctx.sm() as s:
            merged = merge_profile(
                await self.memory.load_profile(s, chat_pk), dec.memory, relationship=dec.relationship, tone=dec.tone
            )
            await self.memory.save_profile(s, chat_pk, merged)
            if repeated:
                await log_event(s, "repeat_blocked", chat_pk)
            await s.commit()

        if dec.needs_owner or repeated:
            note = dec.owner_note or "Модель зациклилась на повторе ответа — ответьте лично."
            async with ctx.sm() as s:
                await log_event(s, "needs_owner", chat_pk)
                await s.commit()
            await self.outbox.to_owner(f"🔔 {display}: {note[:500]}")
        if not reply:
            return

        # Kill switch is re-read right before sending so /pause is instant.
        st = await ctx.state.all()
        if st["paused"]:
            return
        if st["mode"] == "live":
            await self._send_live(conn, chat_pk, tg_chat_id, reply)
        else:
            last_in = next((t for r, t, _ in reversed(history) if r == "in"), "")
            await self.outbox.to_owner(f"📝 Черновик для {display} (режим shadow — собеседнику не отправлено)\n\nОн: {last_in[:300]}\n\nОтвет: {reply}")
            async with ctx.sm() as s:
                await log_event(s, "draft_sent", chat_pk)
                await s.commit()

    async def _send_live(self, conn: Connection, chat_pk: int, tg_chat_id: int, reply: str) -> None:
        ctx = self.ctx
        if ctx.settings.humanize_delay:
            try:
                await ctx.tg.send_chat_action(tg_chat_id, "typing", business_connection_id=conn.id)
            except Exception:
                pass
            await asyncio.sleep(min(len(reply) / 30, 5.0))
            if await ctx.state.get("paused"):
                return
        sent = await self.outbox.to_chat(conn, tg_chat_id, reply)
        async with ctx.sm() as s:
            if sent is None:
                await log_event(s, "send_failed", chat_pk, "reply")
            else:
                await self.memory.add_message(s, chat_pk, sent.get("message_id", 0), "out", reply)
                await log_event(s, "reply_sent", chat_pk)
            await s.commit()

    async def _record_usage(self, result) -> None:
        async with self.ctx.sm() as s:
            s.add(Usage(input_tokens=result.input_tokens, output_tokens=result.output_tokens, model=result.model))
            await s.commit()

    async def _ai_failed(self, chat_pk: int, err: str) -> None:
        async with self.ctx.sm() as s:
            await log_event(s, "ai_error", chat_pk, err)
            await s.commit()
        loop_time = asyncio.get_running_loop().time()
        if self._last_ai_alert is None or loop_time - self._last_ai_alert > 600:
            self._last_ai_alert = loop_time
            await self.outbox.to_owner(f"⚠️ Ошибка вызова Claude ({err}). Автоответы не отправлены. Проверьте ANTHROPIC_API_KEY и баланс.")
