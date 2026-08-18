"""Probe MT5 server clock vs Windows local clock (for forming-bar countdown bug).

Run: python tools/probe_mt5_clock_skew.py [SYMBOL] [TIMEFRAME]
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from pa_agent.data.bar_close_wait import current_forming_ts, seconds_until_bar_closes
from pa_agent.data.mt5 import MT5Source
from pa_agent.util.timefmt import now_local_ms


def main() -> int:
    symbol = (sys.argv[1] if len(sys.argv) > 1 else "XAUUSD").strip()
    timeframe = (sys.argv[2] if len(sys.argv) > 2 else "5m").strip()

    src = MT5Source()
    try:
        src.connect()
        src.subscribe(symbol, timeframe)
    except Exception as exc:
        print(f"MT5 connect/subscribe failed: {exc}")
        print("Cannot run live probe — terminal may be closed.")
        return 1

    try:
        import MetaTrader5 as mt5  # type: ignore[import]

        tick = mt5.symbol_info_tick(symbol)
        local_ms = now_local_ms()
        server_ms = None
        if tick is not None:
            if getattr(tick, "time_msc", None):
                server_ms = int(tick.time_msc)
            elif getattr(tick, "time", None):
                server_ms = int(tick.time) * 1000

        bars = src.latest_snapshot(5)
        forming_ts = current_forming_ts(bars)

        skew_s = None
        if server_ms is not None:
            skew_s = (server_ms - local_ms) / 1000.0

        rem_local = None
        rem_server = None
        if forming_ts is not None:
            rem_local = seconds_until_bar_closes(
                forming_ts, timeframe, now_ms=local_ms
            )
            if server_ms is not None:
                rem_server = seconds_until_bar_closes(
                    forming_ts, timeframe, now_ms=server_ms
                )

        print(f"symbol={symbol} timeframe={timeframe}")
        print(f"local_now_ms={local_ms}")
        print(f"server_now_ms={server_ms}")
        print(f"skew_server_minus_local_sec={skew_s:+.1f}" if skew_s is not None else "skew=unknown")
        print(f"forming_ts_open_ms={forming_ts}")
        print(f"countdown_using_local_now_sec={rem_local}")
        print(f"countdown_using_server_now_sec={rem_server}")
        if rem_local is not None and rem_server is not None:
            delta = rem_local - rem_server
            print(f"countdown_error_if_using_local_sec={delta:+d}")
            if abs(delta) > 300:
                print("VERDICT: BUG LIKELY — local vs server clock skew inflates countdown")
                return 2
            print("VERDICT: countdown OK (skew small or clocks aligned)")
        return 0
    finally:
        src.disconnect()


if __name__ == "__main__":
    raise SystemExit(main())
