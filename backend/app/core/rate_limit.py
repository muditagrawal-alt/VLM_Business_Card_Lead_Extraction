"""Per-IP rate limiting.

The public demo URL is reachable by anyone, and each card costs real inference
time on a single box. Limits protect the queue from a stranger rather than
from the intended user.
"""

from __future__ import annotations

import hashlib
from typing import TYPE_CHECKING

from slowapi import Limiter
from slowapi.util import get_remote_address

if TYPE_CHECKING:
    from fastapi import Request

limiter = Limiter(key_func=get_remote_address)


def client_ip_hash(request: Request, *, salt: str = "") -> str | None:
    """A salted hash of the caller's address.

    Enough to correlate abuse and audit a batch, without storing an address
    that would make the database personal data on its own.
    """
    address = get_remote_address(request)
    if not address:
        return None
    return hashlib.sha256(f"{salt}{address}".encode()).hexdigest()
