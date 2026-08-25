from __future__ import annotations

import argparse
import sys
from datetime import date
from pathlib import Path
from typing import Callable, Sequence

from .config import load_research_config
from .depth import measure_history_depth
from .errors import ResearchHarnessError
from .futu_provider import FutuHistoryProvider
from .models import ResearchConfig
from .provider import HistoryProvider
from .replay import replay


def main(
    argv: Sequence[str] | None = None,
    *,
    provider_factory: Callable[[ResearchConfig], HistoryProvider] | None = None,
) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    try:
        config = load_research_config(args.config)
        if args.command == "replay":
            provider = provider_factory(config) if provider_factory else _make_provider(config)
            try:
                report = replay(config, provider)
            finally:
                _close_provider(provider)
            output_format = args.format or config.output.format
            content = (
                report.to_json(include_matches=args.include_matches or config.output.include_matches)
                if output_format == "json"
                else report.to_markdown(
                    include_matches=args.include_matches or config.output.include_matches
                )
            )
            _write_or_print(content, Path(args.output) if args.output else config.output.path)
            return 0
        if args.command == "depth":
            provider = provider_factory(config) if provider_factory else _make_provider(config)
            try:
                report = measure_history_depth(
                    config,
                    provider,
                    periods=tuple(args.period) if args.period else ("day", "120m"),
                    start=args.start,
                    end=args.end,
                )
            finally:
                _close_provider(provider)
            output_format = args.format or config.output.format
            content = report.to_json() if output_format == "json" else report.to_markdown()
            _write_or_print(content, Path(args.output) if args.output else config.output.path)
            return 0
        raise ResearchHarnessError(f"unsupported command: {args.command}")
    except (ResearchHarnessError, OSError, ValueError) as exc:
        print(f"research_harness: {exc}", file=sys.stderr)
        return 2


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="research_harness",
        description="Replay configurable candidate labels against historical OHLCV data.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    replay_parser = subparsers.add_parser("replay", help="fetch history and replay labels")
    replay_parser.add_argument("--config", required=True, type=Path)
    replay_parser.add_argument("--format", choices=("json", "markdown"))
    replay_parser.add_argument("--output", type=Path)
    replay_parser.add_argument("--include-matches", action="store_true")
    depth_parser = subparsers.add_parser("depth", help="measure history depth for configured instruments")
    depth_parser.add_argument("--config", required=True, type=Path)
    depth_parser.add_argument("--format", choices=("json", "markdown"))
    depth_parser.add_argument("--output", type=Path)
    depth_parser.add_argument("--period", action="append", choices=("day", "120m"))
    depth_parser.add_argument("--start", type=_iso_date)
    depth_parser.add_argument("--end", type=_iso_date)
    return parser


def _iso_date(value: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("date must use YYYY-MM-DD format") from exc


def _make_provider(config: ResearchConfig) -> HistoryProvider:
    provider_name = config.data.provider
    if provider_name != "futu":
        raise ResearchHarnessError(
            f"provider {provider_name!r} has no CLI adapter; inject a provider for offline replay"
        )
    options = dict(config.data.provider_options)
    allowed = {"host", "port", "max_count", "page_delay_seconds"}
    unknown = set(options) - allowed
    if unknown:
        raise ResearchHarnessError(
            f"data.provider_options contains unsupported Futu option(s): {', '.join(sorted(unknown))}"
        )
    return FutuHistoryProvider(**options)


def _close_provider(provider: object) -> None:
    close = getattr(provider, "close", None)
    if callable(close):
        close()


def _write_or_print(content: str, path: Path | None) -> None:
    if path is None:
        print(content, end="")
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8", newline="\n")
