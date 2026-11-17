"""Owner-only JSON API + magic-link login for the web panel."""
import logging
from datetime import timedelta
from urllib.parse import parse_qs

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from sqlalchemy import func, select

from app.models import Chat, Connection, Event, Usage, utcnow
from app.security import sign, unsign
from app.services.events import consume_token, count_events, log_event
from app.state import CONVERTERS

log = logging.getLogger("ghost.api")
SESSION_COOKIE = "ghost_session"
SESSION_TTL = 14 * 24 * 3600

router = APIRouter()


def _svc(request: Request):
    return request.app.state.svc


async def require_owner(request: Request) -> dict:
    svc = _svc(request)
    data = unsign(svc.ctx.settings.session_secret.get_secret_value(), "session", request.cookies.get(SESSION_COOKIE))
    if not data or data.get("uid") != svc.ctx.settings.owner_telegram_id or not data.get("uid"):
        raise HTTPException(401, "unauthorized")
    if request.method not in ("GET", "HEAD") and request.headers.get("x-requested-with") != "ghost":
        raise HTTPException(403, "csrf")
    return data


# ------------------------------------------------------------------ login
LOGIN_PAGE = """<!doctype html><html lang="ru"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
<title>GHOST AI</title><link rel="stylesheet" href="/static/app.css"></head>
<body class="login"><main class="login-card"><div class="wordmark">GHOST<span>AI</span></div>
<p class="muted">Подтвердите вход в панель</p>
<form method="post" action="/auth/login"><input type="hidden" name="t" value="{token}">
<button class="btn primary" type="submit">Войти</button></form></main></body></html>"""


@router.get("/auth/magic", response_class=HTMLResponse)
async def magic_page(t: str = "") -> HTMLResponse:
    # Intentionally does NOT consume the token: Telegram link previews fetch URLs with GET.
    safe = "".join(ch for ch in t if ch.isalnum() or ch in "-_")
    return HTMLResponse(LOGIN_PAGE.format(token=safe), headers={"Cache-Control": "no-store"})


@router.post("/auth/login")
async def login(request: Request):
    svc = _svc(request)
    body = parse_qs((await request.body()).decode(errors="ignore"))
    token = (body.get("t") or [""])[0]
    if not svc.login_limiter.allow(request.client.host if request.client else "?"):
        raise HTTPException(429, "too many attempts")
    async with svc.ctx.sm() as s:
        row = await consume_token(s, token, "magic")
        await s.commit()
    if row is None:
        raise HTTPException(403, "invalid or expired link")
    owner = svc.ctx.settings.owner_telegram_id
    cookie = sign(svc.ctx.settings.session_secret.get_secret_value(), "session", {"uid": owner}, SESSION_TTL)
    resp = RedirectResponse("/", status_code=303)
    resp.set_cookie(SESSION_COOKIE, cookie, max_age=SESSION_TTL, httponly=True, samesite="strict", secure=svc.ctx.settings.cookie_secure)
    return resp


@router.post("/api/logout")
async def logout(_: dict = Depends(require_owner)):
    resp = JSONResponse({"ok": True})
    resp.delete_cookie(SESSION_COOKIE)
    return resp


# -------------------------------------------------------------------- data
@router.get("/api/overview")
async def overview(request: Request, _: dict = Depends(require_owner)):
    svc = _svc(request)
    st = await svc.ctx.state.all()
    now = utcnow()
    async with svc.ctx.sm() as s:
        total = (await s.execute(select(func.count()).select_from(Chat))).scalar_one()
        allowed = (await s.execute(select(func.count()).select_from(Chat).where(Chat.enabled.is_(True)))).scalar_one()
        granted = (await s.execute(select(func.count()).select_from(Chat).where(Chat.consent_status == "granted"))).scalar_one()
        conns = (await s.execute(select(Connection))).scalars().all()
        day = now - timedelta(hours=24)
        stats = {
            "incoming": await count_events(s, "incoming", day),
            "replies": await count_events(s, "reply_sent", day),
            "drafts": await count_events(s, "draft_sent", day),
            "needs_owner": await count_events(s, "needs_owner", day),
        }
        tok = (await s.execute(select(func.coalesce(func.sum(Usage.input_tokens), 0), func.coalesce(func.sum(Usage.output_tokens), 0)).where(Usage.ts >= day))).one()
        recent = (await s.execute(select(Event).order_by(Event.id.desc()).limit(12))).scalars().all()
    return {
        "paused": st["paused"],
        "mode": st["mode"],
        "claude_configured": bool(svc.ctx.settings.anthropic_api_key.get_secret_value()),
        "model": svc.ctx.settings.claude_model,
        "connections": [{"enabled": c.is_enabled, "can_reply": c.can_reply} for c in conns],
        "chats": {"total": total, "allowed": allowed, "granted": granted},
        "day": stats,
        "tokens": {"input": int(tok[0]), "output": int(tok[1])},
        "recent": [{"ts": e.ts.isoformat(), "kind": e.kind, "chat": e.chat_id, "detail": e.detail} for e in recent],
    }


@router.get("/api/chats")
async def chats(request: Request, _: dict = Depends(require_owner)):
    svc = _svc(request)
    async with svc.ctx.sm() as s:
        rows = (await s.execute(select(Chat).order_by(Chat.last_incoming_at.desc().nullslast()).limit(200))).scalars().all()
    return [
        {
            "id": c.id,
            "name": svc.ctx.crypto.dec(c.name_enc) if c.name_enc else f"#{c.tg_chat_id}",
            "enabled": c.enabled,
            "consent": c.consent_status,
            "consent_at": c.consent_at.isoformat() if c.consent_at else None,
            "last_incoming_at": c.last_incoming_at.isoformat() if c.last_incoming_at else None,
        }
        for c in rows
    ]


@router.post("/api/chats/{chat_id}/enabled")
async def set_enabled(chat_id: int, request: Request, _: dict = Depends(require_owner)):
    svc = _svc(request)
    body = await request.json()
    async with svc.ctx.sm() as s:
        chat = await s.get(Chat, chat_id)
        if not chat:
            raise HTTPException(404, "no such chat")
        chat.enabled = bool(body.get("enabled"))
        await log_event(s, "chat_enabled" if chat.enabled else "chat_disabled", chat_id)
        await s.commit()
    return {"ok": True, "enabled": chat.enabled}


@router.post("/api/chats/{chat_id}/forget")
async def forget(chat_id: int, request: Request, _: dict = Depends(require_owner)):
    svc = _svc(request)
    async with svc.ctx.sm() as s:
        if not await s.get(Chat, chat_id):
            raise HTTPException(404, "no such chat")
        await svc.memory.delete_all(s, chat_id)
        await log_event(s, "memory_forgotten", chat_id, "by_owner")
        await s.commit()
    return {"ok": True}


@router.post("/api/pause")
async def pause(request: Request, _: dict = Depends(require_owner)):
    await _svc(request).ctx.state.set("paused", True)
    return {"ok": True, "paused": True}


@router.post("/api/resume")
async def resume(request: Request, _: dict = Depends(require_owner)):
    await _svc(request).ctx.state.set("paused", False)
    return {"ok": True, "paused": False}


@router.get("/api/settings")
async def get_settings(request: Request, _: dict = Depends(require_owner)):
    st = await _svc(request).ctx.state.all()
    st.pop("paused", None)
    return st


@router.post("/api/settings")
async def set_settings(request: Request, _: dict = Depends(require_owner)):
    svc = _svc(request)
    body = await request.json()
    if not isinstance(body, dict):
        raise HTTPException(400, "object expected")
    errors = {}
    for k, v in body.items():
        if k == "paused" or k not in CONVERTERS:
            errors[k] = "unknown setting"
            continue
        try:
            await svc.ctx.state.set(k, v)
        except (ValueError, TypeError) as e:
            errors[k] = str(e)
    if errors:
        return JSONResponse({"ok": False, "errors": errors}, status_code=422)
    return {"ok": True}
