"""Retell webhook signature verification.

Retell signs each webhook with the ``X-Retell-Signature`` header, formatted as
``v={timestamp_ms},d={hex_digest}`` where::

    hex_digest = HMAC-SHA256(key = RETELL_WEBHOOK_KEY, msg = raw_body + timestamp)

``+`` is string concatenation of the raw request body and the timestamp string,
and the key is the Retell API key that carries the "webhook" badge. Verification
MUST run against the raw request bytes (re-serializing parsed JSON would change
whitespace/ordering and break the digest). We also reject timestamps older than a
tolerance window to blunt replay attacks.

Verified against docs.retellai.com/features/secure-webhook.
"""

from __future__ import annotations

import hashlib
import hmac
import re
import time

_SIG_RE = re.compile(r"^v=(\d+),d=(.+)$")
DEFAULT_TOLERANCE_MS = 5 * 60 * 1000  # 5 minutes


def build_signature(raw_body: bytes, key: str, timestamp_ms: int) -> str:
    """Build a valid X-Retell-Signature header value (used by tests and clients)."""
    message = raw_body + str(timestamp_ms).encode("utf-8")
    digest = hmac.new(key.encode("utf-8"), message, hashlib.sha256).hexdigest()
    return f"v={timestamp_ms},d={digest}"


def verify_signature(
    raw_body: bytes,
    signature_header: str | None,
    key: str,
    *,
    now_ms: int | None = None,
    tolerance_ms: int = DEFAULT_TOLERANCE_MS,
) -> bool:
    """Return True iff the signature header is present, fresh, and authentic."""
    if not signature_header:
        return False
    match = _SIG_RE.match(signature_header.strip())
    if not match:
        return False
    timestamp_str, provided = match.group(1), match.group(2)

    now = now_ms if now_ms is not None else int(time.time() * 1000)
    try:
        timestamp = int(timestamp_str)
    except ValueError:
        return False
    if abs(now - timestamp) > tolerance_ms:
        return False

    message = raw_body + timestamp_str.encode("utf-8")
    expected = hmac.new(key.encode("utf-8"), message, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, provided)
