import asyncio
import logging
from datetime import timedelta

from sqlalchemy import delete

from app.ctx import Ctx
from app.models import Event, ProcessedUpdate, SeenMessage, Token, Usage, utcnow
from app.services.memory import MemoryService

log = logging.getLogger("ghost.retention")


async def run_retention_once(ctx: Ctx, memory: MemoryService) -> dict[str, int]:
    """Data retention policy (see docs/PRIVACY.md)."""
    days = await ctx.state.get("retention_days")
    now = utcnow()
    async with ctx.sm() as s:
        msgs = await memory.purge_older_than(s, days)
        await s.execute(delete(Token).where(Token.expires_at < now - timedelta(days=1)).execution_options(synchronize_session=False))
        await s.execute(delete(ProcessedUpdate).where(ProcessedUpdate.created_at < now - timedelta(days=3)).execution_options(synchronize_session=False))
        await s.execute(delete(SeenMessage).where(SeenMessage.created_at < now - timedelta(days=7)).execution_options(synchronize_session=False))
        await s.execute(delete(Event).where(Event.ts < now - timedelta(days=90)).execution_options(synchronize_session=False))
        await s.execute(delete(Usage).where(Usage.ts < now - timedelta(days=90)).execution_options(synchronize_session=False))
        await s.commit()
    return {"messages_deleted": msgs}


async def retention_loop(ctx: Ctx, memory: MemoryService, every_seconds: int = 3600) -> None:
    while True:
        try:
            res = await run_retention_once(ctx, memory)
            log.info("retention: %s", res)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            log.error("retention failed: %s", type(e).__name__)
        await asyncio.sleep(every_seconds)
