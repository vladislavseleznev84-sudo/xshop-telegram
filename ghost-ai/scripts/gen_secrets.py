"""Print fresh secrets for .env (run on the server, keep the output private)."""
import secrets

from cryptography.fernet import Fernet

print("WEBHOOK_SECRET=" + secrets.token_urlsafe(32))
print("SESSION_SECRET=" + secrets.token_urlsafe(48))
print("ENCRYPTION_KEY=" + Fernet.generate_key().decode())
print("POSTGRES_PASSWORD=" + secrets.token_urlsafe(18))
