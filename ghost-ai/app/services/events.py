from datetime import timedelta

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Event, Token, utcnow
from app.security import new_token


async def log_event(s: AsyncSession, kind: str, chat_id: int | None = None, detail: str = "") -> None:
    """detail is metadata only (codes, counts) — NEVER message content."""
    s.add(Event(kind=kind, chat_id=chat_id, detail=detail[:200]))


async def count_events(s: AsyncSession, kind: str, since, chat_id: int | None = None) -> int:
    q = select(func.count()).select_from(Event).where(Event.kind == kind, Event.ts >= since)
    if chat_id is not None:
        q = q.where(Event.chat_id == chat_id)
    return (await s.execute(q)).scalar_one()


async def issue_token(s: AsyncSession, kind: str, ttl: timedelta, chat_id: int | None = None) -> str:
    tok = new_token()
    s.add(Token(token=tok, kind=kind, chat_id=chat_id, expires_at=utcnow() + ttl))
    await s.flush()
    return tok


async def consume_token(s: AsyncSession, token: str, kind: str) -> Token | None:
    """Atomically mark a token used; returns it only for the first valid consumer."""
    res = await s.execute(
        update(Token)
        .where(Token.token == token, Token.kind == kind, Token.used_at.is_(None), Token.expires_at > utcnow())
        .values(used_at=utcnow())
        .execution_options(synchronize_session=False)
    )
    if res.rowcount != 1:
        return None
    return await s.get(Token, token)
