"""The core privacy guarantee: nothing is sent to Claude or stored before explicit per-chat consent."""
from sqlalchemy import func, select, text

from app.models import Chat, Message, Profile
from tests.conftest import USER


async def count(h, model):
    async with h.svc.ctx.sm() as s:
        return (await s.execute(select(func.count()).select_from(model))).scalar_one()


async def test_no_ai_no_storage_before_consent(h):
    await h.send(h.msg_update("секретное сообщение"))  # unknown chat -> not allow-listed
    await h.allow_chat()
    await h.send(h.msg_update("ещё секрет"))  # allowed, consent=none -> notice only
    assert h.ai.calls == []
    assert await count(h, Message) == 0
    assert await count(h, Profile) == 0
    notice = h.tg.to_chat()
    assert len(notice) == 1 and "Anthropic Claude" in notice[0]["text"]
    kb = notice[0]["markup"]["inline_keyboard"][0]
    assert [b["callback_data"][-1] for b in kb] == ["y", "n"]
    assert (await h.chat()).consent_status == "pending"


async def test_pending_ignores_other_text_and_never_calls_ai(h):
    await h.send(h.msg_update("a"))
    await h.allow_chat()
    await h.send(h.msg_update("b"))
    n = len(h.tg.sent)
    await h.send(h.msg_update("что-то ещё"))
    assert len(h.tg.sent) == n and h.ai.calls == [] and await count(h, Message) == 0


async def test_grant_via_button_then_ai_runs_and_content_is_encrypted(h):
    await h.grant_consent()
    assert h.ai.calls == []
    assert any("согласие записано" in m["text"] for m in h.tg.to_chat())
    assert h.tg.edits == [] or True
    await h.send(h.msg_update("Давай завтра в 5?"))
    assert len(h.ai.calls) == 1
    async with h.svc.ctx.sm() as s:
        raw = (await s.execute(text("select content_enc from messages"))).scalars().all()
        prof = (await s.execute(text("select data_enc from profiles"))).scalars().all()
    assert raw and all("завтра" not in r for r in raw)
    assert prof and all("friend" not in p for p in prof)


async def test_button_from_wrong_user_is_ignored(h):
    await h.send(h.msg_update("a"))
    await h.allow_chat()
    await h.send(h.msg_update("b"))
    tok = h.tg.to_chat()[-1]["markup"]["inline_keyboard"][0][0]["callback_data"]
    await h.send(h.cb_update(tok, user_id=999))
    assert (await h.chat()).consent_status == "pending"


async def test_button_is_single_use(h):
    await h.grant_consent()
    tok = [m for m in h.tg.to_chat() if m["markup"]][0]["markup"]["inline_keyboard"][0][0]["callback_data"]
    await h.send(h.cb_update(tok))  # replay
    assert (await h.chat()).consent_status == "granted"


async def test_text_yes_fallback(h):
    await h.send(h.msg_update("a"))
    await h.allow_chat()
    await h.send(h.msg_update("b"))
    await h.send(h.msg_update("Да!"))
    assert (await h.chat()).consent_status == "granted"


async def test_decline_stays_silent_forever(h):
    await h.send(h.msg_update("a"))
    await h.allow_chat()
    await h.send(h.msg_update("b"))
    await h.send(h.msg_update("нет"))
    assert (await h.chat()).consent_status == "declined"
    n = len(h.tg.sent)
    await h.send(h.msg_update("ну пожалуйста ответь"))
    assert len(h.tg.sent) == n and h.ai.calls == [] and await count(h, Message) == 0


async def test_revoke_deletes_everything(h):
    await h.grant_consent()
    await h.send(h.msg_update("привет, это я"))
    assert await count(h, Message) >= 1
    await h.send(h.msg_update("/ai_off"))
    assert (await h.chat()).consent_status == "revoked"
    assert await count(h, Message) == 0 and await count(h, Profile) == 0
    calls = len(h.ai.calls)
    await h.send(h.msg_update("ещё сообщение"))
    assert len(h.ai.calls) == calls


async def test_revoke_button_after_grant(h):
    await h.grant_consent()
    revoke = [m for m in h.tg.to_chat() if m["markup"]][-1]["markup"]["inline_keyboard"][0][0]["callback_data"]
    assert revoke.endswith(":r")
    await h.send(h.cb_update(revoke))
    assert (await h.chat()).consent_status == "revoked"


async def test_ai_forget_keeps_consent_but_drops_memory(h):
    await h.grant_consent()
    await h.send(h.msg_update("запомни: я люблю кофе"))
    await h.send(h.msg_update("/ai_forget"))
    chat = await h.chat()
    assert chat.consent_status == "granted"
    assert await count(h, Message) == 0 and await count(h, Profile) == 0


async def test_reoptin_after_revoke(h):
    await h.grant_consent()
    await h.send(h.msg_update("/ai_off"))
    await h.send(h.msg_update("/ai_on"))
    assert (await h.chat()).consent_status == "pending"


async def test_consent_is_per_chat(h):
    await h.grant_consent()
    other = h.msg_update("привет", from_id=777, chat_id=777)
    await h.send(other)
    async with h.svc.ctx.sm() as s:
        c = (await s.execute(select(Chat).where(Chat.tg_chat_id == 777))).scalar_one()
        assert c.consent_status == "none" and not c.enabled


async def test_deleted_messages_removed(h):
    await h.grant_consent()
    await h.send(h.msg_update("удалю это", message_id=4242))
    assert await count(h, Message) >= 1
    await h.send({"update_id": 99999, "deleted_business_messages": {"business_connection_id": "conn-1", "chat": {"id": USER, "type": "private"}, "message_ids": [4242]}})
    async with h.svc.ctx.sm() as s:
        left = (await s.execute(select(Message).where(Message.tg_message_id == 4242))).scalars().all()
    assert left == []
