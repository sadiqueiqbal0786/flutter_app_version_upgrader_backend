"""Optional HMAC-SHA256 response signing.

Header layout:
    X-Signature-Timestamp: <unix seconds>
    X-Signature: sha256=<hex hmac(secret, "<timestamp>.<body>")>

Clients verify with the same secret via the plugin's ``responseValidator``.
"""

from __future__ import annotations

import hashlib
import hmac
import time


def sign(secret: str, body: bytes, timestamp: int | None = None) -> tuple[str, str]:
    ts = str(timestamp if timestamp is not None else int(time.time()))
    digest = hmac.new(secret.encode(), ts.encode() + b"." + body, hashlib.sha256).hexdigest()
    return ts, f"sha256={digest}"


def verify(secret: str, body: bytes, timestamp: str, signature: str, max_skew_seconds: int = 300) -> bool:
    try:
        ts = int(timestamp)
    except ValueError:
        return False
    if abs(time.time() - ts) > max_skew_seconds:
        return False
    _, expected = sign(secret, body, ts)
    return hmac.compare_digest(expected, signature)
