"""Thin Telegram Bot API client. Only documented methods are used:
getMe, setWebhook, deleteWebhook, getWebhookInfo, setMyCommands, sendMessage,
sendChatAction, editMessageText, answerCallbackQuery, getBusinessConnection.
"""
import asyncio
from typing import Any

import httpx

ALLOWED_UPDATES = [
    "message",
    "callback_query",
    "business_connection",
    "business_message",
    "edited_business_message",
    "deleted_business_messages",
]


class TelegramError(Exception):
    def __init__(self, method: str, description: str, code: int | None = None, retry_after: int | None = None):
        super().__init__(f"{method}: {description}")
        self.method = method
        self.description = description
        self.code = code
        self.retry_after = retry_after


class TelegramClient:
    def __init__(self, token: str, *, http: httpx.AsyncClient | None = None, timeout: float = 20.0):
        self._token = token
        self._http = http or httpx.AsyncClient(timeout=timeout)
        self._owns_http = http is None

    async def aclose(self) -> None:
        if self._owns_http:
            await self._http.aclose()

    async def call(self, method: str, payload: dict[str, Any] | None = None, _retry: bool = True) -> Any:
        url = f"https://api.telegram.org/bot{self._token}/{method}"
        try:
            resp = await self._http.post(url, json={k: v for k, v in (payload or {}).items() if v is not None})
        except httpx.HTTPError as e:
            # never chain: the original exception text contains the URL (= token)
            raise TelegramError(method, f"network error: {type(e).__name__}") from None
        try:
            data = resp.json()
        except ValueError:
            raise TelegramError(method, f"bad response (HTTP {resp.status_code})", resp.status_code) from None
        if data.get("ok"):
            return data["result"]
        params = data.get("parameters") or {}
        retry_after = params.get("retry_after")
        if resp.status_code == 429 and retry_after and retry_after <= 10 and _retry:
            await asyncio.sleep(retry_after)
            return await self.call(method, payload, _retry=False)
        raise TelegramError(method, str(data.get("description", "unknown error")), data.get("error_code"), retry_after)

    # --- bot setup -------------------------------------------------------
    async def get_me(self) -> dict:
        return await self.call("getMe")

    async def set_webhook(self, url: str, secret_token: str, drop_pending_updates: bool = False) -> bool:
        return await self.call(
            "setWebhook",
            {
                "url": url,
                "secret_token": secret_token,
                "allowed_updates": ALLOWED_UPDATES,
                "drop_pending_updates": drop_pending_updates,
                "max_connections": 20,
            },
        )

    async def delete_webhook(self) -> bool:
        return await self.call("deleteWebhook")

    async def get_webhook_info(self) -> dict:
        return await self.call("getWebhookInfo")

    async def set_my_commands(self, commands: list[dict]) -> bool:
        return await self.call("setMyCommands", {"commands": commands})

    # --- messaging -------------------------------------------------------
    async def send_message(
        self,
        chat_id: int,
        text: str,
        *,
        business_connection_id: str | None = None,
        reply_markup: dict | None = None,
        disable_preview: bool = True,
    ) -> dict:
        return await self.call(
            "sendMessage",
            {
                "chat_id": chat_id,
                "text": text,
                "business_connection_id": business_connection_id,
                "reply_markup": reply_markup,
                "link_preview_options": {"is_disabled": True} if disable_preview else None,
            },
        )

    async def send_chat_action(self, chat_id: int, action: str = "typing", *, business_connection_id: str | None = None) -> bool:
        return await self.call(
            "sendChatAction",
            {"chat_id": chat_id, "action": action, "business_connection_id": business_connection_id},
        )

    async def edit_message_text(
        self,
        chat_id: int,
        message_id: int,
        text: str,
        *,
        business_connection_id: str | None = None,
        reply_markup: dict | None = None,
    ) -> Any:
        return await self.call(
            "editMessageText",
            {
                "chat_id": chat_id,
                "message_id": message_id,
                "text": text,
                "business_connection_id": business_connection_id,
                "reply_markup": reply_markup,
            },
        )

    async def answer_callback_query(self, callback_query_id: str, text: str | None = None) -> bool:
        return await self.call("answerCallbackQuery", {"callback_query_id": callback_query_id, "text": text})

    async def get_business_connection(self, business_connection_id: str) -> dict:
        return await self.call("getBusinessConnection", {"business_connection_id": business_connection_id})
