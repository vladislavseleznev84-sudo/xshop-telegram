import asyncio
import json
import logging
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path

from fastapi import FastAPI, Request, Response
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import text

from app.admin import api as admin_api
from app.admin.bot import COMMANDS, AdminBot
from app.config import Settings, get_settings
from app.ctx import Ctx
from app.db import init_db, make_engine, make_sessionmaker
from app.logging_setup import setup_logging
from app.ratelimit import SlidingWindow
from app.security import Crypto, safe_equal
from app.services.ai import ClaudeClient
from app.services.connections import ConnectionService
from app.services.consent import ConsentService
from app.services.engine import Engine
from app.services.memory import MemoryService
from app.services.outbox import Outbox
from app.services.retention import retention_loop
from app.state import StateStore
from app.telegram.client import TelegramClient
from app.telegram.router import UpdateRouter

log = logging.getLogger("ghost")
STATIC = Path(__file__).parent / "web" / "static"
MAX_BODY = 1_000_000

SECURITY_HEADERS = {
    "Content-Security-Policy": "default-src 'self'; img-src 'self' data:; frame-ancestors 'none'; base-uri 'none'; form-action 'self'",
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
    "X-Frame-Options": "DENY",
}


@dataclass
class Services:
    ctx: Ctx
    memory: MemoryService
    consent: ConsentService
    engine: Engine
    router: UpdateRouter
    login_limiter: SlidingWindow


def build_services(settings: Settings, *, tg=None, ai=None, engine=None) -> tuple[Services, object]:
    db_engine = engine or make_engine(settings.database_url)
    sm = make_sessionmaker(db_engine)
    ctx = Ctx(
        settings=settings,
        sm=sm,
        crypto=Crypto(settings.encryption_key.get_secret_value()),
        tg=tg or TelegramClient(settings.telegram_bot_token.get_secret_value()),
        ai=ai or ClaudeClient(settings.anthropic_api_key.get_secret_value(), settings.claude_model, settings.claude_max_tokens),
        state=StateStore(sm, settings),
    )
    memory = MemoryService(ctx)
    outbox = Outbox(ctx)
    consent = ConsentService(ctx, memory, outbox)
    conns = ConnectionService(sm, settings)
    eng = Engine(ctx, memory, consent, outbox, conns)
    admin = AdminBot(ctx, memory, consent, outbox)
    router = UpdateRouter(ctx, eng, consent, admin, conns, outbox)
    return Services(ctx, memory, consent, eng, router, SlidingWindow(10, 60)), db_engine


def create_app(settings: Settings | None = None, *, tg=None, ai=None, db_engine=None, run_background: bool = True) -> FastAPI:
    settings = settings or get_settings()
    setup_logging(settings.log_level)
    svc, engine = build_services(settings, tg=tg, ai=ai, engine=db_engine)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        await init_db(engine)
        bg: list[asyncio.Task] = []
        if run_background:
            await _configure_telegram(svc)
            bg.append(asyncio.create_task(retention_loop(svc.ctx, svc.memory)))
        yield
        for t in bg:
            t.cancel()
        await svc.engine.shutdown()
        if hasattr(svc.ctx.tg, "aclose"):
            await svc.ctx.tg.aclose()
        await engine.dispose()

    app = FastAPI(title="GHOST AI", docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan)
    app.state.svc = svc

    @app.middleware("http")
    async def headers(request: Request, call_next):
        resp = await call_next(request)
        for k, v in SECURITY_HEADERS.items():
            resp.headers.setdefault(k, v)
        if request.url.path.startswith(("/api", "/auth")):
            resp.headers["Cache-Control"] = "no-store"
        return resp

    @app.get("/healthz")
    async def healthz():
        try:
            async with svc.ctx.sm() as s:
                await s.execute(text("select 1"))
        except Exception:
            return JSONResponse({"ok": False}, status_code=503)
        return {"ok": True}

    @app.post("/telegram/webhook")
    async def webhook(request: Request):
        got = request.headers.get("x-telegram-bot-api-secret-token", "")
        if not safe_equal(got, settings.webhook_secret.get_secret_value()):
            return Response(status_code=403)
        raw = await request.body()
        if len(raw) > MAX_BODY:
            return Response(status_code=413)
        try:
            update = json.loads(raw)
        except ValueError:
            return Response(status_code=400)
        try:
            await svc.router.handle(update)
        except Exception as e:
            # Log the type only: exception text could contain message content.
            log.error("update handling failed: %s", type(e).__name__)
        return {"ok": True}

    app.include_router(admin_api.router)
    app.mount("/static", StaticFiles(directory=STATIC), name="static")

    @app.get("/")
    async def index():
        return FileResponse(STATIC / "index.html", headers={"Cache-Control": "no-cache"})

    return app


async def _configure_telegram(svc: Services) -> None:
    s = svc.ctx.settings
    try:
        await svc.ctx.tg.set_my_commands(COMMANDS)
        if s.auto_set_webhook and s.public_base_url:
            await svc.ctx.tg.set_webhook(f"{s.public_base_url}/telegram/webhook", s.webhook_secret.get_secret_value())
            log.info("webhook set")
        else:
            log.warning("webhook NOT set (AUTO_SET_WEBHOOK off or PUBLIC_BASE_URL empty)")
    except Exception as e:
        log.error("telegram setup failed: %s", type(e).__name__)


def app_factory() -> FastAPI:  # uvicorn --factory app.main:app_factory
    return create_app()
