"""Time formatting utilities."""
from __future__ import annotations
import time


def now_local_ms() -> int:
    """Return current local time as milliseconds since epoch."""
    return int(time.time() * 1000)
