import logging

from app.ctx import Ctx
from app.models import Connection
from app.telegram.client import TelegramError

log = logging.getLogger("ghost.outbox")


class Outbox:
    """All outgoing Telegram messages go through here (rights check + error containment)."""

    def __init__(self, ctx: Ctx):
        self.ctx = ctx

    @staticmethod
    def can_send(conn: Connection) -> bool:
        return bool(conn.is_enabled and conn.can_reply)

    async def to_chat(self, conn: Connection, tg_chat_id: int, text: str, markup: dict | None = None) -> dict | None:
        """sendMessage on behalf of the business account. None if not allowed or failed."""
        if not self.can_send(conn):
            return None
        try:
            return await self.ctx.tg.send_message(
                tg_chat_id, text, business_connection_id=conn.id, reply_markup=markup
            )
        except TelegramError as e:
            log.warning("send to chat failed: %s", e.description)
            return None

    async def edit_in_chat(self, conn: Connection, tg_chat_id: int, message_id: int, text: str) -> bool:
        try:
            await self.ctx.tg.edit_message_text(tg_chat_id, message_id, text, business_connection_id=conn.id)
            return True
        except TelegramError as e:
            log.warning("edit failed: %s", e.description)
            return False

    async def to_owner(self, text: str, markup: dict | None = None) -> dict | None:
        owner = self.ctx.settings.owner_telegram_id
        if not owner:
            return None
        try:
            return await self.ctx.tg.send_message(owner, text, reply_markup=markup)
        except TelegramError as e:
            log.warning("send to owner failed: %s", e.description)
            return None
