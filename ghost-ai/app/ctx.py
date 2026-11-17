from dataclasses import dataclass
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.config import Settings
from app.security import Crypto
from app.state import StateStore


@dataclass
class Ctx:
    settings: Settings
    sm: async_sessionmaker[AsyncSession]
    crypto: Crypto
    tg: Any  # TelegramClient-compatible
    ai: Any  # AIClient-compatible
    state: StateStore
