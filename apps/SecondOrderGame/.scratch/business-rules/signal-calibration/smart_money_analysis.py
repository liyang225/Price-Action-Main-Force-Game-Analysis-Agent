"""Pick the smart-money "positive" threshold from its z-score distribution.

The current rule is `positive = value > MA`, which is a direction state (~50%).
This script measures, for a grid of z-score cutoffs, the resulting trigger
frequency and forward-return predictive power, so the production logic can be
changed to "significantly above recent average" with a data-backed threshold.

Run after fetch_bars.py populates the cache.
"""

from __future__ import annotations

import argparse
import json
import pickle
import statistics
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from src.data.models import Bar  # noqa: E402

HERE = Path(__file__).resolve().parent
CACHE_DIR = HERE / "cache"
MA_LENGTH = 10
FORWARD_WINDOWS = (2, 4, 8)
Z_CUTOFFS = (0.0, 0.5, 1.0, 1.5, 2.0)


def smart_money(bar: Bar) -> float | None:
    rng = bar.high - bar.low
    if rng <= 0:
        return None
    return (bar.close - bar.open) / rng * bar.volume


def analyze() -> dict[str, Any]:
    buckets: dict[float, dict[str, list[float]]] = {
        z: {f"w{w}": [] for w in FORWARD_WINDOWS} for z in Z_CUTOFFS
    }
    baseline: dict[int, list[float]] = {w: [] for w in FORWARD_WINDOWS}
    total = 0

    for cache_file in sorted(CACHE_DIR.glob("*.pkl")):
        with open(cache_file, "rb") as handle:
            bars = pickle.load(handle)
        bars = sorted(bars, key=lambda b: b.time_key)
        values: list[float | None] = [smart_money(bar) for bar in bars]
        closes = [bar.close for bar in bars]
        size = len(bars)

        fwd: dict[int, list[float | None]] = {w: [None] * size for w in FORWARD_WINDOWS}
        for index in range(size):
            for window in FORWARD_WINDOWS:
                target = index + window
                if target < size and closes[index] > 0:
                    fwd[window][index] = closes[target] / closes[index] - 1.0

        for index in range(MA_LENGTH, size):
            window_values = values[index - MA_LENGTH + 1 : index + 1]
            if any(v is None for v in window_values):
                continue
            numeric = [float(v) for v in window_values]  # type: ignore[arg-type]
            value = numeric[-1]
            average = statistics.fmean(numeric)
            std = statistics.pstdev(numeric)
            z = (value - average) / std if std > 0 else 0.0
            total += 1
            for cutoff in Z_CUTOFFS:
                triggered = value > average and z > cutoff
                for window in FORWARD_WINDOWS:
                    ret = fwd[window][index]
                    if ret is None:
                        continue
                    if triggered:
                        buckets[cutoff][f"w{window}"].append(ret)
                    elif cutoff == Z_CUTOFFS[0]:
                        baseline[window].append(ret)

    report: dict[str, Any] = {"windows_evaluated": total}
    for cutoff in Z_CUTOFFS:
        entry: dict[str, Any] = {}
        for window in FORWARD_WINDOWS:
            trig = buckets[cutoff][f"w{window}"]
            base = baseline[window]
            trig_mean = statistics.fmean(trig) if trig else None
            base_mean = statistics.fmean(base) if base else None
            entry[f"w{window}"] = {
                "trigger_count": len(trig),
                "trigger_freq_pct": round(100.0 * len(trig) / max(1, total), 3),
                "trigger_mean_ret": round(trig_mean, 5) if trig_mean is not None else None,
                "baseline_mean_ret": round(base_mean, 5) if base_mean is not None else None,
                "ret_gap": round(trig_mean - base_mean, 5)
                if trig_mean is not None and base_mean is not None
                else None,
            }
        report[f"z_cutoff_{cutoff}"] = entry
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=HERE / "smart_money_analysis.json")
    args = parser.parse_args()
    result = analyze()
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    print(f"\nwrote {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
