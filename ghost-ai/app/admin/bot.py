"""Owner-only control surface inside Telegram: /start /status /pause /resume /stats /chats /forget /settings (+ /panel /set)."""
import logging
from datetime import timedelta

from sqlalchemy import func, select

from app.ctx import Ctx
from app.models import Chat, Connection, Event, Usage, utcnow
from app.security import sign
from app.services.consent import ConsentService
from app.services.events import count_events, issue_token, log_event
from app.services.memory import MemoryService
from app.services.outbox import Outbox
from app.state import CONVERTERS

log = logging.getLogger("ghost.admin")

COMMANDS = [
    {"command": "start", "description": "Запуск и справка"},
    {"command": "status", "description": "Состояние системы"},
    {"command": "pause", "description": "Остановить автоответы (стоп-кран)"},
    {"command": "resume", "description": "Возобновить автоответы"},
    {"command": "stats", "description": "Статистика"},
    {"command": "chats", "description": "Список чатов"},
    {"command": "forget", "description": "Удалить память о собеседнике: /forget <id>"},
    {"command": "settings", "description": "Настройки"},
    {"command": "panel", "description": "Ссылка на веб-панель"},
]

CONSENT_LABEL = {"none": "не спрашивали", "pending": "ждём ответа", "granted": "согласен", "declined": "отказал", "revoked": "отозвал"}


class AdminBot:
    def __init__(self, ctx: Ctx, memory: MemoryService, consent: ConsentService, outbox: Outbox):
        self.ctx, self.memory, self.consent, self.outbox = ctx, memory, consent, outbox

    def _is_owner(self, user_id: int | None) -> bool:
        owner = self.ctx.settings.owner_telegram_id
        return bool(owner) and user_id == owner

    async def _say(self, chat_id: int, text: str, markup: dict | None = None) -> None:
        try:
            await self.ctx.tg.send_message(chat_id, text, reply_markup=markup)
        except Exception as e:
            log.warning("admin reply failed: %s", type(e).__name__)

    # ---------------------------------------------------------------- messages
    async def on_message(self, msg: dict) -> None:
        user = msg.get("from") or {}
        chat = msg.get("chat") or {}
        if chat.get("type") != "private":
            return
        text = (msg.get("text") or "").strip()
        if not text.startswith("/"):
            return
        if not self.ctx.settings.owner_telegram_id and text.split()[0].split("@")[0] == "/start":
            await self._say(
                chat["id"],
                f"GHOST AI не настроен. Ваш Telegram ID: {user.get('id')}\n"
                "Впишите его в .env как OWNER_TELEGRAM_ID и перезапустите сервис.",
            )
            return
        if not self._is_owner(user.get("id")):
            return  # silent for everyone else
        cmd, _, arg = text.partition(" ")
        cmd = cmd.split("@")[0].lower()
        arg = arg.strip()
        handler = {
            "/start": self._start,
            "/status": self._status,
            "/pause": self._pause,
            "/resume": self._resume,
            "/stats": self._stats,
            "/chats": self._chats,
            "/forget": self._forget,
            "/settings": self._settings,
            "/set": self._set,
            "/panel": self._panel,
        }.get(cmd)
        if handler:
            await handler(chat["id"], arg)

    async def _start(self, cid: int, arg: str) -> None:
        st = await self.ctx.state.all()
        await self._say(
            cid,
            "👻 GHOST AI\n\n"
            "Ассистент отвечает вместо вас в Telegram Business — только в разрешённых вами чатах и только после согласия собеседника.\n\n"
            f"Сейчас: {'⏸ на паузе' if st['paused'] else '▶️ работает'}, режим {st['mode']}.\n\n"
            "/status — состояние\n/pause /resume — стоп-кран\n/chats — чаты и разрешения\n"
            "/forget <id> — удалить память о собеседнике\n/stats — статистика\n/settings — настройки\n/panel — веб-панель",
        )

    async def _status(self, cid: int, arg: str) -> None:
        st = await self.ctx.state.all()
        async with self.ctx.sm() as s:
            conns = (await s.execute(select(Connection))).scalars().all()
        lines = [
            f"Автоответы: {'⏸ ПАУЗА' if st['paused'] else '▶️ включены'}",
            f"Режим: {st['mode']} ({'отправка собеседникам' if st['mode'] == 'live' else 'черновики только вам'})",
            f"Claude: {'ключ задан' if self.ctx.settings.anthropic_api_key.get_secret_value() else '⚠️ ANTHROPIC_API_KEY не задан'} · {self.ctx.settings.claude_model}",
        ]
        if not conns:
            lines.append("Business-подключение: нет (Настройки Telegram → Business → Чат-боты)")
        for c in conns:
            lines.append(f"Подключение: {'вкл' if c.is_enabled else 'выкл'}, ответы {'разрешены' if c.can_reply else '⚠️ НЕТ права отвечать'}")
        try:
            wh = await self.ctx.tg.get_webhook_info()
            lines.append(f"Webhook: {'установлен' if wh.get('url') else '⚠️ не установлен'}, в очереди {wh.get('pending_update_count', 0)}")
            if wh.get("last_error_message"):
                lines.append(f"Последняя ошибка webhook: {wh['last_error_message'][:120]}")
        except Exception:
            lines.append("Webhook: не удалось проверить")
        await self._say(cid, "\n".join(lines))

    async def _pause(self, cid: int, arg: str) -> None:
        await self.ctx.state.set("paused", True)
        await self._say(cid, "⏸ Автоответы остановлены. Ничего не отправляется собеседникам, пока не выполните /resume.")

    async def _resume(self, cid: int, arg: str) -> None:
        await self.ctx.state.set("paused", False)
        mode = await self.ctx.state.get("mode")
        extra = (
            "Режим shadow: собеседникам уходят только запросы согласия, ответы приходят вам черновиками."
            if mode == "shadow"
            else "⚠️ Режим live: ответы отправляются собеседникам."
        )
        await self._say(cid, f"▶️ Автоответы включены. {extra}")

    async def _stats(self, cid: int, arg: str) -> None:
        now = utcnow()
        async with self.ctx.sm() as s:
            total = (await s.execute(select(func.count()).select_from(Chat))).scalar_one()
            allowed = (await s.execute(select(func.count()).select_from(Chat).where(Chat.enabled.is_(True)))).scalar_one()
            granted = (await s.execute(select(func.count()).select_from(Chat).where(Chat.consent_status == "granted"))).scalar_one()
            lines = [f"Чатов: {total} · разрешено: {allowed} · согласились: {granted}"]
            for label, delta in (("24 ч", timedelta(hours=24)), ("7 дней", timedelta(days=7))):
                since = now - delta
                inc = await count_events(s, "incoming", since)
                rep = await count_events(s, "reply_sent", since)
                drf = await count_events(s, "draft_sent", since)
                lines.append(f"За {label}: входящих {inc} · ответов {rep} · черновиков {drf}")
            tok = (
                await s.execute(select(func.coalesce(func.sum(Usage.input_tokens), 0), func.coalesce(func.sum(Usage.output_tokens), 0)).where(Usage.ts >= now - timedelta(hours=24)))
            ).one()
            lines.append(f"Токены Claude за 24 ч: {tok[0]} вх / {tok[1]} исх")
        await self._say(cid, "\n".join(lines))

    async def _list_rows(self) -> list[tuple[Chat, str]]:
        async with self.ctx.sm() as s:
            chats = (await s.execute(select(Chat).order_by(Chat.last_incoming_at.desc().nullslast()).limit(20))).scalars().all()
        return [(c, self.ctx.crypto.dec(c.name_enc) if c.name_enc else f"#{c.tg_chat_id}") for c in chats]

    async def _chats(self, cid: int, arg: str) -> None:
        rows = await self._list_rows()
        if not rows:
            await self._say(cid, "Чатов пока нет. Они появятся, когда кто-то напишет вам после подключения бота.")
            return
        await self._say(cid, "Чаты (по новизне). Ответы включаются только для чатов, которые вы разрешили:")
        for c, name in rows:
            flag = "🟢 разрешён" if c.enabled else "⚪️ не разрешён"
            markup = {
                "inline_keyboard": [
                    [
                        {"text": "Выключить" if c.enabled else "Разрешить", "callback_data": f"adm:toggle:{c.id}"},
                        {"text": "Забыть", "callback_data": f"adm:forget:{c.id}"},
                    ]
                ]
            }
            await self._say(cid, f"#{c.id} {name}\n{flag} · согласие: {CONSENT_LABEL.get(c.consent_status, c.consent_status)}", markup)

    async def _forget(self, cid: int, arg: str) -> None:
        if not arg.isdigit():
            await self._say(cid, "Укажите id чата из /chats: /forget 3")
            return
        await self._ask_forget(cid, int(arg))

    async def _ask_forget(self, cid: int, chat_pk: int) -> None:
        async with self.ctx.sm() as s:
            chat = await s.get(Chat, chat_pk)
        if not chat:
            await self._say(cid, "Нет такого чата.")
            return
        name = self.ctx.crypto.dec(chat.name_enc) if chat.name_enc else f"#{chat.tg_chat_id}"
        await self._say(
            cid,
            f"Удалить всю память и историю про «{name}»? Согласие собеседника сохранится.",
            {"inline_keyboard": [[{"text": "Да, удалить", "callback_data": f"adm:forgetok:{chat_pk}"}, {"text": "Отмена", "callback_data": "adm:cancel:0"}]]},
        )

    async def _settings(self, cid: int, arg: str) -> None:
        st = await self.ctx.state.all()
        await self._say(
            cid,
            "Настройки:\n"
            f"mode = {st['mode']}  (shadow | live)\n"
            f"takeover_minutes = {st['takeover_minutes']}  (пауза после вашего ручного сообщения)\n"
            f"debounce_seconds = {st['debounce_seconds']}  (ждать, пока собеседник допишет)\n"
            f"max_replies_per_hour = {st['max_replies_per_hour']}  (на чат)\n"
            f"retention_days = {st['retention_days']}  (хранение переписки)\n"
            f"style_prompt = {st['style_prompt']}\n\n"
            "Изменить: /set <ключ> <значение>, например /set mode live",
        )

    async def _set(self, cid: int, arg: str) -> None:
        key, _, val = arg.partition(" ")
        if key not in CONVERTERS or key == "paused" or not val.strip():
            await self._say(cid, "Формат: /set <ключ> <значение>. Ключи — в /settings. Пауза — /pause и /resume.")
            return
        try:
            value = await self.ctx.state.set(key, val.strip())
        except ValueError as e:
            await self._say(cid, f"Не принято: {e}")
            return
        warn = "\n⚠️ Теперь ответы отправляются собеседникам." if key == "mode" and value == "live" else ""
        await self._say(cid, f"Готово: {key} обновлён.{warn}")

    async def _panel(self, cid: int, arg: str) -> None:
        base = self.ctx.settings.public_base_url
        if not base:
            await self._say(cid, "PUBLIC_BASE_URL не задан — веб-панель недоступна.")
            return
        async with self.ctx.sm() as s:
            tok = await issue_token(s, "magic", timedelta(minutes=5))
            await s.commit()
        try:
            await self.ctx.tg.send_message(cid, f"Вход в панель (одноразовая ссылка, 5 минут):\n{base}/auth/magic?t={tok}", disable_preview=True)
        except Exception as e:
            log.warning("panel link failed: %s", type(e).__name__)

    # --------------------------------------------------------------- callbacks
    async def on_callback(self, cq: dict) -> bool:
        data = cq.get("data") or ""
        if not data.startswith("adm:"):
            return False
        user_id = (cq.get("from") or {}).get("id")
        if not self._is_owner(user_id):
            return True
        _, action, raw = data.split(":", 2)
        cid = (cq.get("message") or {}).get("chat", {}).get("id", user_id)
        try:
            await self.ctx.tg.answer_callback_query(cq["id"])
        except Exception:
            pass
        pk = int(raw) if raw.isdigit() else 0
        if action == "toggle":
            async with self.ctx.sm() as s:
                chat = await s.get(Chat, pk)
                if chat:
                    chat.enabled = not chat.enabled
                    await log_event(s, "chat_enabled" if chat.enabled else "chat_disabled", pk)
                    await s.commit()
                    await self._say(cid, f"#{pk}: {'🟢 разрешён' if chat.enabled else '⚪️ выключен'}")
        elif action == "forget":
            await self._ask_forget(cid, pk)
        elif action == "forgetok":
            async with self.ctx.sm() as s:
                await self.memory.delete_all(s, pk)
                await log_event(s, "memory_forgotten", pk, "by_owner")
                await s.commit()
            await self._say(cid, f"Память о чате #{pk} удалена.")
        return True
