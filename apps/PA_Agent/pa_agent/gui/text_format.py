"""Shared display-only text formatting for analysis panels."""
from __future__ import annotations

import re


def sentence_line_breaks(value: object) -> str:
    """Put prose sentences on separate lines without splitting decimal prices."""
    text = str(value or "").strip()
    text = re.sub(r"(?<=[。！？；])(?=[^\r\n])", "\n", text)
    return re.sub(r"\.[ \t]+(?=\S)", ".\n", text)


def bullet_point_lines(value: object) -> str:
    """Normalize prose to one display bullet per non-empty sentence line."""
    lines = [
        line.strip(" •·-\t")
        for line in sentence_line_breaks(value).splitlines()
        if line.strip(" •·-\t")
    ]
    return "\n".join(f"• {line}" for line in lines)
