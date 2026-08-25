"""Signal-frequency calibration against real A-share K_120M data.

Reads the 60-stock pool from the behavior-study manifest, pulls K_120M
history from Futu OpenD, replays GameSignalCalculator bar-by-bar, and
reports trigger frequencies plus smart-money ratio percentiles so the
"frequency imbalance" can be quantified before any parameter change.

Run (system Python 3.14, per project convention):
    & 'C:\\Users\\bai\\AppData\\Local\\Programs\\Python\\Python314\\python.exe' calibrate_signals.py --limit 3
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
from collections import Counter
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

import time  # noqa: E402

from src.data.futu_client import FutuMarketDataSource  # noqa: E402
from src.data.rate_limiter import RateLimitExceeded, RateLimiter  # noqa: E402
from src.probability.models import DecisionPoint  # noqa: E402
from src.signals.game_signals import (  # noqa: E402
    GameSignalCalculator,
    GameSignalRequest,
    GameSignalSnapshot,
    InsufficientData,
    load_game_signal_config,
)

START = "2020-01-01"
END = "2026-08-15"
MANIFEST = (
    ROOT
    / ".scratch"
    / "business-rules"
    / "experiments"
    / "output"
    / "daily_ohlcv_manifest.json"
)


def load_stock_codes() -> list[str]:
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    return [
        str(item["code"]).upper()
        for item in manifest["measurements"]
        if item.get("asset_type") == "stock"
    ]


def percent(numerator: int, denominator: int) -> float | None:
    if denominator == 0:
        return None
    return round(100.0 * numerator / denominator, 3)


class Accumulator:
    """Aggregate counters and distributions across the whole pool."""

    def __init__(self) -> None:
        self.bars = 0
        self.insufficient = 0
        self.smart_ratio: list[float] = []
        self.nash_position: Counter[str] = Counter()
        self.flags: Counter[str] = Counter()
        self.flag_names = (
            "smart_money_positive",
            "herd_volume_spike",
            "herd_buying",
            "herd_selling",
            "institutional_volume",
            "accumulation",
            "distribution",
            "trap_volume_spike",
            "trap_up",
            "trap_down",
            "contrarian_buy",
            "contrarian_sell",
            "momentum_buy",
            "momentum_sell",
            "nash_reversion_buy",
            "nash_reversion_sell",
        )

    def add(self, snap: GameSignalSnapshot) -> None:
        self.bars += 1
        self.nash_position[snap.nash.position] += 1
        flags = {
            "smart_money_positive": snap.smart_money.positive,
            "herd_volume_spike": snap.herd.volume_spike,
            "herd_buying": snap.herd.buying,
            "herd_selling": snap.herd.selling,
            "institutional_volume": snap.institutional_flow.institutional_volume,
            "accumulation": snap.institutional_flow.accumulation,
            "distribution": snap.institutional_flow.distribution,
            "trap_volume_spike": snap.liquidity_trap.volume_spike,
            "trap_up": snap.liquidity_trap.upper,
            "trap_down": snap.liquidity_trap.lower,
            "contrarian_buy": snap.features.contrarian_buy,
            "contrarian_sell": snap.features.contrarian_sell,
            "momentum_buy": snap.features.momentum_buy,
            "momentum_sell": snap.features.momentum_sell,
            "nash_reversion_buy": snap.features.nash_reversion_buy,
            "nash_reversion_sell": snap.features.nash_reversion_sell,
        }
        for name, value in flags.items():
            if value:
                self.flags[name] += 1
        ma = snap.smart_money.moving_average
        if ma > 0:
            self.smart_ratio.append(snap.smart_money.value / ma)

    def summary(self) -> dict[str, Any]:
        frequencies = {
            name: percent(self.flags.get(name, 0), self.bars)
            for name in self.flag_names
        }
        ratios = sorted(self.smart_ratio)
        percentiles: dict[str, float | None] = {}
        if ratios:
            for p in (5, 10, 25, 50, 75, 90, 95):
                percentiles[f"p{p}"] = round(
                    ratios[min(len(ratios) - 1, int(len(ratios) * p / 100))], 4
                )
            percentiles["min"] = round(ratios[0], 4)
            percentiles["max"] = round(ratios[-1], 4)
        else:
            percentiles = {f"p{p}": None for p in (5, 10, 25, 50, 75, 90, 95)}
        nash_total = sum(self.nash_position.values()) or 1
        return {
            "bars_evaluated": self.bars,
            "insufficient_bars": self.insufficient,
            "flag_frequency_pct": frequencies,
            "smart_money_ratio_percentiles": percentiles,
            "nash_position_pct": {
                key: round(100.0 * count / nash_total, 3)
                for key, count in sorted(self.nash_position.items())
            },
        }


def fetch_kline_with_retry(
    source: FutuMarketDataSource, code: str, *, max_attempts: int = 8
) -> list[Any]:
    """Pull K_120M history, waiting out OpenD rate-limit windows."""
    for attempt in range(max_attempts):
        try:
            return list(source.get_kline(code, "K_120M", START, END))
        except RateLimitExceeded as exc:
            wait = exc.retry_after + 0.5
            print(f"    rate-limited, waiting {wait:.1f}s")
            time.sleep(wait)
    raise RuntimeError(f"gave up after {max_attempts} rate-limit attempts")


def process_stock(
    source: FutuMarketDataSource,
    calc: GameSignalCalculator,
    code: str,
    acc: Accumulator,
) -> dict[str, Any]:
    bars = fetch_kline_with_retry(source, code)
    if not bars:
        return {"code": code, "bars": 0, "status": "empty"}
    request = GameSignalRequest(
        code=code, start=START, end=END, decision_point=DecisionPoint.CLOSE
    )
    points = calc.calculate_series_from_bars(request, bars, display_bars=len(bars))
    stock_bars = 0
    for point in points:
        snap = point.signal
        if snap is None:
            acc.insufficient += 1
            continue
        acc.add(snap)
        stock_bars += 1
    return {"code": code, "bars": stock_bars, "status": "ok"}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--limit", type=int, default=0, help="only process the first N stocks")
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT / ".scratch" / "business-rules" / "signal-calibration" / "frequency_report.json",
    )
    args = parser.parse_args()

    config = load_game_signal_config(ROOT / "config" / "signals.yaml")
    source = FutuMarketDataSource(
        host="127.0.0.1",
        port=11111,
        rate_limiter=RateLimiter(max_calls=30, window_seconds=30.0),
    )
    calc = GameSignalCalculator(source, config)
    acc = Accumulator()

    codes = load_stock_codes()
    if args.limit:
        codes = codes[: args.limit]
    print(f"pool size: {len(codes)}")

    per_stock: list[dict[str, Any]] = []
    try:
        for index, code in enumerate(codes, 1):
            try:
                info = process_stock(source, calc, code, acc)
            except Exception as exc:  # noqa: BLE001
                info = {"code": code, "bars": 0, "status": "error", "error": str(exc)}
            per_stock.append(info)
            print(f"[{index}/{len(codes)}] {code} bars={info.get('bars')} {info.get('status')}")
    finally:
        source.close()

    summary = acc.summary()
    report = {
        "start": START,
        "end": END,
        "pool_size": len(codes),
        "config_version": config.version,
        "summary": summary,
        "per_stock": per_stock,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print("\n=== FREQUENCY REPORT ===")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    print(f"\nwrote {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
