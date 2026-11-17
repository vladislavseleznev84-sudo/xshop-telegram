"""Runtime switches stored in the DB so panel and bot share them and they survive restarts."""
import json
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.config import Settings
from app.models import AppState

DEFAULT_STYLE = (
    "Пиши как живой человек в мессенджере: коротко, просто, по делу, без канцелярита. "
    "Подстраивайся под тон и длину сообщений собеседника."
)

MODES = ("shadow", "live")


def _int(lo: int, hi: int):
    def conv(v: Any) -> int:
        if isinstance(v, bool):
            raise ValueError("expected number")
        n = int(v)
        if not lo <= n <= hi:
            raise ValueError(f"must be between {lo} and {hi}")
        return n

    return conv


def _bool(v: Any) -> bool:
    if isinstance(v, bool):
        return v
    if str(v).lower() in ("1", "true", "yes", "on"):
        return True
    if str(v).lower() in ("0", "false", "no", "off"):
        return False
    raise ValueError("expected true/false")


def _mode(v: Any) -> str:
    if v not in MODES:
        raise ValueError(f"mode must be one of {MODES}")
    return v


def _style(v: Any) -> str:
    s = str(v).strip()
    if not 1 <= len(s) <= 1500:
        raise ValueError("style must be 1..1500 chars")
    return s


CONVERTERS = {
    "paused": _bool,
    "mode": _mode,
    "style_prompt": _style,
    "takeover_minutes": _int(0, 1440),
    "debounce_seconds": _int(0, 60),
    "max_replies_per_hour": _int(1, 200),
    "retention_days": _int(1, 365),
}


class StateStore:
    def __init__(self, sm: async_sessionmaker[AsyncSession], settings: Settings):
        self.sm = sm
        self.defaults: dict[str, Any] = {
            "paused": True,  # ships disabled: owner must /resume explicitly
            "mode": "shadow",  # shadow = drafts go to the owner, nothing is sent to interlocutors
            "style_prompt": DEFAULT_STYLE,
            "takeover_minutes": 60,
            "debounce_seconds": 4,
            "max_replies_per_hour": 15,
            "retention_days": settings.default_retention_days,
        }

    async def all(self) -> dict[str, Any]:
        async with self.sm() as s:
            rows = (await s.execute(select(AppState))).scalars().all()
        out = dict(self.defaults)
        for r in rows:
            if r.key in out:
                out[r.key] = json.loads(r.value)
        return out

    async def get(self, key: str) -> Any:
        return (await self.all())[key]

    async def set(self, key: str, raw: Any) -> Any:
        if key not in CONVERTERS:
            raise ValueError(f"unknown setting: {key}")
        value = CONVERTERS[key](raw)
        async with self.sm() as s:
            row = await s.get(AppState, key)
            if row is None:
                s.add(AppState(key=key, value=json.dumps(value, ensure_ascii=False)))
            else:
                row.value = json.dumps(value, ensure_ascii=False)
            await s.commit()
        return value
