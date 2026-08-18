"""One-shot palette migration: map legacy hex/rgba values to the new tokens palette.

Run from repo root:  python tools/_palette_migrate.py
"""
import io
import pathlib
import re

MAP = {
    # surfaces
    "#0d1117": "#0C0E11", "#0d0f12": "#0C0E11", "#05070b": "#0C0E11",
    "#161b22": "#12151A", "#15181e": "#12151A", "#16181d": "#12151A",
    "#1a1d24": "#181C22", "#181b22": "#151920",
    "#21262d": "#22272F", "#1e232d": "#1A1F27", "#1c2128": "#1A1F27",
    "#1f2937": "#22272F", "#252a34": "#22272F", "#222731": "#1A1F27",
    "#2a303c": "#22272F", "#30363d": "#22272F", "#2b303b": "#22272F",
    "#333842": "#333A45", "#374151": "#333A45", "#484f58": "#333A45",
    "#394150": "#333A45", "#264f78": "#4A7EBB",
    # text
    "#e6edf3": "#E8ECF1", "#f1f5f9": "#E8ECF1", "#f0f3f8": "#E8ECF1",
    "#c9d1d9": "#E8ECF1", "#9ca3af": "#9AA5B1", "#94a3b8": "#9AA5B1",
    "#8b949e": "#9AA5B1", "#6e7681": "#646E7A", "#64748b": "#646E7A",
    "#475569": "#646E7A", "#6b7280": "#646E7A",
    # blues / accent family
    "#3b82f6": "#4A7EBB", "#2563eb": "#4A7EBB", "#1f6feb": "#4A7EBB",
    "#38bdf8": "#4A7EBB", "#58a6ff": "#4A7EBB", "#4a90d9": "#4A7EBB",
    "#60a5fa": "#5B8CC9", "#93c5fd": "#5B8CC9", "#79c0ff": "#7FA6CF",
    "#7dd3fc": "#7FA6CF", "#357abd": "#5B8CC9", "#2a5f9e": "#4A7EBB",
    "#2dd4bf": "#7FA6CF", "#5eead4": "#7FA6CF",
    # violet -> muted slate
    "#a371f7": "#8FA3B8",
    # greens (semantic success / positive)
    "#10b981": "#00C087", "#22c55e": "#00C087", "#3fb950": "#00C087",
    "#238636": "#00C087", "#15803d": "#00C087", "#16a34a": "#00C087",
    "#2EBD85": "#00C087", "#86efac": "#00C087",
    # reds (semantic danger)
    "#ef4444": "#FF5353", "#f85149": "#FF5353", "#da3633": "#FF5353",
    "#cc0000": "#FF5353", "#fca5a5": "#FF5353", "#EF4F4F": "#FF5353",
    # ambers
    "#f59e0b": "#C0913C", "#e6b800": "#C0913C", "#ffcf33": "#C0913C",
    "#d29922": "#C0913C", "#fbbf24": "#CDA756", "#fde047": "#CDA756",
    "#fb923c": "#C07A52",
}
RGBA_MAP = [
    (r"rgba\(\s*59\s*,\s*130\s*,\s*246\s*,", "rgba(74,126,187,"),
    (r"rgba\(\s*56\s*,\s*189\s*,\s*248\s*,", "rgba(74,126,187,"),
    (r"rgba\(\s*239\s*,\s*68\s*,\s*68\s*,", "rgba(255,83,83,"),
    (r"rgba\(\s*34\s*,\s*197\s*,\s*94\s*,", "rgba(0,192,135,"),
    (r"rgba\(\s*245\s*,\s*158\s*,\s*11\s*,", "rgba(192,145,60,"),
    (r"rgba\(\s*45\s*,\s*212\s*,\s*191\s*,", "rgba(74,126,187,"),
    (r"rgba\(\s*34\s*,\s*231\s*,\s*255\s*,", "rgba(74,126,187,"),
    (r"rgba\(\s*88\s*,\s*166\s*,\s*255\s*,", "rgba(74,126,187,"),
]

HEX_RE = re.compile(r"#[0-9a-fA-F]{6}\b")


def main() -> None:
    changed = []
    for path in pathlib.Path("pa_agent/gui").rglob("*.py"):
        if path.name == "tokens.py":
            continue
        src = io.open(path, encoding="utf-8").read()
        out = HEX_RE.sub(lambda m: MAP.get(m.group(0).lower(), m.group(0)), src)
        for pat, rep in RGBA_MAP:
            out = re.sub(pat, rep, out)
        if out != src:
            io.open(path, "w", encoding="utf-8").write(out)
            changed.append(str(path))
    for c in changed:
        print(c)
    print(f"--- {len(changed)} files updated")


if __name__ == "__main__":
    main()
