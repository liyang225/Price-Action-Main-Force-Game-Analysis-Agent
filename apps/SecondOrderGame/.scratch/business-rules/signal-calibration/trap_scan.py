"""Scan liquidity_trap.retracement_min to see if a stronger "recover" flips
trap_down back to its documented bullish (mean-reversion) semantics.

Replays only the liquidity-trap branch of game_signals.py against cached bars,
so it runs in seconds instead of the full calculator's minutes.
"""

from __future__ import annotations

import json
import pickle
import statistics
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

import yaml  # noqa: E402

from src.signals.game_signals import (  # noqa: E402
    _nearest_psychological_level,
    _passes_optional_ratio,
    _within_psychological_level,
)

HERE = Path(__file__).resolve().parent
CACHE_DIR = HERE / "cache"
FORWARD_WINDOWS = (2, 4, 8)
RETRACE_VALUES: tuple[float | None, ...] = (None, 0.01, 0.02, 0.03)


def load_config() -> dict[str, Any]:
    with open(ROOT / "config" / "signals.yaml", encoding="utf-8") as handle:
        return yaml.safe_load(handle)


def scan() -> dict[str, Any]:
    cfg = load_config()
    herd = cfg["herd"]
    liq = cfg["liquidity_trap"]
    vol_ma_len = int(herd["volume_ma_length"])
    vol_mult = float(liq["volume_multiple"])
    lookback = int(liq["lookback"])
    offset = int(liq["break_reference_offset"])
    brackets = tuple(liq["psych_level_brackets"])
    prox = float(liq["psych_proximity"])
    breakout_margin = liq["breakout_margin_min"]

    buckets: dict[Any, dict[str, dict[int, list[float]]]] = {
        r: {
            "down": {w: [] for w in FORWARD_WINDOWS},
            "up": {w: [] for w in FORWARD_WINDOWS},
        }
        for r in RETRACE_VALUES
    }
    total_bars = 0

    for cache_file in sorted(CACHE_DIR.glob("*.pkl")):
        with open(cache_file, "rb") as handle:
            bars = pickle.load(handle)
        bars = sorted(bars, key=lambda b: b.time_key)
        size = len(bars)
        if size < lookback + offset + max(FORWARD_WINDOWS):
            continue

        volumes = [bar.volume for bar in bars]
        closes = [bar.close for bar in bars]
        fwd: dict[int, list[float | None]] = {w: [None] * size for w in FORWARD_WINDOWS}
        for i in range(size):
            for w in FORWARD_WINDOWS:
                j = i + w
                if j < size and closes[i] > 0:
                    fwd[w][i] = closes[j] / closes[i] - 1.0

        for i in range(lookback + offset, size):
            current = bars[i]
            if current.high == current.low:
                continue
            ref_start = i - offset - lookback
            ref_end = i - offset
            reference = bars[ref_start:ref_end]
            recent_high = max(bar.high for bar in reference)
            recent_low = min(bar.low for bar in reference)
            vol_ma = statistics.fmean(volumes[i - vol_ma_len + 1 : i + 1])
            vol_spike = current.volume > vol_ma * vol_mult
            upper_psych = _nearest_psychological_level(current.close, current.high, brackets)
            lower_psych = _nearest_psychological_level(current.close, current.low, brackets)
            broke_up = current.high > recent_high
            recovered_up = current.close < recent_high
            broke_down = current.low < recent_low
            recovered_down = current.close > recent_low
            up_psych = _within_psychological_level(current.high, upper_psych, current.close, prox)
            down_psych = _within_psychological_level(current.low, lower_psych, current.close, prox)
            up_break_margin = _passes_optional_ratio(
                (current.high - recent_high) / recent_high, breakout_margin
            )
            down_break_margin = _passes_optional_ratio(
                (recent_low - current.low) / recent_low, breakout_margin
            )
            total_bars += 1

            for r in RETRACE_VALUES:
                up_retrace = _passes_optional_ratio(
                    (current.high - current.close) / current.high, r
                )
                down_retrace = _passes_optional_ratio(
                    (current.close - current.low) / current.low, r
                )
                up_trap = (
                    broke_up and recovered_up and vol_spike and up_psych
                    and up_break_margin and up_retrace
                )
                down_trap = (
                    broke_down and recovered_down and vol_spike and down_psych
                    and down_break_margin and down_retrace
                )
                for w in FORWARD_WINDOWS:
                    ret = fwd[w][i]
                    if ret is None:
                        continue
                    if up_trap:
                        buckets[r]["up"][w].append(ret)
                    if down_trap:
                        buckets[r]["down"][w].append(ret)

    report: dict[str, Any] = {"bars_evaluated": total_bars}
    for r in RETRACE_VALUES:
        entry: dict[str, Any] = {}
        for side in ("down", "up"):
            for w in FORWARD_WINDOWS:
                rets = buckets[r][side][w]
                n = len(rets)
                if n == 0:
                    entry[f"{side}_w{w}"] = {"count": 0}
                    continue
                mean_ret = statistics.fmean(rets)
                if side == "down":
                    acc = 100.0 * sum(1 for x in rets if x > 0) / n
                else:
                    acc = 100.0 * sum(1 for x in rets if x < 0) / n
                entry[f"{side}_w{w}"] = {
                    "count": n,
                    "freq_pct": round(100.0 * n / max(1, total_bars), 3),
                    "mean_ret": round(mean_ret, 5),
                    "direction_acc_pct": round(acc, 2),
                }
        report[f"retracement_min={r}"] = entry
    return report


def main() -> int:
    result = scan()
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
