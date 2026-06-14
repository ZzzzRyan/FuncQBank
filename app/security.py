"""Security helpers: password hashing (argon2), CSRF tokens, login rate limiting."""
from __future__ import annotations

import secrets
import time
from collections import defaultdict

from argon2 import PasswordHasher
from argon2.exceptions import Argon2Error
from fastapi import Request

_ph = PasswordHasher()


def hash_password(password: str) -> str:
    return _ph.hash(password)


def verify_password(password_hash: str, password: str) -> bool:
    try:
        return _ph.verify(password_hash, password)
    except (Argon2Error, Exception):  # noqa: BLE001 — any failure means "no match"
        return False


# --- CSRF (double-submit token stored in the signed session) ---
def get_csrf_token(request: Request) -> str:
    token = request.session.get("csrf")
    if not token:
        token = secrets.token_urlsafe(32)
        request.session["csrf"] = token
    return token


def verify_csrf(request: Request, submitted: str | None) -> bool:
    expected = request.session.get("csrf", "")
    return bool(submitted) and bool(expected) and secrets.compare_digest(expected, submitted)


# --- very small in-memory fixed-window rate limiter (per key) ---
_hits: dict[str, list[float]] = defaultdict(list)


def rate_limited(key: str, limit: int, window_seconds: int) -> bool:
    now = time.time()
    bucket = _hits[key]
    cutoff = now - window_seconds
    bucket[:] = [t for t in bucket if t > cutoff]
    if len(bucket) >= limit:
        return True
    bucket.append(now)
    return False
