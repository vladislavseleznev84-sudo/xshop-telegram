"""Sanity-check the bot token and webhook state (prints no secrets)."""
import asyncio

from app.config import Settings
from app.telegram.client import TelegramClient, TelegramError


async def main() -> None:
    s = Settings()  # type: ignore[call-arg]
    tg = TelegramClient(s.telegram_bot_token.get_secret_value())
    try:
        me = await tg.get_me()
        print(f"bot: @{me.get('username')}  business_mode(can_connect_to_business)={me.get('can_connect_to_business')}")
        wh = await tg.get_webhook_info()
        print(f"webhook url set: {bool(wh.get('url'))}, pending: {wh.get('pending_update_count')}, last_error: {wh.get('last_error_message')}")
    except TelegramError as e:
        print("Telegram error:", e.description)
    finally:
        await tg.aclose()


asyncio.run(main())
