import itertools

import pytest
import pytest_asyncio
from cryptography.fernet import Fernet
from sqlalchemy import select

from app.config import Settings
from app.db import init_db, make_engine
from app.main import build_services, create_app
from app.models import Chat
from app.services.ai import AIResult, Decision

OWNER = 1
CONN = "conn-1"
USER = 555


class FakeTG:
    def __init__(self):
        self.sent: list[dict] = []
        self.edits: list[dict] = []
        self.answers: list[dict] = []
        self.actions: list[dict] = []
        self._mid = itertools.count(1000)
        self.business_connections: dict[str, dict] = {}

    async def send_message(self, chat_id, text, *, business_connection_id=None, reply_markup=None, disable_preview=True):
        self.sent.append(dict(chat_id=chat_id, text=text, bcid=business_connection_id, markup=reply_markup))
        return {"message_id": next(self._mid)}

    async def send_chat_action(self, chat_id, action="typing", *, business_connection_id=None):
        self.actions.append(dict(chat_id=chat_id, action=action, bcid=business_connection_id))

    async def edit_message_text(self, chat_id, message_id, text, *, business_connection_id=None, reply_markup=None):
        self.edits.append(dict(chat_id=chat_id, message_id=message_id, text=text, bcid=business_connection_id))

    async def answer_callback_query(self, callback_query_id, text=None):
        self.answers.append(dict(id=callback_query_id, text=text))

    async def get_business_connection(self, cid):
        return self.business_connections[cid]

    async def set_my_commands(self, commands):
        return True

    async def set_webhook(self, *a, **k):
        return True

    async def get_webhook_info(self):
        return {"url": "https://x.test/telegram/webhook", "pending_update_count": 0}

    def to_chat(self, chat_id=USER):
        return [m for m in self.sent if m["bcid"] and m["chat_id"] == chat_id]

    def to_owner(self):
        return [m for m in self.sent if not m["bcid"] and m["chat_id"] == OWNER]


class FakeAI:
    def __init__(self):
        self.calls: list = []
        self.queue: list[Decision] = []
        self.default = Decision(should_reply=True, reply="Привет! Чем могу помочь?", tone="friendly", relationship="friend")
        self.fail = False

    async def decide(self, inp):
        self.calls.append(inp)
        if self.fail:
            raise RuntimeError("boom")
        dec = self.queue.pop(0) if self.queue else self.default
        return AIResult(decision=dec, input_tokens=10, output_tokens=5, model="fake")


class Harness:
    def __init__(self, svc, tg, ai, engine, settings):
        self.svc, self.tg, self.ai, self.engine, self.settings = svc, tg, ai, engine, settings
        self._uid = itertools.count(1)
        self._mid = itertools.count(1)

    def bc_update(self, *, can_reply=True, is_enabled=True, user_id=OWNER, conn_id=CONN):
        return {"update_id": next(self._uid), "business_connection": {
            "id": conn_id, "user": {"id": user_id, "first_name": "Влад", "is_bot": False}, "user_chat_id": user_id,
            "date": 1, "rights": {"can_reply": can_reply}, "is_enabled": is_enabled}}

    def msg_update(self, text, *, from_id=USER, chat_id=USER, message_id=None, **extra):
        msg = {"message_id": message_id or next(self._mid), "date": 1, "text": text, "business_connection_id": CONN,
               "chat": {"id": chat_id, "type": "private", "first_name": "Аня"},
               "from": {"id": from_id, "first_name": "Аня" if from_id == USER else "Влад", "is_bot": False, "language_code": "ru"}}
        msg.update(extra)
        return {"update_id": next(self._uid), "business_message": msg}

    def cb_update(self, data, *, user_id=USER, message_id=77):
        return {"update_id": next(self._uid), "callback_query": {
            "id": f"cb{next(self._uid)}", "from": {"id": user_id}, "data": data,
            "message": {"message_id": message_id, "chat": {"id": user_id, "type": "private"}, "business_connection_id": CONN}}}

    def owner_cmd(self, text):
        return {"update_id": next(self._uid), "message": {"message_id": 1, "text": text, "chat": {"id": OWNER, "type": "private"}, "from": {"id": OWNER}}}

    async def send(self, update):
        r = await self.svc.router.handle(update)
        await self.engine.drain()
        return r

    async def chat(self) -> Chat:
        async with self.svc.ctx.sm() as s:
            return (await s.execute(select(Chat).where(Chat.tg_chat_id == USER))).scalar_one()

    async def allow_chat(self):
        async with self.svc.ctx.sm() as s:
            (await s.execute(select(Chat).where(Chat.tg_chat_id == USER))).scalar_one().enabled = True
            await s.commit()

    async def grant_consent(self):
        """Full happy path up to granted consent."""
        await self.send(self.msg_update("привет"))
        await self.allow_chat()
        await self.send(self.msg_update("привет"))  # -> notice
        tok = self.tg.to_chat()[-1]["markup"]["inline_keyboard"][0][0]["callback_data"]
        await self.send(self.cb_update(tok))
        assert (await self.chat()).consent_status == "granted"


@pytest.fixture
def settings():
    return Settings(
        _env_file=None,
        telegram_bot_token="123:TESTTOKEN",
        webhook_secret="whsecret",
        encryption_key=Fernet.generate_key().decode(),
        session_secret="x" * 32,
        owner_telegram_id=OWNER,
        public_base_url="https://ghost.test",
        database_url="sqlite+aiosqlite:///:memory:",
        anthropic_api_key="sk-test",
        humanize_delay=False,
    )


@pytest_asyncio.fixture
async def h(settings):
    tg, ai = FakeTG(), FakeAI()
    engine = make_engine(settings.database_url)
    svc, _ = build_services(settings, tg=tg, ai=ai, engine=engine)
    await init_db(engine)
    harness = Harness(svc, tg, ai, svc.engine, settings)
    await svc.ctx.state.set("paused", False)
    await svc.ctx.state.set("debounce_seconds", 0)
    await harness.send(harness.bc_update())
    tg.sent.clear()
    yield harness
    await svc.engine.shutdown()
    await engine.dispose()


@pytest_asyncio.fixture
async def client(settings):
    import httpx

    tg, ai = FakeTG(), FakeAI()
    engine = make_engine(settings.database_url)
    app = create_app(settings, tg=tg, ai=ai, db_engine=engine, run_background=False)
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="https://ghost.test") as c:
            c.app = app  # type: ignore[attr-defined]
            c.tg = tg  # type: ignore[attr-defined]
            yield c
