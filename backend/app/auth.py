from __future__ import annotations

import hashlib
import hmac
import os

COOKIE = "desk"
PASSWORD = os.environ.get("DESK_PASSWORD", "Abcd1234!")


def expected_token() -> str:
    return hashlib.sha256(f"georgechin|{PASSWORD}".encode("utf-8")).hexdigest()


def password_ok(given: str) -> bool:
    return hmac.compare_digest(given or "", PASSWORD)


def cookie_ok(value: str | None) -> bool:
    if not value:
        return False
    return hmac.compare_digest(value, expected_token())
