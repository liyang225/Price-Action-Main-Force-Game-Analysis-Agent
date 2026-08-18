"""Offscreen screenshot of the redesigned chart (design verification only)."""
import math
import os
import random
import sys
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication

from pa_agent.data.base import IndicatorBundle, KlineBar, KlineFrame
from pa_agent.gui.chart_widget import ChartWidget
from pa_agent.gui.theme import apply_theme


def make_frame(n: int = 80) -> KlineFrame:
    random.seed(7)
    bars = []
    price = 1.40
    ts = int(time.time()) - n * 3600
    for i in range(n):
        drift = math.sin(i / 9.0) * 0.004
        o = price
        c = o + drift + random.uniform(-0.003, 0.003)
        h = max(o, c) + random.uniform(0.0005, 0.002)
        lo = min(o, c) - random.uniform(0.0005, 0.002)
        bars.append(
            KlineBar(
                seq=i,
                ts_open=ts + i * 3600,
                open=o,
                high=h,
                low=lo,
                close=c,
                volume=1000 + i,
                closed=True,
            )
        )
        price = c
    closes = [b.close for b in bars]
    ema = []
    k = 2 / 21
    e = closes[0]
    for c in closes:
        e = c * k + e * (1 - k)
        ema.append(e)
    return KlineFrame(
        symbol="TEST",
        timeframe="1h",
        bars=tuple(bars),
        indicators=IndicatorBundle(ema20=tuple(ema), atr14=tuple([0.002] * len(bars))),
        snapshot_ts_local_ms=int(time.time() * 1000),
    )


def main() -> int:
    app = QApplication(sys.argv)
    apply_theme(app)
    chart = ChartWidget()
    chart.resize(900, 560)
    chart.set_frame_now(make_frame(), fit_view=True)
    chart.set_decision(
        {
            "order_type": "限价做多",
            "order_direction": "做多",
            "entry_price": 1.4025,
            "take_profit_price": 1.438,
            "take_profit_price_2": 1.452,
            "stop_loss_price": 1.389,
        }
    )
    chart.show()
    app.processEvents()
    chart.grab().save("_design_review/chart.png")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
