import httpx
import pytest
from sqlalchemy import select

from app.models import Chat, Message
from app.security import Crypto, sign, unsign
from app.services.memory import MemoryUpdate, ProfileData, merge_profile
from app.services.retention import run_retention_once
from app.telegram.client import TelegramClient, TelegramError
from app.texts import parse_answer, pick_lang
from tests.conftest import OWNER, USER


def test_crypto_roundtrip_and_ciphertext_differs():
    from cryptography.fernet import Fernet

    c = Crypto(Fernet.generate_key().decode())
    t = c.enc("секрет")
    assert "секрет" not in t and c.dec(t) == "секрет"
    assert c.dec_json(c.enc_json({"a": [1, "я"]})) == {"a": [1, "я"]}


def test_signed_tokens():
    tok = sign("s3cret", "session", {"uid": 1}, 60)
    assert unsign("s3cret", "session", tok)["uid"] == 1
    assert unsign("other", "session", tok) is None
    assert unsign("s3cret", "magic", tok) is None  # purpose separation
    assert unsign("s3cret", "session", sign("s3cret", "session", {"uid": 1}, -5)) is None
    assert unsign("s3cret", "session", tok[:-2] + "xx") is None
    assert unsign("s3cret", "session", None) is None


def test_parse_answer_and_lang():
    assert parse_answer("Да!") == "y" and parse_answer(" нет. ") == "n" and parse_answer("yes") == "y"
    assert parse_answer("да, но позже") is None
    assert pick_lang("ru") == "ru" and pick_lang("en-US") == "en" and pick_lang(None) == "ru"


def test_merge_profile_dedupes_and_caps():
    p = merge_profile(ProfileData(), MemoryUpdate(name="Аня", add_facts=["Любит чай", "любит чай"]), relationship="friend", tone="happy")
    p = merge_profile(p, MemoryUpdate(add_facts=[f"f{i}" for i in range(100)]), relationship="unknown", tone="neutral")
    assert p.name == "Аня" and p.relationship == "friend" and len(p.facts) == 40 and p.interactions == 2


async def test_retention_purges_old_messages(h):
    await h.grant_consent()
    await h.send(h.msg_update("старое"))
    from datetime import timedelta

    from app.models import utcnow

    async with h.svc.ctx.sm() as s:
        for m in (await s.execute(select(Message))).scalars().all():
            m.created_at = utcnow() - timedelta(days=60)
        await s.commit()
    res = await run_retention_once(h.svc.ctx, h.svc.memory)
    assert res["messages_deleted"] >= 1
    async with h.svc.ctx.sm() as s:
        assert (await s.execute(select(Message))).scalars().all() == []


async def test_telegram_client_payloads_and_error_hides_token():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"], seen["body"] = str(request.url), request.read().decode()
        if request.url.path.endswith("/boom"):
            raise httpx.ConnectError("failed connecting to " + str(request.url))
        if request.url.path.endswith("/getMe"):
            return httpx.Response(401, json={"ok": False, "error_code": 401, "description": "Unauthorized"})
        return httpx.Response(200, json={"ok": True, "result": {"message_id": 5}})

    tg = TelegramClient("111:SECRETTOKEN", http=httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    res = await tg.send_message(42, "hi", business_connection_id="bc1")
    assert res["message_id"] == 5 and seen["url"].endswith("/bot111:SECRETTOKEN/sendMessage")
    import json

    body = json.loads(seen["body"])
    assert body["business_connection_id"] == "bc1" and body["chat_id"] == 42 and "reply_markup" not in body
    with pytest.raises(TelegramError) as e:
        await tg.get_me()
    assert e.value.code == 401
    with pytest.raises(TelegramError) as e2:
        await tg.call("boom")
    assert "SECRETTOKEN" not in str(e2.value) and e2.value.__cause__ is None


# ------------------------------------------------------------- webhook + panel
async def test_webhook_requires_secret_header(client):
    r = await client.post("/telegram/webhook", json={"update_id": 1})
    assert r.status_code == 403
    r = await client.post("/telegram/webhook", json={"update_id": 1}, headers={"X-Telegram-Bot-Api-Secret-Token": "wrong"})
    assert r.status_code == 403
    r = await client.post("/telegram/webhook", json={"update_id": 1}, headers={"X-Telegram-Bot-Api-Secret-Token": "whsecret"})
    assert r.status_code == 200
    r = await client.post("/telegram/webhook", content=b"not json", headers={"X-Telegram-Bot-Api-Secret-Token": "whsecret"})
    assert r.status_code == 400


async def test_health_and_security_headers(client):
    r = await client.get("/healthz")
    assert r.status_code == 200 and r.json() == {"ok": True}
    r = await client.get("/")
    assert "default-src 'self'" in r.headers["content-security-policy"] and r.headers["x-frame-options"] == "DENY"
    assert (await client.get("/openapi.json")).status_code == 404


async def _login(client):
    await client.post("/telegram/webhook", json={"update_id": 10, "message": {"message_id": 1, "text": "/panel", "chat": {"id": OWNER, "type": "private"}, "from": {"id": OWNER}}}, headers={"X-Telegram-Bot-Api-Secret-Token": "whsecret"})
    link = [m for m in client.tg.sent if "/auth/magic?t=" in m["text"]][-1]["text"]
    token = link.split("t=")[1].strip()
    page = await client.get(f"/auth/magic?t={token}")
    assert page.status_code == 200 and 'name="t"' in page.text  # GET must not burn the token
    r = await client.post("/auth/login", content=f"t={token}", headers={"Content-Type": "application/x-www-form-urlencoded"})
    return token, r


async def test_panel_requires_auth_and_magic_link_is_single_use(client):
    assert (await client.get("/api/overview")).status_code == 401
    token, r = await _login(client)
    assert r.status_code == 303 and "ghost_session" in r.headers["set-cookie"] and "HttpOnly" in r.headers["set-cookie"]
    assert "SameSite=strict" in r.headers["set-cookie"] or "samesite=strict" in r.headers["set-cookie"].lower()
    again = await client.post("/auth/login", content=f"t={token}", headers={"Content-Type": "application/x-www-form-urlencoded"})
    assert again.status_code == 403
    assert (await client.get("/api/overview")).status_code == 200


async def test_panel_rejects_forged_cookie_and_missing_csrf_header(client):
    client.cookies.set("ghost_session", "forged.value")
    assert (await client.get("/api/overview")).status_code == 401
    client.cookies.clear()
    await _login(client)
    assert (await client.post("/api/pause")).status_code == 403
    assert (await client.post("/api/pause", headers={"X-Requested-With": "ghost"})).status_code == 200


async def test_panel_kill_switch_settings_and_chat_controls(client):
    await _login(client)
    hd = {"X-Requested-With": "ghost"}
    ov = (await client.get("/api/overview")).json()
    assert ov["paused"] is True  # ships paused
    assert (await client.post("/api/resume", headers=hd)).json()["paused"] is False
    assert (await client.post("/api/pause", headers=hd)).json()["paused"] is True

    r = await client.post("/api/settings", json={"mode": "live", "takeover_minutes": 30}, headers=hd)
    assert r.status_code == 200
    s = (await client.get("/api/settings")).json()
    assert s["mode"] == "live" and s["takeover_minutes"] == 30 and "paused" not in s
    bad = await client.post("/api/settings", json={"mode": "yolo", "retention_days": 0, "paused": False}, headers=hd)
    assert bad.status_code == 422 and set(bad.json()["errors"]) == {"mode", "retention_days", "paused"}

    svc = client.app.state.svc
    async with svc.ctx.sm() as sess:
        c = Chat(connection_id="x", tg_chat_id=5, name_enc=svc.ctx.crypto.enc("<img src=x onerror=alert(1)>"))
        from app.models import Connection

        sess.add(Connection(id="x", owner_user_id=OWNER))
        sess.add(c)
        await sess.commit()
        pk = c.id
    chats = (await client.get("/api/chats")).json()
    assert chats[0]["name"] == "<img src=x onerror=alert(1)>" and chats[0]["enabled"] is False  # raw; UI uses textContent
    assert (await client.post(f"/api/chats/{pk}/enabled", json={"enabled": True}, headers=hd)).json()["enabled"] is True
    assert (await client.post(f"/api/chats/{pk}/forget", headers=hd)).status_code == 200
    assert (await client.post("/api/chats/9999/forget", headers=hd)).status_code == 404


# ---------------------------------------------------------------- admin bot
async def test_admin_commands_owner_only(h):
    await h.send({"update_id": 500, "message": {"message_id": 1, "text": "/pause", "chat": {"id": 9, "type": "private"}, "from": {"id": 9}}})
    assert await h.svc.ctx.state.get("paused") is False and h.tg.sent == []
    await h.send(h.owner_cmd("/pause"))
    assert await h.svc.ctx.state.get("paused") is True
    await h.send(h.owner_cmd("/resume"))
    assert await h.svc.ctx.state.get("paused") is False
    for cmd in ("/status", "/stats", "/chats", "/settings", "/start"):
        h.tg.sent.clear()
        await h.send(h.owner_cmd(cmd))
        assert h.tg.to_owner(), cmd


async def test_admin_set_validates_and_chats_buttons(h):
    await h.send(h.owner_cmd("/set mode live"))
    assert await h.svc.ctx.state.get("mode") == "live"
    h.tg.sent.clear()
    await h.send(h.owner_cmd("/set retention_days 9999"))
    assert "Не принято" in h.tg.to_owner()[-1]["text"] and await h.svc.ctx.state.get("retention_days") == 30
    await h.send(h.owner_cmd("/set paused false"))  # pause only via /resume
    await h.send(h.msg_update("привет"))
    h.tg.sent.clear()
    await h.send(h.owner_cmd("/chats"))
    toggle = [m for m in h.tg.to_owner() if m["markup"]][0]["markup"]["inline_keyboard"][0][0]["callback_data"]
    await h.send({"update_id": 600, "callback_query": {"id": "q", "from": {"id": OWNER}, "data": toggle, "message": {"message_id": 3, "chat": {"id": OWNER}}}})
    assert (await h.chat()).enabled is True
    await h.send({"update_id": 601, "callback_query": {"id": "q2", "from": {"id": 12345}, "data": toggle, "message": {"message_id": 3, "chat": {"id": 12345}}}})
    assert (await h.chat()).enabled is True  # non-owner press ignored (no toggle back)


async def test_start_unconfigured_shows_user_id_only():
    from tests.conftest import FakeAI, FakeTG
    from app.config import Settings
    from app.db import init_db, make_engine
    from app.main import build_services
    from cryptography.fernet import Fernet

    s = Settings(_env_file=None, telegram_bot_token="1:a", webhook_secret="w", encryption_key=Fernet.generate_key().decode(),
                 session_secret="x" * 32, owner_telegram_id=0, database_url="sqlite+aiosqlite:///:memory:")
    tg = FakeTG()
    eng = make_engine(s.database_url)
    svc, _ = build_services(s, tg=tg, ai=FakeAI(), engine=eng)
    await init_db(eng)
    await svc.router.handle({"update_id": 1, "message": {"message_id": 1, "text": "/start", "chat": {"id": 42, "type": "private"}, "from": {"id": 42}}})
    assert "42" in tg.sent[0]["text"]
    await svc.router.handle({"update_id": 2, "message": {"message_id": 1, "text": "/pause", "chat": {"id": 42, "type": "private"}, "from": {"id": 42}}})
    assert len(tg.sent) == 1
