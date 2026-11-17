"""Dispatches Telegram updates. Update-level dedup happens first."""
import logging

from sqlalchemy.exc import IntegrityError

from app.admin.bot import AdminBot
from app.ctx import Ctx
from app.models import ProcessedUpdate
from app.services.connections import ConnectionService
from app.services.consent import ConsentService
from app.services.engine import Engine
from app.services.outbox import Outbox

log = logging.getLogger("ghost.router")


class UpdateRouter:
    def __init__(self, ctx: Ctx, engine: Engine, consent: ConsentService, admin: AdminBot, conns: ConnectionService, outbox: Outbox):
        self.ctx, self.engine, self.consent, self.admin, self.conns, self.outbox = ctx, engine, consent, admin, conns, outbox

    async def handle(self, update: dict) -> bool:
        """Returns False when the update was a duplicate."""
        uid = update.get("update_id")
        if uid is not None:
            async with self.ctx.sm() as s:
                s.add(ProcessedUpdate(update_id=uid))
                try:
                    await s.commit()
                except IntegrityError:
                    return False

        if "business_connection" in update:
            await self._on_connection(update["business_connection"])
        elif "business_message" in update:
            await self.engine.on_business_message(update["business_message"])
        elif "deleted_business_messages" in update:
            await self.engine.on_deleted(update["deleted_business_messages"])
        elif "callback_query" in update:
            cq = update["callback_query"]
            if not await self.admin.on_callback(cq):
                await self.consent.handle_callback(cq)
        elif "message" in update:
            await self.admin.on_message(update["message"])
        # edited_business_message is intentionally ignored: edits never trigger replies.
        return True

    async def _on_connection(self, bc: dict) -> None:
        conn, change = await self.conns.upsert(bc)
        if conn is None:
            return
        if not conn.is_enabled:
            msg = "🔌 Business-подключение GHOST AI отключено. Автоответы остановлены."
        elif not conn.can_reply:
            msg = "⚠️ Бот подключён, но у него нет права отвечать на сообщения. Включите «Отвечать на сообщения» в Настройки → Business → Чат-боты."
        else:
            msg = "🔌 Бот подключён к вашему аккаунту. Автоответы по умолчанию на паузе: /chats — разрешить чаты, /resume — включить."
        await self.outbox.to_owner(msg)
