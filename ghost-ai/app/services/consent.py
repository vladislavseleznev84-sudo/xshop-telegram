"""Per-chat consent state machine: none -> pending -> granted | declined; granted -> revoked.

Nothing from a chat is sent to an AI service or stored until its status is `granted`.
"""
import logging
from datetime import timedelta

from app.ctx import Ctx
from app.models import Chat, Connection, utcnow
from app.services.events import consume_token, issue_token, log_event
from app.services.memory import MemoryService
from app.services.outbox import Outbox
from app.texts import t

log = logging.getLogger("ghost.consent")
TOKEN_TTL = timedelta(days=7)


def _kb(rows: list[list[tuple[str, str]]]) -> dict:
    return {"inline_keyboard": [[{"text": txt, "callback_data": data} for txt, data in row] for row in rows]}


class ConsentService:
    def __init__(self, ctx: Ctx, memory: MemoryService, outbox: Outbox):
        self.ctx, self.memory, self.outbox = ctx, memory, outbox

    async def send_notice(self, conn: Connection, chat_pk: int) -> bool:
        days = await self.ctx.state.get("retention_days")  # read before opening the transaction
        async with self.ctx.sm() as s:
            chat = await s.get(Chat, chat_pk)
            if chat is None:
                return False
            tok = await issue_token(s, "consent", TOKEN_TTL, chat_pk)
            text = t(chat.lang, "notice", owner=conn.owner_name or "владельца", days=days)
            markup = _kb([[(t(chat.lang, "btn_yes"), f"c:{tok}:y"), (t(chat.lang, "btn_no"), f"c:{tok}:n")]])
            sent = await self.outbox.to_chat(conn, chat.tg_chat_id, text, markup)
            if sent is None:
                await s.rollback()
                async with self.ctx.sm() as s2:
                    await log_event(s2, "send_failed", chat_pk, "notice")
                    await s2.commit()
                return False
            chat.consent_status = "pending"
            chat.notice_sent_at = utcnow()
            await log_event(s, "notice_sent", chat_pk)
            await s.commit()
            return True

    async def answer(self, conn: Connection, chat_pk: int, yes: bool, *, edit_message_id: int | None = None) -> None:
        async with self.ctx.sm() as s:
            chat = await s.get(Chat, chat_pk)
            if chat is None:
                return
            lang, tg_chat_id = chat.lang, chat.tg_chat_id
            owner = conn.owner_name or "владелец"
            if yes:
                chat.consent_status = "granted"
                chat.consent_at = utcnow()
                await log_event(s, "consent_granted", chat_pk)
                tok = await issue_token(s, "consent", TOKEN_TTL, chat_pk)
                text = t(lang, "granted")
                markup = _kb([[(t(lang, "btn_revoke"), f"c:{tok}:r")]])
            else:
                chat.consent_status = "declined"
                await self.memory.delete_all(s, chat_pk)
                await log_event(s, "consent_declined", chat_pk)
                text = t(lang, "declined", owner=owner)
                markup = None
            await s.commit()
        await self._reply(conn, tg_chat_id, text, markup, edit_message_id)

    async def revoke(self, conn: Connection, chat_pk: int, *, edit_message_id: int | None = None) -> None:
        async with self.ctx.sm() as s:
            chat = await s.get(Chat, chat_pk)
            if chat is None:
                return
            chat.consent_status = "revoked"
            await self.memory.delete_all(s, chat_pk)
            await log_event(s, "consent_revoked", chat_pk)
            lang, tg_chat_id = chat.lang, chat.tg_chat_id
            await s.commit()
        await self._reply(conn, tg_chat_id, t(lang, "revoked", owner=conn.owner_name or "владелец"), None, edit_message_id)

    async def forget(self, conn: Connection, chat_pk: int) -> None:
        async with self.ctx.sm() as s:
            chat = await s.get(Chat, chat_pk)
            if chat is None:
                return
            await self.memory.delete_all(s, chat_pk)
            await log_event(s, "memory_forgotten", chat_pk, "by_user")
            lang, tg_chat_id = chat.lang, chat.tg_chat_id
            await s.commit()
        await self.outbox.to_chat(conn, tg_chat_id, t(lang, "forgotten"))

    async def _reply(self, conn: Connection, tg_chat_id: int, text: str, markup: dict | None, edit_message_id: int | None) -> None:
        if edit_message_id is not None and markup is None:
            if await self.outbox.edit_in_chat(conn, tg_chat_id, edit_message_id, text):
                return
        await self.outbox.to_chat(conn, tg_chat_id, text, markup)

    async def handle_callback(self, cq: dict) -> None:
        """Inline buttons: c:<token>:<y|n|r>. Token is bound to a chat; the presser must be that chat's user."""
        data = cq.get("data") or ""
        parts = data.split(":")
        if len(parts) != 3 or parts[0] != "c" or parts[2] not in ("y", "n", "r"):
            return
        _, tok, action = parts
        presser = (cq.get("from") or {}).get("id")
        msg = cq.get("message") or {}
        async with self.ctx.sm() as s:
            from app.models import Token

            row = await s.get(Token, tok)
            chat = await s.get(Chat, row.chat_id) if row and row.kind == "consent" and row.chat_id else None
            conn = await s.get(Connection, chat.connection_id) if chat else None
            if chat is None or conn is None or chat.tg_chat_id != presser:
                return  # not the interlocutor of this chat: ignore
            used = await consume_token(s, tok, "consent")
            await s.commit()
        try:
            await self.ctx.tg.answer_callback_query(cq["id"], t(chat.lang, "already") if used is None else None)
        except Exception:
            pass
        if used is None:
            return
        mid = msg.get("message_id")
        if action == "r":
            await self.revoke(conn, chat.id, edit_message_id=mid)
        elif chat.consent_status in ("pending", "declined", "revoked", "none"):
            await self.answer(conn, chat.id, action == "y", edit_message_id=mid)
