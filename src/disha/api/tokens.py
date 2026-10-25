"""Signed session tokens for per-user auth.

Format: base64url(json payload) + "." + hex(hmac_sha256(payload, key))
Payload: {"uid": ..., "tid": ..., "adm": bool, "exp": unix_ts}

Key is DISHA_UI_TOKEN — already a high-entropy server secret. Stateless: no
sessions table, tokens are valid until exp (30 days) or key rotation.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
from dataclasses import dataclass
from uuid import UUID

from disha.config import get_settings

_TTL_SECONDS = 30 * 24 * 3600


@dataclass(frozen=True)
class TokenPayload:
    user_id: UUID
    tenant_id: UUID
    is_admin: bool


def _key() -> bytes:
    return get_settings().disha_ui_token.get_secret_value().encode()


def issue_token(user_id: UUID, tenant_id: UUID, is_admin: bool) -> str:
    payload = json.dumps(
        {
            "uid": str(user_id),
            "tid": str(tenant_id),
            "adm": is_admin,
            "exp": int(time.time()) + _TTL_SECONDS,
        },
        separators=(",", ":"),
    ).encode()
    b64 = base64.urlsafe_b64encode(payload).decode().rstrip("=")
    sig = hmac.new(_key(), b64.encode(), hashlib.sha256).hexdigest()
    return f"{b64}.{sig}"


def verify_token(token: str) -> TokenPayload | None:
    try:
        b64, sig = token.rsplit(".", 1)
        expected = hmac.new(_key(), b64.encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(sig, expected):
            return None
        padded = b64 + "=" * (-len(b64) % 4)
        data = json.loads(base64.urlsafe_b64decode(padded))
        if int(data["exp"]) < time.time():
            return None
        return TokenPayload(
            user_id=UUID(data["uid"]),
            tenant_id=UUID(data["tid"]),
            is_admin=bool(data.get("adm", False)),
        )
    except Exception:  # noqa: BLE001 — any malformed token is just invalid
        return None
