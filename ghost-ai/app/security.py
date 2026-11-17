"""Encryption at rest, signed tokens, constant-time compare helpers."""
import base64
import hashlib
import hmac
import json
import secrets
import time
from typing import Any

from cryptography.fernet import Fernet


class Crypto:
    """Fernet (AES-128-CBC + HMAC) wrapper for sensitive columns."""

    def __init__(self, key: str):
        self._f = Fernet(key.encode())

    def enc(self, text: str) -> str:
        return self._f.encrypt(text.encode()).decode()

    def dec(self, token: str) -> str:
        return self._f.decrypt(token.encode()).decode()

    def enc_json(self, obj: Any) -> str:
        return self.enc(json.dumps(obj, ensure_ascii=False))

    def dec_json(self, token: str) -> Any:
        return json.loads(self.dec(token))


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def _mac(secret: str, purpose: str, body: str) -> str:
    key = hashlib.sha256(f"{purpose}:{secret}".encode()).digest()
    return _b64(hmac.new(key, body.encode(), hashlib.sha256).digest())


def sign(secret: str, purpose: str, payload: dict, ttl: int) -> str:
    body = _b64(json.dumps({**payload, "exp": int(time.time()) + ttl}).encode())
    return f"{body}.{_mac(secret, purpose, body)}"


def unsign(secret: str, purpose: str, token: str | None) -> dict | None:
    if not token or "." not in token:
        return None
    body, sig = token.rsplit(".", 1)
    if not hmac.compare_digest(sig, _mac(secret, purpose, body)):
        return None
    try:
        data = json.loads(_unb64(body))
    except Exception:
        return None
    if not isinstance(data, dict) or data.get("exp", 0) < time.time():
        return None
    return data


def new_token(nbytes: int = 9) -> str:
    return secrets.token_urlsafe(nbytes)


def safe_equal(a: str, b: str) -> bool:
    return hmac.compare_digest(a.encode(), b.encode())
