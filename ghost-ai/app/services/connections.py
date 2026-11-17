import logging

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.config import Settings
from app.models import Connection, utcnow

log = logging.getLogger("ghost.connections")


def parse_can_reply(bc: dict) -> bool:
    """Bot API 9.x moved the flag into `rights.can_reply`; older payloads had `can_reply`."""
    rights = bc.get("rights")
    if isinstance(rights, dict):
        return bool(rights.get("can_reply"))
    return bool(bc.get("can_reply", False))


class ConnectionService:
    def __init__(self, sm: async_sessionmaker[AsyncSession], settings: Settings):
        self.sm = sm
        self.settings = settings

    async def upsert(self, bc: dict) -> tuple[Connection | None, str]:
        """Register/update a BusinessConnection. Only the configured owner may connect.

        Returns (connection, change) where change is one of: new, updated, foreign, unconfigured.
        """
        user = bc.get("user") or {}
        owner_id = self.settings.owner_telegram_id
        if not owner_id:
            return None, "unconfigured"
        if user.get("id") != owner_id:
            log.warning("ignored business_connection from a non-owner account")
            return None, "foreign"
        async with self.sm() as s:
            conn = await s.get(Connection, bc["id"])
            change = "updated"
            if conn is None:
                conn = Connection(id=bc["id"], owner_user_id=owner_id)
                s.add(conn)
                change = "new"
            conn.owner_chat_id = bc.get("user_chat_id", conn.owner_chat_id or 0)
            conn.owner_name = (user.get("first_name") or "").strip()[:200] or conn.owner_name
            conn.can_reply = parse_can_reply(bc)
            conn.is_enabled = bool(bc.get("is_enabled", True))
            conn.updated_at = utcnow()
            await s.commit()
            return conn, change
