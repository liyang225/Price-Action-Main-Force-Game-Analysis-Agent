"""Secret masking utility (standalone, no dependencies)."""


def mask_secret(s: str) -> str:
    """Return s with all but the last 4 characters replaced by '*'.

    If len(s) < 4, return s unchanged (including empty string).
    """
    if len(s) < 4:
        return s
    return "*" * (len(s) - 4) + s[-4:]
