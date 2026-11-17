import logging
import sys


def setup_logging(level: str = "INFO") -> None:
    """Plain technical logs. Message text is never logged anywhere in this codebase.

    httpx/httpcore are silenced to WARNING because their INFO lines contain the full
    request URL, and Telegram's URL embeds the bot token.
    """
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        stream=sys.stdout,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        force=True,
    )
    for noisy in ("httpx", "httpcore", "anthropic", "uvicorn.access"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
