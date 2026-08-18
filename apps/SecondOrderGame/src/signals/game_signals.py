"""Deterministic game-signal observations computed from K_120M OHLCV bars."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import asdict, dataclass
import math
from pathlib import Path
from statistics import fmean, pstdev
from types import MappingProxyType
from typing import Any, TypeAlias

import yaml

from src.config_validator import ConfigError, validate_signals
from src.data.models import Bar
from src.data.protocol import MarketDataSource
from src.probability.models import (
    DecisionPoint,
    InsufficientData,
    ProbabilityType,
)


DATA_SOURCE = "K_120M_ohlcv"
EXPERT_PRIOR_NOTICE = "专家先验推演，非统计估计"


@dataclass(frozen=True, slots=True)
class GameSignalConfig:
    """A validated, immutable view of the frozen signal configuration."""

    version: int
    values: Mapping[str, Any]

    @classmethod
    def from_mapping(cls, values: Mapping[str, Any]) -> GameSignalConfig:
        copied = _copy_mapping(values)
        validate_signals(copied)
        return cls(
            version=copied["version"],
            values=_freeze(copied),
        )


@dataclass(frozen=True, slots=True)
class GameSignalRequest:
    """One stock-level observation request at a program decision point."""

    code: str
    start: str
    end: str
    decision_point: DecisionPoint

    def __post_init__(self) -> None:
        for field_name in ("code", "start", "end"):
            value = getattr(self, field_name)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{field_name} must be a non-empty string")
        if not isinstance(self.decision_point, DecisionPoint):
            raise TypeError("decision_point must be a DecisionPoint")


@dataclass(frozen=True, slots=True)
class NashObservation:
    center: float
    upper: float
    lower: float
    position: str


@dataclass(frozen=True, slots=True)
class HerdObservation:
    rsi: float
    volume_moving_average: float
    momentum: float
    momentum_moving_average: float
    volume_spike: bool
    buying: bool
    selling: bool


@dataclass(frozen=True, slots=True)
class SmartMoneyObservation:
    value: float
    moving_average: float
    positive: bool


@dataclass(frozen=True, slots=True)
class InstitutionalFlowObservation:
    institutional_volume: bool
    accumulation: bool
    distribution: bool


@dataclass(frozen=True, slots=True)
class LiquidityTrapObservation:
    recent_high: float
    recent_low: float
    upper_psychological_level: float
    lower_psychological_level: float
    volume_spike: bool
    upper: bool
    lower: bool


@dataclass(frozen=True, slots=True)
class DerivedFeatureObservation:
    contrarian_buy: bool
    contrarian_sell: bool
    momentum_buy: bool
    momentum_sell: bool
    nash_reversion_buy: bool
    nash_reversion_sell: bool


@dataclass(frozen=True, slots=True)
class GameSignalSnapshot:
    """Available observations for the latest complete K_120M bar."""

    bar_time: str
    config_version: int
    decision_point: DecisionPoint
    nash: NashObservation
    herd: HerdObservation
    smart_money: SmartMoneyObservation
    institutional_flow: InstitutionalFlowObservation
    liquidity_trap: LiquidityTrapObservation
    features: DerivedFeatureObservation
    data_source: str = DATA_SOURCE
    role: str = "observation_feature"
    notice: str = EXPERT_PRIOR_NOTICE

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-safe observation payload for prompts and persistence."""
        payload = asdict(self)
        payload["decision_point"] = self.decision_point.value
        return payload


@dataclass(frozen=True, slots=True)
class GameSignalSeriesPoint:
    """One chart-only signal point aligned to a completed K_120M bar."""

    bar_time: str
    status: str
    signal: GameSignalSnapshot | None = None
    reason: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "bar_time": self.bar_time,
            "status": self.status,
            "signal": self.signal.to_dict() if self.signal is not None else None,
            "reason": self.reason,
        }


GameSignalResult: TypeAlias = GameSignalSnapshot | InsufficientData


class GameSignalCalculator:
    """Read K_120M bars through the market-data seam and compute observations."""

    def __init__(
        self,
        source: MarketDataSource,
        config: GameSignalConfig,
    ) -> None:
        self._source = source
        self._config = config

    def calculate(self, request: GameSignalRequest) -> GameSignalResult:
        if not isinstance(request, GameSignalRequest):
            raise TypeError("request must be a GameSignalRequest")
        bars = self._source.get_kline(
            request.code,
            "K_120M",
            request.start,
            request.end,
        )
        return self._calculate_bars(request, bars)

    def calculate_series(
        self,
        request: GameSignalRequest,
        *,
        display_bars: int = 20,
    ) -> tuple[GameSignalSeriesPoint, ...]:
        """Calculate chart observations bar-by-bar without future leakage."""
        if not isinstance(request, GameSignalRequest):
            raise TypeError("request must be a GameSignalRequest")
        if (
            isinstance(display_bars, bool)
            or not isinstance(display_bars, int)
            or display_bars < 1
        ):
            raise ValueError("display_bars must be a positive integer")
        bars = list(
            self._source.get_kline(
                request.code,
                "K_120M",
                request.start,
                request.end,
            )
        )
        return self.calculate_series_from_bars(
            request,
            bars,
            display_bars=display_bars,
        )

    def calculate_series_from_bars(
        self,
        request: GameSignalRequest,
        bars: list[Bar] | tuple[Bar, ...],
        *,
        display_bars: int = 20,
    ) -> tuple[GameSignalSeriesPoint, ...]:
        """Calculate an already-fetched history for chart rendering."""
        if not isinstance(request, GameSignalRequest):
            raise TypeError("request must be a GameSignalRequest")
        if (
            isinstance(display_bars, bool)
            or not isinstance(display_bars, int)
            or display_bars < 1
        ):
            raise ValueError("display_bars must be a positive integer")
        bars = list(bars)
        start_index = max(0, len(bars) - display_bars)
        points: list[GameSignalSeriesPoint] = []
        for end_index in range(start_index, len(bars)):
            bar_time = bars[end_index].time_key
            result = self._calculate_bars(request, bars[: end_index + 1])
            if isinstance(result, GameSignalSnapshot):
                points.append(
                    GameSignalSeriesPoint(
                        bar_time=bar_time,
                        status="available",
                        signal=result,
                    )
                )
            else:
                points.append(
                    GameSignalSeriesPoint(
                        bar_time=bar_time,
                        status="insufficient_data",
                        reason=result.reason,
                    )
                )
        return tuple(points)

    def _calculate_bars(
        self,
        request: GameSignalRequest,
        bars: list[Bar] | tuple[Bar, ...],
    ) -> GameSignalResult:
        herd_config = self._config.values["herd"]
        smart_config = self._config.values["smart_money"]
        institutional_config = self._config.values["institutional"]
        liquidity_config = self._config.values["liquidity_trap"]
        nash_config = self._config.values["nash"]
        required_bars = max(
            nash_config["period"],
            herd_config["rsi_length"] + 1,
            herd_config["volume_ma_length"],
            herd_config["momentum_lookback"] + herd_config["momentum_ma_length"],
            smart_config["ma_length"],
            institutional_config["ad_ma_length"],
            liquidity_config["lookback"]
            + liquidity_config["break_reference_offset"],
        )
        if len(bars) < required_bars:
            return InsufficientData(
                probability_type=ProbabilityType.BEHAVIOR,
                outcome="game_signal_observations",
                reason=f"{len(bars)} K_120M bars available; {required_bars} required",
                data_source=DATA_SOURCE,
                config_version=self._config.version,
                decision_point=request.decision_point,
            )
        for bar in bars:
            _validate_bar(bar)

        degenerate_config = self._config.values["degenerate_bar"]
        affected_window = max(
            smart_config["ma_length"],
            institutional_config["ad_ma_length"],
        )
        degenerate_bars = [
            bar for bar in bars[-affected_window:] if bar.high == bar.low
        ]
        if (
            degenerate_bars
            and degenerate_config["zero_range_policy"] == "insufficient_data"
        ):
            return InsufficientData(
                probability_type=ProbabilityType.BEHAVIOR,
                outcome="game_signal_observations",
                reason=(
                    "zero-range K_120M bar makes configured smart-money "
                    "and institutional observations unavailable"
                ),
                data_source=DATA_SOURCE,
                config_version=self._config.version,
                decision_point=request.decision_point,
            )

        period = nash_config["period"]
        closes = [bar.close for bar in bars]
        volumes = [bar.volume for bar in bars]
        nash_closes = closes[-period:]
        center = fmean(nash_closes)
        half_width = pstdev(nash_closes) * nash_config["deviation"]

        volume_moving_average = fmean(volumes[-herd_config["volume_ma_length"] :])
        volume_spike = (
            volumes[-1] > volume_moving_average * herd_config["volume_multiple"]
        )
        momentum_values = _momentum_values(
            closes,
            herd_config["momentum_lookback"],
            herd_config["momentum_ma_length"],
        )
        momentum = momentum_values[-1]
        momentum_moving_average = fmean(momentum_values)
        upward_momentum = _passes_optional_deviation(
            momentum - momentum_moving_average,
            herd_config["momentum_deviation_min"],
        )
        downward_momentum = _passes_optional_deviation(
            momentum_moving_average - momentum,
            herd_config["momentum_deviation_min"],
        )
        rsi = _wilder_rsi(closes, herd_config["rsi_length"])
        herd_buying = (
            rsi > herd_config["rsi_overbought"]
            and volume_spike
            and upward_momentum
        )
        herd_selling = (
            rsi < herd_config["rsi_oversold"]
            and volume_spike
            and downward_momentum
        )

        smart_values = [
            _smart_money(bar) for bar in bars[-smart_config["ma_length"] :]
        ]
        smart_window = smart_values[-smart_config["ma_length"] :]
        smart_money_value = smart_window[-1]
        smart_money_average = fmean(smart_window)
        smart_money_positive = _passes_smart_money_threshold(
            smart_money_value,
            smart_money_average,
            smart_config["absolute_threshold"],
            smart_config["positive_z_threshold"],
            pstdev(smart_window),
        )

        institutional_volume = (
            volumes[-1]
            > volume_moving_average * institutional_config["volume_multiple"]
        )
        ad_values = _ad_line(bars[-institutional_config["ad_ma_length"] :])
        ad_value = ad_values[-1]
        ad_average = fmean(ad_values[-institutional_config["ad_ma_length"] :])
        accumulation = institutional_volume and ad_value > ad_average
        distribution = institutional_volume and ad_value < ad_average

        reference_end = len(bars) - liquidity_config["break_reference_offset"]
        reference_start = reference_end - liquidity_config["lookback"]
        reference_bars = bars[reference_start:reference_end]
        recent_high = max(bar.high for bar in reference_bars)
        recent_low = min(bar.low for bar in reference_bars)
        current = bars[-1]
        upper_psychological_level = _nearest_psychological_level(
            current.close,
            current.high,
            liquidity_config["psych_level_brackets"],
        )
        lower_psychological_level = _nearest_psychological_level(
            current.close,
            current.low,
            liquidity_config["psych_level_brackets"],
        )
        liquidity_volume_spike = (
            current.volume
            > volume_moving_average * liquidity_config["volume_multiple"]
        )
        upper_trap = (
            current.high > recent_high
            and current.close < recent_high
            and liquidity_volume_spike
            and _within_psychological_level(
                current.high,
                upper_psychological_level,
                current.close,
                liquidity_config["psych_proximity"],
            )
            and _passes_optional_ratio(
                (current.high - recent_high) / recent_high,
                liquidity_config["breakout_margin_min"],
            )
            and _passes_optional_ratio(
                (current.high - current.close) / current.high,
                liquidity_config["retracement_min"],
            )
        )
        lower_trap = (
            current.low < recent_low
            and current.close > recent_low
            and liquidity_volume_spike
            and _within_psychological_level(
                current.low,
                lower_psychological_level,
                current.close,
                liquidity_config["psych_proximity"],
            )
            and _passes_optional_ratio(
                (recent_low - current.low) / recent_low,
                liquidity_config["breakout_margin_min"],
            )
            and _passes_optional_ratio(
                (current.close - current.low) / current.low,
                liquidity_config["retracement_min"],
            )
        )
        below_nash = current.close < center - half_width
        above_nash = current.close > center + half_width
        nash_position = "below" if below_nash else "above" if above_nash else "inside"
        return GameSignalSnapshot(
            bar_time=bars[-1].time_key,
            config_version=self._config.version,
            decision_point=request.decision_point,
            nash=NashObservation(
                center=center,
                upper=center + half_width,
                lower=center - half_width,
                position=nash_position,
            ),
            herd=HerdObservation(
                rsi=rsi,
                volume_moving_average=volume_moving_average,
                momentum=momentum,
                momentum_moving_average=momentum_moving_average,
                volume_spike=volume_spike,
                buying=herd_buying,
                selling=herd_selling,
            ),
            smart_money=SmartMoneyObservation(
                value=smart_money_value,
                moving_average=smart_money_average,
                positive=smart_money_positive,
            ),
            institutional_flow=InstitutionalFlowObservation(
                institutional_volume=institutional_volume,
                accumulation=accumulation,
                distribution=distribution,
            ),
            liquidity_trap=LiquidityTrapObservation(
                recent_high=recent_high,
                recent_low=recent_low,
                upper_psychological_level=upper_psychological_level,
                lower_psychological_level=lower_psychological_level,
                volume_spike=liquidity_volume_spike,
                upper=upper_trap,
                lower=lower_trap,
            ),
            features=DerivedFeatureObservation(
                contrarian_buy=herd_selling and accumulation,
                contrarian_sell=herd_buying and (distribution or upper_trap),
                momentum_buy=(
                    below_nash and smart_money_positive and not herd_buying
                ),
                momentum_sell=(
                    above_nash and not smart_money_positive and not herd_selling
                ),
                nash_reversion_buy=(
                    below_nash
                    and current.close > bars[-2].close
                    and current.volume > volume_moving_average
                ),
                nash_reversion_sell=(
                    above_nash
                    and current.close < bars[-2].close
                    and current.volume > volume_moving_average
                ),
            ),
        )


def load_game_signal_config(path: Path | str) -> GameSignalConfig:
    source = Path(path)
    try:
        with source.open("r", encoding="utf-8") as config_file:
            values = yaml.safe_load(config_file)
    except (OSError, yaml.YAMLError) as error:
        raise ConfigError(f"[{source.resolve()}] cannot load YAML: {error}") from error
    if not isinstance(values, Mapping):
        raise ConfigError(f"[{source.name}] top-level value must be a mapping")
    return GameSignalConfig.from_mapping(values)


def _copy_mapping(value: Mapping[str, Any]) -> dict[str, Any]:
    copied: dict[str, Any] = {}
    for key, item in value.items():
        if isinstance(item, Mapping):
            copied[key] = _copy_mapping(item)
        elif isinstance(item, list):
            copied[key] = [
                _copy_mapping(entry) if isinstance(entry, Mapping) else entry
                for entry in item
            ]
        else:
            copied[key] = item
    return copied


def _freeze(value: Any) -> Any:
    if isinstance(value, Mapping):
        return MappingProxyType({key: _freeze(item) for key, item in value.items()})
    if isinstance(value, list):
        return tuple(_freeze(item) for item in value)
    return value


def _require_finite(value: object, field_name: str) -> None:
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(value)
    ):
        raise ValueError(f"{field_name} must be a finite number")


def _validate_bar(bar: Bar) -> None:
    if not isinstance(bar, Bar):
        raise TypeError("market data must contain Bar values")
    for field_name in ("open", "high", "low", "close", "volume"):
        value = getattr(bar, field_name)
        _require_finite(value, field_name)
    if bar.high < max(bar.open, bar.close) or bar.low > min(bar.open, bar.close):
        raise ValueError(f"OHLC values are inconsistent at {bar.time_key!r}")
    if bar.high < bar.low:
        raise ValueError(f"high must not be below low at {bar.time_key!r}")
    if bar.volume < 0:
        raise ValueError(f"volume must be non-negative at {bar.time_key!r}")


def _momentum_values(
    closes: list[float],
    lookback: int,
    moving_average_length: int,
) -> list[float]:
    start = len(closes) - moving_average_length
    return [closes[index] - closes[index - lookback] for index in range(start, len(closes))]


def _wilder_rsi(closes: list[float], length: int) -> float:
    changes = [current - previous for previous, current in zip(closes, closes[1:])]
    gains = [max(change, 0.0) for change in changes]
    losses = [max(-change, 0.0) for change in changes]
    average_gain = fmean(gains[:length])
    average_loss = fmean(losses[:length])
    for gain, loss in zip(gains[length:], losses[length:]):
        average_gain = (average_gain * (length - 1) + gain) / length
        average_loss = (average_loss * (length - 1) + loss) / length
    if average_loss == 0:
        return 100.0 if average_gain > 0 else 50.0
    relative_strength = average_gain / average_loss
    return 100.0 - 100.0 / (1.0 + relative_strength)


def _passes_optional_deviation(difference: float, threshold: float | None) -> bool:
    if difference <= 0:
        return False
    return threshold is None or difference >= threshold


def _smart_money(bar: Bar) -> float:
    return (bar.close - bar.open) / (bar.high - bar.low) * bar.volume


def _passes_smart_money_threshold(
    value: float,
    moving_average: float,
    absolute_threshold: float | None,
    z_threshold: float | None = None,
    std: float = 0.0,
) -> bool:
    if value <= moving_average:
        return False
    if absolute_threshold is not None and value < absolute_threshold:
        return False
    if z_threshold is not None:
        if std <= 0:
            return False
        return (value - moving_average) / std >= z_threshold
    return True


def _ad_line(bars: list[Bar]) -> list[float]:
    """Return window-relative cumulative AD values.

    Accumulation-versus-average comparisons are invariant to the omitted
    cumulative prefix. The relative values are deliberately kept internal;
    exposing them as a full-history AD level would misstate their semantics.
    """
    cumulative = 0.0
    values: list[float] = []
    for bar in bars:
        money_flow_multiplier = (
            (bar.close - bar.low) - (bar.high - bar.close)
        ) / (bar.high - bar.low)
        cumulative += money_flow_multiplier * bar.volume
        values.append(cumulative)
    return values


def _nearest_psychological_level(
    close: float,
    extreme: float,
    brackets: tuple[Mapping[str, Any], ...],
) -> float:
    step: float | None = None
    for bracket in brackets:
        upper_bound = bracket["below"]
        if upper_bound is None or close < upper_bound:
            step = float(bracket["step"])
            break
    if step is None:  # validated configuration always has an unbounded final bracket
        raise ValueError("psychological price brackets do not cover the current close")
    return math.floor(extreme / step + 0.5) * step


def _within_psychological_level(
    extreme: float,
    psychological_level: float,
    close: float,
    proximity: float,
) -> bool:
    return abs(extreme - psychological_level) / close < proximity


def _passes_optional_ratio(value: float, threshold: float | None) -> bool:
    return threshold is None or value >= threshold
