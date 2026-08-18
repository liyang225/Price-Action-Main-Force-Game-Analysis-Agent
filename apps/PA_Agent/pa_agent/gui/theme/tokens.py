"""Design tokens for the PA Agent dark quantitative-terminal theme.

Single source of truth for every visual value in ``pa_agent.gui``.

Design principles (binding for all consumers):
  * Restrained, calm, professional — data is the only protagonist.
  * No large saturated fills, no gradients, no glow, no heavy shadows.
  * Emphasis comes from position, weight and thin accent bars — never
    from enlarged type or shouting CTAs.
  * Market colours follow the CN convention: 红涨绿跌 (up = red, down = green).
"""
from __future__ import annotations

# ---------------------------------------------------------------------------
# Surfaces — neutral cool grey steps, separation via 1px hairlines
# ---------------------------------------------------------------------------
BG = "#0C0E11"          # application base
SURFACE_1 = "#12151A"   # panels / sidebars
SURFACE_2 = "#11161D"   # cards / elevated blocks
SURFACE_3 = "#22272F"   # hover / pressed steps
SURFACE_4 = "#333A45"   # strong borders, dividers in focus

BORDER_SOFT = "#22272F"                  # default 1px hairline
BORDER_STRONG = "#333A45"

# ---------------------------------------------------------------------------
# Text — three reading levels, all >= 4.5:1 on their surfaces
# ---------------------------------------------------------------------------
FG = "#E8ECF1"          # primary reading
FG_2 = "#9AA5B1"        # secondary reading / captions
FG_3 = "#646E7A"        # placeholders, disabled-adjacent hints

# ---------------------------------------------------------------------------
# Accent — one restrained steel blue, used sparingly
# (interactive affordance, active states, informational links)
# ---------------------------------------------------------------------------
ACCENT = "#4A7EBB"
ACCENT_HOVER = "#5B8CC9"
ACCENT_SOFT = "rgba(74,126,187,0.14)"    # selection wash
ACCENT_BORDER = "rgba(74,126,187,0.40)"

# ---------------------------------------------------------------------------
# Semantic — system state only, never used for market direction
# ---------------------------------------------------------------------------
SUCCESS = "#00D084"
DANGER = "#FF4757"
WARNING = "#C0913C"
INFO = ACCENT

# ---------------------------------------------------------------------------
# Market direction — CN convention: up = red, down = green.
# ---------------------------------------------------------------------------
MKT_UP = "#FF4757"
MKT_DOWN = "#00D084"
MKT_FLAT = FG_2


# ---------------------------------------------------------------------------
# Chart (pyqtgraph) — candles, grid, studies, decision overlays
# ---------------------------------------------------------------------------
CHART_BG = BG
CHART_GRID = "#1A1F27"
CHART_UP = "#E03F4D"
CHART_DOWN = "#00B775"
CHART_UP_OUTLINE = "#BF3340"
CHART_DOWN_OUTLINE = "#00955F"
CHART_LINE = "#B8933E"   # EMA — muted ochre, below candle salience
CHART_LINE_2 = "#8FA3B8"
CHART_LINE_3 = "#C07A52"
CHART_CROSSHAIR = (154, 165, 177, 180)

# Decision price lines keep their learned role mapping
# (Entry = blue, TP = green, SL = red) at the same restraint level.
LINE_ENTRY = ACCENT
LINE_TP = MKT_DOWN
LINE_TP2 = "#5FA57F"
LINE_SL = MKT_UP

# ---------------------------------------------------------------------------
# Pills — status tags, low-alpha fills only
# ---------------------------------------------------------------------------
PILL_GREEN_TEXT = "#00D084"
PILL_GREEN_BORDER = "rgba(0,208,132,0.35)"
PILL_GREEN_BG = "rgba(0,208,132,0.10)"

PILL_AMBER_TEXT = "#CDA756"
PILL_AMBER_BORDER = "rgba(192,145,60,0.35)"
PILL_AMBER_BG = "rgba(192,145,60,0.10)"

PILL_BLUE_TEXT = "#7FA6CF"
PILL_BLUE_BORDER = "rgba(74,126,187,0.35)"
PILL_BLUE_BG = "rgba(74,126,187,0.10)"

PILL_RED_TEXT = "#FF4757"
PILL_RED_BORDER = "rgba(255,71,87,0.35)"
PILL_RED_BG = "rgba(255,71,87,0.10)"

PILL_CYAN_TEXT = "#8FA8B8"
PILL_CYAN_BORDER = "rgba(143,168,184,0.30)"
PILL_CYAN_BG = "rgba(143,168,184,0.08)"

# ---------------------------------------------------------------------------
# Typography — one UI family, one mono family for data/measurement
# ---------------------------------------------------------------------------
FONT_UI = '"Segoe UI", "Microsoft YaHei UI", sans-serif'
FONT_MONO = '"JetBrains Mono", "Cascadia Mono", "Consolas", monospace'

SIZE_CAPTION = 11    # captions, hints
SIZE_SECTION = 12    # section headers (weight 600, FG_2)
SIZE_BODY = 13       # default reading
SIZE_TITLE = 15      # panel titles
SIZE_CONCLUSION = 16 # the one conclusion line — weight, not size, does the work
SIZE_QUOTE = 22      # latest price on the market strip (the data protagonist)

WEIGHT_REGULAR = 400
WEIGHT_MEDIUM = 500
WEIGHT_SEMIBOLD = 600

# ---------------------------------------------------------------------------
# Layout
# ---------------------------------------------------------------------------
RADIUS = 4
SPACING = 8

# ---------------------------------------------------------------------------
# Legacy aliases (backward-compatible names mapped to the palette above)
# ---------------------------------------------------------------------------
BG_BASE = BG
BG_PANEL = SURFACE_1
BG_ELEVATED = SURFACE_2
BG_REASONING = SURFACE_2
BG_INPUT = SURFACE_2

BORDER = SURFACE_4
BORDER_MUTED = SURFACE_3

TEXT_PRIMARY = FG
TEXT_SECONDARY = FG_2
TEXT_MUTED = FG_3

ACCENT_PRIMARY = ACCENT
ACCENT_REASONING = ACCENT
ACCENT_SUCCESS = SUCCESS
ACCENT_WARNING = WARNING
ACCENT_DANGER = DANGER

TRADE_LONG = MKT_UP      # CN convention: 做多 = 红
TRADE_SHORT = MKT_DOWN   # CN convention: 做空 = 绿
TRADE_NEUTRAL = FG_2

TOKEN_YELLOW = WARNING
TOKEN_RED = DANGER
