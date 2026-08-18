"""Detect non-retryable API provider quota / billing failures in model output."""
from __future__ import annotations

PROVIDER_QUOTA_USER_MESSAGE = "OpenClaw 积分不足，请充值或更换 API"

_QUOTA_MARKERS: tuple[str, ...] = (
    "402",
    "积分已用完",
    "积分不足",
    "错误码: 402",
    "错误码:402",
    "insufficient quota",
    "quota exceeded",
    "payment required",
    "out of credits",
)


def is_provider_quota_exhausted(text: str | None) -> bool:
    """True when *text* looks like an OpenClaw / gateway 402 quota response."""
    raw = (text or "").strip()
    if not raw:
        return False
    lower = raw.lower()
    if "402" in raw and ("积分" in raw or "quota" in lower or "credit" in lower):
        return True
    return any(marker.lower() in lower for marker in _QUOTA_MARKERS)
