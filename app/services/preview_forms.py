"""Security helpers for public preview-site forms."""

import hashlib
import hmac
import time
from collections import defaultdict, deque
from typing import Optional

from app.config import settings

_WINDOW_SECONDS = 60
_MAX_REQUESTS_PER_WINDOW = 10
_requests: dict[str, deque[float]] = defaultdict(deque)


def form_token(build_id: int) -> str:
    """Create a build-scoped token without exposing the admin credential."""
    return hmac.new(
        settings.admin_token.encode(),
        f"preview-form:{build_id}".encode(),
        hashlib.sha256,
    ).hexdigest()


def valid_form_token(build_id: int, token: str) -> bool:
    return hmac.compare_digest(form_token(build_id), token)


def rate_limit_exceeded(client_key: str, now: Optional[float] = None) -> bool:
    """Small single-process abuse guard; reverse-proxy limits remain recommended."""
    current = now if now is not None else time.monotonic()
    bucket = _requests[client_key]
    while bucket and current - bucket[0] >= _WINDOW_SECONDS:
        bucket.popleft()
    if len(bucket) >= _MAX_REQUESTS_PER_WINDOW:
        return True
    bucket.append(current)
    return False
