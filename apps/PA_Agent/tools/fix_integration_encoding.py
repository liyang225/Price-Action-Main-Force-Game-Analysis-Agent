"""One-off repair for corrupted UTF-8 in tests/integration/*.py (ASCII-only source)."""
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1] / "tests" / "integration"

BYTE_SUBS = [
    (b"\xe2\x86?", b"; "),
    (b"\xe2\x80?", b" - "),
]


def fix_line(line: str) -> str:
    if '"reasoning"' in line and line.count('"') % 2 == 1:
        stripped = line.rstrip("\r\n")
        if stripped.endswith(","):
            return stripped[:-1] + '.",\n'
    return line


def fix_bytes(data: bytes) -> str:
    for old, new in BYTE_SUBS:
        data = data.replace(old, new)
    text = data.decode("utf-8", errors="replace").replace("\ufffd", "")
    lines = text.splitlines(keepends=True)
    return "".join(fix_line(ln) for ln in lines)


def main() -> None:
    for path in sorted(ROOT.glob("test_*.py")):
        fixed = fix_bytes(path.read_bytes())
        path.write_text(fixed, encoding="utf-8", newline="\n")
        compile(fixed, str(path), "exec")
        print("fixed", path.name)


if __name__ == "__main__":
    main()
