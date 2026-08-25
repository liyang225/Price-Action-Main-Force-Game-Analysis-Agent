"""Fetch and cache K_120M bars for the 60-stock signal pool.

Caches each stock's bars as a local pickle so parameter iterations never
re-hit Futu OpenD.  Run once.

    & 'C:\\Users\\bai\\AppData\\Local\\Programs\\Python\\Python314\\python.exe' fetch_bars.py
"""

from __future__ import annotations

import json
import pickle
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from src.data.futu_client import FutuMarketDataSource  # noqa: E402
from src.data.rate_limiter import RateLimitExceeded, RateLimiter  # noqa: E402

START = "2020-01-01"
END = "2026-08-15"
MANIFEST = (
    ROOT / ".scratch" / "business-rules" / "experiments" / "output" / "daily_ohlcv_manifest.json"
)
CACHE_DIR = Path(__file__).resolve().parent / "cache"


def load_stock_codes() -> list[str]:
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    return [
        str(item["code"]).upper()
        for item in manifest["measurements"]
        if item.get("asset_type") == "stock"
    ]


def fetch_with_retry(
    source: FutuMarketDataSource, code: str, *, max_attempts: int = 8
) -> list:
    for _ in range(max_attempts):
        try:
            return list(source.get_kline(code, "K_120M", START, END))
        except RateLimitExceeded as exc:
            wait = exc.retry_after + 0.5
            print(f"    rate-limited, waiting {wait:.1f}s")
            time.sleep(wait)
    raise RuntimeError(f"gave up after {max_attempts} attempts")


def main() -> int:
    codes = load_stock_codes()
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    source = FutuMarketDataSource(
        host="127.0.0.1",
        port=11111,
        rate_limiter=RateLimiter(max_calls=30, window_seconds=30.0),
    )
    total = 0
    try:
        for index, code in enumerate(codes, 1):
            path = CACHE_DIR / f"{code.replace('.', '_')}.pkl"
            if path.exists():
                print(f"[{index}/{len(codes)}] {code} cached, skip")
                continue
            try:
                bars = fetch_with_retry(source, code)
                with open(path, "wb") as handle:
                    pickle.dump(bars, handle)
                total += len(bars)
                print(f"[{index}/{len(codes)}] {code} {len(bars)} bars cached")
            except Exception as exc:  # noqa: BLE001
                print(f"[{index}/{len(codes)}] {code} ERROR {exc}")
    finally:
        source.close()
    print(f"\ncached total bars this run: {total}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
