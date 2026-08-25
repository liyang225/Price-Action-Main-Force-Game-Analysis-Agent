"""Evaluate signal predictive power over cached K_120M bars.

Reads cached bars, replays GameSignalCalculator, and measures each signal's
forward-return predictive power (IC, trigger-vs-baseline return gap, and
directional accuracy) so parameters can be tuned against an objective target
instead of by feel.

Supports a YAML override file for fast parameter iteration without touching
production config:

    & 'C:\\Users\\bai\\AppData\\Local\\Programs\\Python\\Python314\\python.exe' evaluate.py --override candidate.yaml
"""

from __future__ import annotations

import argparse
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

from src.data.models import Bar  # noqa: E402
from src.probability.models import DecisionPoint  # noqa: E402
from src.signals.game_signals import (  # noqa: E402
    GameSignalCalculator,
    GameSignalConfig,
    GameSignalRequest,
    GameSignalSnapshot,
)

HERE = Path(__file__).resolve().parent
CACHE_DIR = HERE / "cache"
START = "2020-01-01"
END = "2026-08-15"
FORWARD_WINDOWS = (2, 4, 8)

BULLISH = {
    "smart_money_positive",
    "contrarian_buy",
    "momentum_buy",
    "nash_reversion_buy",
    "trap_down",
    "herd_selling",
}
BEARISH = {
    "contrarian_sell",
    "momentum_sell",
    "nash_reversion_sell",
    "trap_up",
    "herd_buying",
}
ALL_FLAGS = (
    "smart_money_positive",
    "herd_buying",
    "herd_selling",
    "trap_up",
    "trap_down",
    "contrarian_buy",
    "contrarian_sell",
    "momentum_buy",
    "momentum_sell",
    "nash_reversion_buy",
    "nash_reversion_sell",
)


def deep_merge(base: dict, override: dict) -> None:
    for key, value in override.items():
        if key in base and isinstance(base[key], dict) and isinstance(value, dict):
            deep_merge(base[key], value)
        else:
            base[key] = value


def load_config(override_path: Path | None) -> GameSignalConfig:
    with open(ROOT / "config" / "signals.yaml", encoding="utf-8") as handle:
        cfg = yaml.safe_load(handle)
    if override_path is not None:
        with open(override_path, encoding="utf-8") as handle:
            override = yaml.safe_load(handle)
        deep_merge(cfg, override)
    return GameSignalConfig.from_mapping(cfg)


def flag_map(snap: GameSignalSnapshot) -> dict[str, bool]:
    return {
        "smart_money_positive": snap.smart_money.positive,
        "herd_buying": snap.herd.buying,
        "herd_selling": snap.herd.selling,
        "trap_up": snap.liquidity_trap.upper,
        "trap_down": snap.liquidity_trap.lower,
        "contrarian_buy": snap.features.contrarian_buy,
        "contrarian_sell": snap.features.contrarian_sell,
        "momentum_buy": snap.features.momentum_buy,
        "momentum_sell": snap.features.momentum_sell,
        "nash_reversion_buy": snap.features.nash_reversion_buy,
        "nash_reversion_sell": snap.features.nash_reversion_sell,
    }


def forward_returns(bars: list[Bar]) -> dict[int, list[float | None]]:
    closes = [bar.close for bar in bars]
    size = len(closes)
    result: dict[int, list[float | None]] = {w: [None] * size for w in FORWARD_WINDOWS}
    for index in range(size):
        for window in FORWARD_WINDOWS:
            target = index + window
            if target < size and closes[index] > 0:
                result[window][index] = closes[target] / closes[index] - 1.0
    return result


def ic(xs: list[float], ys: list[float]) -> float | None:
    if len(xs) < 3:
        return None
    mean_x = statistics.fmean(xs)
    mean_y = statistics.fmean(ys)
    num = sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, ys))
    den_x = sum((x - mean_x) ** 2 for x in xs)
    den_y = sum((y - mean_y) ** 2 for y in ys)
    if den_x == 0 or den_y == 0:
        return None
    return num / (den_x * den_y) ** 0.5


def directional_accuracy(flag: str, returns: list[float]) -> float:
    if not returns:
        return float("nan")
    if flag in BULLISH:
        return 100.0 * sum(1 for r in returns if r > 0) / len(returns)
    return 100.0 * sum(1 for r in returns if r < 0) / len(returns)


def evaluate(config: GameSignalConfig) -> dict[str, Any]:
    calc = GameSignalCalculator(source=None, config=config)  # type: ignore[arg-type]
    trigger: dict[str, dict[int, list[float]]] = {
        flag: {w: [] for w in FORWARD_WINDOWS} for flag in ALL_FLAGS
    }
    baseline: dict[str, dict[int, list[float]]] = {
        flag: {w: [] for w in FORWARD_WINDOWS} for flag in ALL_FLAGS
    }
    total_bars = 0

    cache_files = sorted(CACHE_DIR.glob("*.pkl"))
    for cache_file in cache_files:
        with open(cache_file, "rb") as handle:
            bars = pickle.load(handle)
        bars = sorted(bars, key=lambda b: b.time_key)
        if len(bars) < 40:
            continue
        fwd = forward_returns(bars)
        request = GameSignalRequest(
            code=cache_file.stem, start=START, end=END, decision_point=DecisionPoint.CLOSE
        )
        points = calc.calculate_series_from_bars(request, bars, display_bars=len(bars))
        for index, point in enumerate(points):
            snap = point.signal
            if snap is None:
                continue
            total_bars += 1
            flags = flag_map(snap)
            for flag in ALL_FLAGS:
                for window in FORWARD_WINDOWS:
                    ret = fwd[window][index]
                    if ret is None:
                        continue
                    if flags[flag]:
                        trigger[flag][window].append(ret)
                    else:
                        baseline[flag][window].append(ret)

    report: dict[str, Any] = {}
    for flag in ALL_FLAGS:
        rows: dict[str, Any] = {}
        for window in FORWARD_WINDOWS:
            trig = trigger[flag][window]
            base = baseline[flag][window]
            trig_mean = statistics.fmean(trig) if trig else None
            base_mean = statistics.fmean(base) if base else None
            gap = (trig_mean - base_mean) if (trig_mean is not None and base_mean is not None) else None
            acc = directional_accuracy(flag, trig)
            rows[f"w{window}"] = {
                "trigger_count": len(trig),
                "trigger_freq_pct": round(100.0 * len(trig) / max(1, len(trig) + len(base)), 3),
                "trigger_mean_ret": round(trig_mean, 5) if trig_mean is not None else None,
                "baseline_mean_ret": round(base_mean, 5) if base_mean is not None else None,
                "ret_gap": round(gap, 5) if gap is not None else None,
                "direction_acc_pct": round(acc, 2) if acc == acc else None,
            }
        report[flag] = rows

    return {"bars_evaluated": total_bars, "config_version": config.version, "signals": report}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--override", type=Path, default=None)
    parser.add_argument(
        "--output",
        type=Path,
        default=HERE / "predictive_report.json",
    )
    args = parser.parse_args()

    config = load_config(args.override)
    result = evaluate(config)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    print(f"\nwrote {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
