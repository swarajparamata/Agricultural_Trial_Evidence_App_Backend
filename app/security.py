"""Password hashing (Argon2) and signed login tokens (JWT)."""

from __future__ import annotations

import secrets
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import jwt
from pwdlib import PasswordHash

from .config import DATA_DIR, Settings

ALGORITHM = "HS256"
_password_hash = PasswordHash.recommended()


def hash_password(password: str) -> str:
    return _password_hash.hash(password)


def verify_password(password: str, hashed: str) -> bool:
    try:
        return _password_hash.verify(password, hashed)
    except Exception:  # malformed hash
        return False


def resolve_secret_key(settings: Settings, key_file: Path = DATA_DIR / ".secret_key") -> str:
    """The configured key, or a random one generated once and kept on disk so logins survive restarts."""
    if settings.secret_key:
        return settings.secret_key
    if key_file.exists():
        return key_file.read_text(encoding="utf-8").strip()
    key_file.parent.mkdir(parents=True, exist_ok=True)
    key = secrets.token_urlsafe(48)
    key_file.write_text(key, encoding="utf-8")
    return key


def create_access_token(*, user_id: int, role: str, secret: str, expires_minutes: int) -> tuple[str, datetime]:
    expires_at = datetime.now(timezone.utc) + timedelta(minutes=expires_minutes)
    payload = {"sub": str(user_id), "role": role, "exp": expires_at, "iat": datetime.now(timezone.utc)}
    return jwt.encode(payload, secret, algorithm=ALGORITHM), expires_at


def decode_access_token(token: str, secret: str) -> dict[str, Any] | None:
    try:
        return jwt.decode(token, secret, algorithms=[ALGORITHM])
    except jwt.PyJWTError:
        return None
