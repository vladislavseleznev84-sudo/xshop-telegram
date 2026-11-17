"""Per-interlocutor memory. One encrypted profile per chat row; nothing is shared across chats."""
from datetime import datetime, timedelta

from pydantic import BaseModel, Field
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.ctx import Ctx
from app.models import Message, Profile, as_utc, utcnow

MAX_ITEMS = 40


class MemoryUpdate(BaseModel):
    name: str = ""
    context: str = ""  # how we know each other
    add_preferences: list[str] = Field(default_factory=list)
    add_agreements: list[str] = Field(default_factory=list)
    add_events: list[str] = Field(default_factory=list)
    add_facts: list[str] = Field(default_factory=list)


class ProfileData(BaseModel):
    name: str = ""
    context: str = ""
    relationship: str = "unknown"
    last_tone: str = "neutral"
    preferences: list[str] = Field(default_factory=list)
    agreements: list[str] = Field(default_factory=list)
    events: list[str] = Field(default_factory=list)
    facts: list[str] = Field(default_factory=list)
    first_seen: str = ""
    interactions: int = 0


def _merge_list(old: list[str], new: list[str]) -> list[str]:
    seen = {x.casefold() for x in old}
    out = list(old)
    for item in new:
        item = item.strip()[:300]
        if item and item.casefold() not in seen:
            out.append(item)
            seen.add(item.casefold())
    return out[-MAX_ITEMS:]


def merge_profile(p: ProfileData, upd: MemoryUpdate, *, relationship: str, tone: str) -> ProfileData:
    p = p.model_copy(deep=True)
    if upd.name.strip():
        p.name = upd.name.strip()[:100]
    if upd.context.strip():
        p.context = upd.context.strip()[:400]
    if relationship and relationship != "unknown":
        p.relationship = relationship
    p.last_tone = tone or p.last_tone
    p.preferences = _merge_list(p.preferences, upd.add_preferences)
    p.agreements = _merge_list(p.agreements, upd.add_agreements)
    p.events = _merge_list(p.events, upd.add_events)
    p.facts = _merge_list(p.facts, upd.add_facts)
    p.interactions += 1
    if not p.first_seen:
        p.first_seen = utcnow().date().isoformat()
    return p


class MemoryService:
    def __init__(self, ctx: Ctx):
        self.ctx = ctx

    async def load_profile(self, s: AsyncSession, chat_id: int) -> ProfileData:
        row = await s.get(Profile, chat_id)
        if row is None:
            return ProfileData()
        return ProfileData.model_validate(self.ctx.crypto.dec_json(row.data_enc))

    async def save_profile(self, s: AsyncSession, chat_id: int, p: ProfileData) -> None:
        enc = self.ctx.crypto.enc_json(p.model_dump())
        row = await s.get(Profile, chat_id)
        if row is None:
            s.add(Profile(chat_id=chat_id, data_enc=enc))
        else:
            row.data_enc = enc
            row.updated_at = utcnow()

    async def add_message(self, s: AsyncSession, chat_id: int, tg_message_id: int, role: str, text: str) -> None:
        s.add(Message(chat_id=chat_id, tg_message_id=tg_message_id, role=role, content_enc=self.ctx.crypto.enc(text)))

    async def history(self, s: AsyncSession, chat_id: int, limit: int) -> list[tuple[str, str, datetime]]:
        rows = (
            await s.execute(
                select(Message).where(Message.chat_id == chat_id).order_by(Message.created_at.desc(), Message.id.desc()).limit(limit)
            )
        ).scalars().all()
        return [(r.role, self.ctx.crypto.dec(r.content_enc), as_utc(r.created_at)) for r in reversed(rows)]

    async def delete_all(self, s: AsyncSession, chat_id: int) -> None:
        await s.execute(delete(Message).where(Message.chat_id == chat_id).execution_options(synchronize_session=False))
        await s.execute(delete(Profile).where(Profile.chat_id == chat_id).execution_options(synchronize_session=False))

    async def purge_older_than(self, s: AsyncSession, days: int) -> int:
        res = await s.execute(
            delete(Message).where(Message.created_at < utcnow() - timedelta(days=days)).execution_options(synchronize_session=False)
        )
        return res.rowcount or 0
