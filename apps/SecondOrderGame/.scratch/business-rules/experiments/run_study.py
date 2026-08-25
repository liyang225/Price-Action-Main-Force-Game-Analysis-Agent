"""Run tickets 02-05 against the collected daily OHLCV dataset."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import pandas as pd

from behavior_study.pipeline import _jsonable, run_study
from behavior_study.rules import load_rule_config


ROOT = Path(__file__).resolve().parent


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, default=ROOT / "output" / "daily_ohlcv.csv.gz")
    parser.add_argument("--rules", type=Path, default=ROOT / "config" / "behavior_rules.yaml")
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT / "output" / "tickets_02_05_summary.json",
    )
    parser.add_argument("--forward-days", type=int, default=5)
    parser.add_argument("--volume-window", type=int, default=20)
    parser.add_argument("--tail-threshold", type=float, default=0.03)
    parser.add_argument("--extreme-market-threshold", type=float, default=-0.03)
    args = parser.parse_args()

    data = pd.read_csv(args.input, compression="infer", low_memory=False)
    rules = load_rule_config(args.rules)
    study = run_study(
        data,
        rules,
        forward_days=args.forward_days,
        volume_window=args.volume_window,
        negative_threshold=-abs(args.tail_threshold),
        positive_threshold=abs(args.tail_threshold),
        extreme_market_threshold=args.extreme_market_threshold,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(_jsonable(study["summary"]), ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    print(args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
