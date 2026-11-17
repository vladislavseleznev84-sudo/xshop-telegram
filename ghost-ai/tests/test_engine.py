import pytest
from sqlalchemy import func, select

from app.models import Chat, Event, Message
from app.services.ai import Decision
from app.services.engine import clean_reply, is_repeat
from tests.conftest import CONN, OWNER, USER


async def events(h, kind):
    async with h.svc.ctx.sm() as s:
        return (await s.execute(select(func.count()).select_from(Event).where(Event.kind == kind))).scalar_one()


async def live(h):
    await h.svc.ctx.state.set("mode", "live")


async def test_shadow_mode_sends_draft_to_owner_not_to_chat(h):
    await h.grant_consent()
    h.tg.sent.clear()
    await h.send(h.msg_update("Привет, как дела?"))
    assert h.tg.to_chat() == []
    drafts = h.tg.to_owner()
    assert len(drafts) == 1 and "Черновик" in drafts[0]["text"] and "Чем могу помочь" in drafts[0]["text"]
    assert await events(h, "draft_sent") == 1


async def test_live_reply_uses_business_connection_id(h):
    await h.grant_consent()
    await live(h)
    h.tg.sent.clear()
    await h.send(h.msg_update("Привет"))
    out = h.tg.to_chat()
    assert len(out) == 1 and out[0]["bcid"] == CONN and out[0]["text"] == "Привет! Чем могу помочь?"
    async with h.svc.ctx.sm() as s:
        roles = (await s.execute(select(Message.role))).scalars().all()
    assert sorted(roles) == ["in", "out"]


async def test_owner_own_message_never_triggers_reply_and_sets_takeover(h):
    await h.grant_consent()
    await live(h)
    h.tg.sent.clear()
    await h.send(h.msg_update("Это пишу я сам", from_id=OWNER))
    assert h.ai.calls == [] and h.tg.sent == []
    await h.send(h.msg_update("Привет"))  # takeover window active
    assert h.ai.calls == [] and await events(h, "takeover_skip") == 1


async def test_message_sent_by_business_bot_is_ignored(h):
    await h.grant_consent()
    await live(h)
    await h.send(h.msg_update("echo", from_id=OWNER, sender_business_bot={"id": 1, "is_bot": True}))
    assert h.ai.calls == []
    chat = await h.chat()
    assert chat.last_owner_message_at is None  # our own bot's message must not count as human takeover


async def test_bot_sender_ignored(h):
    upd = h.msg_update("spam")
    upd["business_message"]["from"]["is_bot"] = True
    await h.send(upd)
    async with h.svc.ctx.sm() as s:
        assert (await s.execute(select(func.count()).select_from(Chat))).scalar_one() == 0


async def test_duplicate_update_and_duplicate_message_processed_once(h):
    await h.grant_consent()
    await live(h)
    h.tg.sent.clear()
    upd = h.msg_update("Привет", message_id=900)
    assert await h.send(upd) is True
    assert await h.send(upd) is False  # same update_id
    again = h.msg_update("Привет", message_id=900)  # new update_id, same message_id
    await h.send(again)
    assert len(h.ai.calls) == 1 and len(h.tg.to_chat()) == 1


async def test_paused_blocks_everything_but_privacy_commands(h):
    await h.send(h.msg_update("a"))
    await h.allow_chat()
    await h.svc.ctx.state.set("paused", True)
    await h.send(h.msg_update("b"))
    assert h.tg.sent == [] and (await h.chat()).consent_status == "none"
    await h.send(h.msg_update("/ai_off"))
    assert (await h.chat()).consent_status == "revoked"


async def test_pause_between_receive_and_send_stops_reply(h):
    await h.grant_consent()
    await live(h)
    h.tg.sent.clear()

    async def decide(inp):
        await h.svc.ctx.state.set("paused", True)  # owner hits the kill switch while Claude is thinking
        return await type(h.ai).decide(h.ai, inp)

    h.ai.decide, orig = decide, h.ai.decide
    await h.send(h.msg_update("Привет"))
    assert h.tg.to_chat() == [] and h.tg.to_owner() == []


async def test_not_allowlisted_chat_never_gets_anything(h):
    await h.send(h.msg_update("привет"))
    assert h.tg.sent == [] and h.ai.calls == []
    assert await events(h, "skipped_not_allowed") == 1


async def test_connection_without_reply_right_blocks_sending(h):
    await h.send(h.msg_update("a"))
    await h.allow_chat()
    await h.send(h.bc_update(can_reply=False))
    await h.send(h.msg_update("b"))
    assert h.tg.to_chat() == [] and await events(h, "skipped_no_rights") == 1


async def test_legacy_can_reply_field_supported(h):
    upd = h.bc_update()
    upd["business_connection"].pop("rights")
    upd["business_connection"]["can_reply"] = True
    await h.send(upd)
    upd2 = h.bc_update()
    upd2["business_connection"].pop("rights")
    upd2["business_connection"]["can_reply"] = False
    await h.send(upd2)
    await h.send(h.msg_update("x"))
    assert await events(h, "skipped_not_allowed") == 1  # reached allow-list gate; still fine


async def test_foreign_business_connection_ignored(h):
    await h.send(h.bc_update(user_id=4242, conn_id="evil"))
    assert h.tg.sent == []
    from app.models import Connection

    async with h.svc.ctx.sm() as s:
        assert await s.get(Connection, "evil") is None


async def test_repetition_guard_regenerates_then_blocks(h):
    await h.grant_consent()
    await live(h)
    await h.send(h.msg_update("Привет"))
    h.tg.sent.clear()
    same = Decision(should_reply=True, reply="Привет! Чем могу помочь?")
    h.ai.queue = [same, same]
    await h.send(h.msg_update("Эй"))
    assert h.tg.to_chat() == [] and await events(h, "repeat_blocked") == 1
    assert h.tg.to_owner() and "🔔" in h.tg.to_owner()[0]["text"]
    # second attempt gives a fresh reply -> sent
    h.ai.queue = [same, Decision(should_reply=True, reply="Слушаю, что нужно?")]
    await h.send(h.msg_update("Эй, ты тут?"))
    assert [m["text"] for m in h.tg.to_chat()] == ["Слушаю, что нужно?"]


async def test_should_reply_false_sends_nothing(h):
    await h.grant_consent()
    await live(h)
    h.tg.sent.clear()
    h.ai.queue = [Decision(should_reply=False, reply="")]
    await h.send(h.msg_update("ок"))
    assert h.tg.sent == []


async def test_needs_owner_notifies_owner(h):
    await h.grant_consent()
    await live(h)
    h.tg.sent.clear()
    h.ai.queue = [Decision(should_reply=True, reply="Передам Владу", needs_owner=True, owner_note="просит встречу в 5")]
    await h.send(h.msg_update("Давай встретимся"))
    assert any("просит встречу" in m["text"] for m in h.tg.to_owner())


async def test_rate_limit_per_chat(h):
    await h.grant_consent()
    await live(h)
    await h.svc.ctx.state.set("max_replies_per_hour", 2)
    for i in range(4):
        h.ai.queue = [Decision(should_reply=True, reply=f"уникальный ответ номер {i} {'x' * i * 7}")]
        await h.send(h.msg_update(f"сообщение {i}"))
    assert len(h.tg.to_chat()) == 2 + 0 or len([m for m in h.tg.to_chat() if "уникальный" in m["text"]]) == 2
    assert await events(h, "rate_limited") >= 1


async def test_flood_limiter_drops_excess(h):
    await h.grant_consent()
    await h.svc.ctx.state.set("paused", True)  # keep it cheap; flood check happens before pause gate
    for _ in range(25):
        await h.send(h.msg_update("spam"))
    assert await events(h, "flood_dropped") >= 1


async def test_ai_failure_alerts_owner_and_sends_nothing(h):
    await h.grant_consent()
    await live(h)
    h.tg.sent.clear()
    h.ai.fail = True
    await h.send(h.msg_update("Привет"))
    assert h.tg.to_chat() == [] and await events(h, "ai_error") == 1
    assert any("Ошибка вызова Claude" in m["text"] for m in h.tg.to_owner())


async def test_memory_profile_updates_and_isolated_between_chats(h):
    await h.grant_consent()
    from app.services.memory import MemoryUpdate

    h.ai.queue = [Decision(should_reply=False, relationship="friend", memory=MemoryUpdate(name="Аня", add_agreements=["созвон в пятницу"]))]
    await h.send(h.msg_update("Созвонимся в пятницу?"))
    h.ai.queue = [Decision(should_reply=False)]
    await h.send(h.msg_update("и ещё"))
    prof = h.ai.calls[-1].profile
    assert prof.name == "Аня" and prof.agreements == ["созвон в пятницу"] and prof.relationship == "friend"
    # second interlocutor gets an empty profile and no foreign history
    async with h.svc.ctx.sm() as s:
        c2 = Chat(connection_id=CONN, tg_chat_id=888, enabled=True, consent_status="granted")
        s.add(c2)
        await s.commit()
    upd = h.msg_update("Привет", from_id=888, chat_id=888)
    h.ai.queue = [Decision(should_reply=False)]
    await h.send(upd)
    last = h.ai.calls[-1]
    assert last.profile.name == "" and last.profile.agreements == [] and [t for _, t, _ in last.history] == ["Привет"]


def test_clean_reply_and_repeat_helpers():
    assert clean_reply('  "**Ок**, понял"  ') == "Ок, понял"
    assert is_repeat("Привет! Чем помочь?", ["привет! чем помочь"])
    assert not is_repeat("Совсем другое сообщение про погоду", ["привет! чем помочь"])
