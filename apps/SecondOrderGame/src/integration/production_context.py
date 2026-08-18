"""Build auditable production inputs from the unified market-data seam."""
from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import asdict, dataclass, is_dataclass
from datetime import date, datetime, timedelta
import hashlib
import json
from pathlib import Path
import math
from typing import Any

import yaml


from src.hmm_filter import HMMFilter, load_config as load_hmm_config
from src.data.daily_cache import DailyMaterialCache, DailyMaterialSnapshot
from src.data.capital_flow_ledger import CapitalFlowLedger
from src.data.news_sentiment import NewsSentimentAnalyzer
from src.data.sector_market import load_sector_price_action, resolve_trading_session
from src.data.sentiment_breadth import SentimentBreadthCalculator
from src.data.sentiment_calculator import SentimentCalculator
from src.data.sentiment_ledger import SentimentLedger, SentimentState
from src.integration.pa_link import BridgeContext, PADecisionPoint, PAStage2Input
from src.reasoning.policy_detector import PolicyDetector
from src.probability import (
    ConditionDimension,
    DecisionPoint,
    InsufficientData,
    OpeningDistributionConfig,
    OpeningDistributionEstimator,
    OpeningDistributionKind,
    OpeningRange,
    T1FirstPassageConfig,
    T1FirstPassageEstimator,
    T1FirstPassageRequest,
    T1GateCalculator,
    T1GateRequest,
    T1GateResult,
    T1GateStatus,
    load_t1_gate_config,
)
from src.reasoning.scenario_builder import REQUIRED_SCENARIOS, ScenarioInputs
from src.signals.game_signals import (
    GameSignalCalculator,
    GameSignalRequest,
    load_game_signal_config,
)


ROOT = Path(__file__).resolve().parents[2]
CYCLE_STATES = ("冰点", "启动", "发酵", "高潮", "退潮")


@dataclass(frozen=True, slots=True)
class ProductionProbabilityConfig:
    opening: OpeningDistributionConfig
    first_passage: T1FirstPassageConfig


def load_production_probability_config(
    path: Path | str = ROOT / "config" / "probability.yaml",
) -> ProductionProbabilityConfig:
    source = Path(path)
    try:
        raw = yaml.safe_load(source.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise ValueError(f"无法加载概率配置 {source}: {exc}") from exc
    if not isinstance(raw, Mapping):
        raise ValueError("概率配置顶层必须是映射")
    version = raw.get("version")
    opening = raw.get("opening_distribution")
    passage = raw.get("first_passage")
    if not isinstance(version, int) or version < 1:
        raise ValueError("概率配置 version 必须是正整数")
    if not isinstance(opening, Mapping) or not isinstance(passage, Mapping):
        raise ValueError("概率配置缺少 opening_distribution 或 first_passage")

    ranges_value = opening.get("ranges")
    priors_value = opening.get("priors")
    if not isinstance(ranges_value, list) or not isinstance(priors_value, Mapping):
        raise ValueError("开盘分布配置的 ranges/priors 无效")
    ranges = tuple(
        OpeningRange(
            str(item["outcome"]), item.get("lower_bound"), item.get("upper_bound")
        )
        for item in ranges_value
        if isinstance(item, Mapping)
    )
    priors = {
        kind: dict(priors_value[kind.value])
        for kind in OpeningDistributionKind
    }
    opening_config = OpeningDistributionConfig(
        ranges=ranges,
        min_day_blocks=opening["min_day_blocks"],
        bootstrap_iterations=opening["bootstrap_iterations"],
        prior_strength=opening["prior_strength"],
        priors=priors,
        config_version=version,
        random_seed=opening["random_seed"],
    )
    first_passage_config = T1FirstPassageConfig(
        min_samples=passage["min_samples"],
        volatility_lookback=passage["volatility_lookback"],
        recent_return_lookback=passage["recent_return_lookback"],
        volatility_quantiles=tuple(passage["volatility_quantiles"]),
        turnover_quantiles=tuple(passage["turnover_quantiles"]),
        recent_return_edges=tuple(passage["recent_return_edges"]),
        degradation_order=tuple(
            ConditionDimension(item) for item in passage["degradation_order"]
        ),
        bootstrap_iterations=passage["bootstrap_iterations"],
        random_seed=passage["random_seed"],
        config_version=version,
    )
    return ProductionProbabilityConfig(opening_config, first_passage_config)


class ProductionContextBuilder:
    """Run deterministic market, HMM, probability and gate modules in order."""

    def __init__(
        self,
        market_source: Any,
        *,
        today: Callable[[], date] = date.today,
        history_days: int = 900,
        probability_config: ProductionProbabilityConfig | None = None,
        material_cache: DailyMaterialCache | None = None,
        sentiment_database: Path | str | None = None,
        policy_detector: PolicyDetector | None = None,
        capital_flow_ledger: Path | str | None = None,
    ) -> None:
        self._source = market_source
        self._today = today
        self._history_days = history_days
        self._probability = probability_config or load_production_probability_config()
        self._material_cache = material_cache
        self._sentiment_database = Path(sentiment_database) if sentiment_database is not None else ROOT / "runtime" / "sentiment.db"
        self._policy_detector = policy_detector
        self._capital_flow_ledger = (
            Path(capital_flow_ledger) if capital_flow_ledger is not None else None
        )

    def build(self, pa: PAStage2Input) -> BridgeContext:
        end_date = self._today()
        start_date = end_date - timedelta(days=self._history_days)
        start, end = start_date.isoformat(), end_date.isoformat()
        code = _futu_symbol(pa.symbol)
        decision_point = _decision_point(pa.decision_point)
        supplied_cycle = _cycle_position(pa.payload)
        cycle = supplied_cycle or "待判断"
        sector_code = _sector_code(pa.payload)
        sector_name = _sector_name(pa.payload, sector_code)
        validator = getattr(self._source, "validate_sector_code", None)
        if callable(validator):
            validator(sector_code)

        hmm = HMMFilter(_load_calibrated_hmm(), sector_name=sector_name)
        # The cycle LLM is the observation sensor. Do not contaminate the
        # prior with a fabricated "冰点" (or double-consume PA's prior label)
        # before the production orchestrator runs that observation.
        belief = hmm.belief
        detected_env, policy_detection = self._detect_policy(
            end_date, fallback=_policy_environment(pa.payload)
        )
        policy_environment = detected_env
        stage1_analysis = _stage1_analysis(pa.payload)
        participant_priors = {
            participant: hmm.predict_behaviors(participant, policy_environment)
            for participant in ("主力", "散户")
        }
        news = self._news_for_decision(sector_name)
        subject_purpose = self._subject_purpose_for_decision(sector_name)
        decision_at = _decision_datetime(end_date, pa.decision_point)
        scored_news = self._scored_news_for_decision(
            sector_code,
            sector_name,
            pa.symbol,
            news,
            subject_purpose,
            updated_at=decision_at,
        )

        signals = GameSignalCalculator(
            self._source, load_game_signal_config(ROOT / "config" / "signals.yaml")
        ).calculate(GameSignalRequest(code, start, end, decision_point))
        signal_payload = signals.to_dict()
        market_material = _market_material(pa.payload, as_of=decision_at)
        price_action = load_sector_price_action(
            self._source, sector_code, start=start, end=end
        )
        trading_session = resolve_trading_session(self._source, end_date, price_action)
        sentiment = self._calculate_sentiment(
            sector_code,
            scored_news,
            price_action.calculator_input(),
            trading_session.is_trading_day,
            session_source=trading_session.source,
            fundamental_baseline=_fundamental_baseline(pa.payload),
            updated_at=decision_at,
        )
        breadth = self._sentiment_breadth(
            sector_code, cycle, sentiment.sentiment_index
        )
        sector_kline_120m = _sector_kline_120m(self._source, sector_code, start, end)
        board_analysis = self._board_analysis_material(sector_code, sector_name, end_date)
        sector_material = {
            "sector_code": sector_code,
            "sector_name": sector_name,
            "news_keyword": sector_name,
            "sentiment_index": sentiment.sentiment_index,
            "sentiment_index_details": sentiment.to_dict(),
            "sector_price_action": asdict(price_action),
            "sector_kline_120m": sector_kline_120m,
            "board_analysis": board_analysis,
            "trading_session": asdict(trading_session),
            "cycle_position": cycle,
            "cycle_position_source": "pa_payload" if supplied_cycle else "llm_pending",
            "signal_credibility": _signal_credibility(signal_payload),
        }
        cache_info = self._freeze_materials(
            symbol=pa.symbol,
            sector_code=sector_code,
            sector_name=sector_name,
            market=market_material,
            sector=sector_material,
            news=news,
            scored_news=scored_news,
            subject_purpose=subject_purpose,
            signals=signal_payload,
        )

        opening = OpeningDistributionEstimator(
            self._source, self._probability.opening
        ).estimate(code, decision_point, start, end)
        target = _number_from_payload(
            pa.payload,
            "take_profit_price",
            "takeProfitPrice",
            "target_price",
            "targetPrice",
        )
        stop = pa.stop_loss_price
        position_cases = _position_cases()
        if target is None or stop is None:
            reason = "缺少止盈价或止损价，无法计算 C 类首达概率；禁止新增买入"
            return self._insufficient_context(
                pa,
                cycle,
                belief,
                participant_priors,
                signal_payload,
                opening,
                decision_point,
                reason,
                news,
                subject_purpose,
                position_cases,
                cache_info,
                market_material,
                sector_material,
                code=code,
                as_of=end_date,
                policy_environment=policy_environment,
                policy_detection=policy_detection,
            )

        try:
            passage = T1FirstPassageEstimator(
                self._source, self._probability.first_passage
            ).estimate(
                T1FirstPassageRequest(
                    code=code,
                    start=start,
                    end=end,
                    target_price=target,
                    stop_loss_price=stop,
                    decision_point=decision_point,
                    cycle_state_snapshots={},
                    turnover_rate_snapshots={},
                )
            )
        except ValueError as exc:
            message = str(exc)
            if "target_return must be" not in message and "stop_loss_return must be" not in message:
                raise
            return self._insufficient_context(
                pa,
                cycle,
                belief,
                participant_priors,
                signal_payload,
                opening,
                decision_point,
                (
                    "当前参考价与 PA 止盈/止损价无法形成有效的正收益与负风险区间；"
                    "C 类首达概率数据不足，禁止新增买入"
                ),
                news,
                subject_purpose,
                position_cases,
                cache_info,
                market_material,
                sector_material,
                code=code,
                as_of=end_date,
                policy_environment=policy_environment,
                policy_detection=policy_detection,
            )
        gate = T1GateCalculator(load_t1_gate_config()).evaluate(
            T1GateRequest(
                trading_date=end_date,
                decision_point=decision_point,
                holdings=(),
                opening_distribution=opening,
                first_passage=passage,
            )
        )
        opening_values = _available_probabilities(opening)
        passage_values = _available_first_passage(passage)
        scenarios = _scenarios(opening_values, passage_values, gate)
        return BridgeContext(
            cycle_position=cycle,
            policy_environment=policy_environment,
            materials={
                "market_window": {"code": code, "start": start, "end": end},
                "material_cache": cache_info["status"],
                "material_snapshot": cache_info["snapshot"],
                "market_analysis": market_material,
                "sentiment_breadth": breadth,
                **({"pa_stage1_analysis": stage1_analysis} if stage1_analysis else {}),
                "sector_analysis": sector_material,
                "user_context": list(pa.payload.get("user_context") or ()),
                "news": news,
                "scored_news": scored_news,
                **(
                    {"subject_purpose": subject_purpose}
                    if subject_purpose is not None
                    else {}
                ),
                **(
                    {"policy_detection": policy_detection}
                    if policy_detection is not None
                    else {}
                ),
                "position_cases": position_cases,
                "participant_priors": participant_priors,
                "dragon_tiger": _dragon_tiger_material(
                    self._source, code, end_date.isoformat()
                ),
                "limit_pool": _limit_pool_material(self._source, end_date.isoformat()),
                "capital_flow": self._capital_flow_material(code, end_date),
                "probability_chain": {
                    "opening_distribution": _serialize_opening(opening),
                    "first_passage": passage.to_dict(),
                    "t1_gate": gate.to_dict(),
                },
            },
            game_signals=signal_payload,
            sector_belief=belief,
            prior_weight=1.0,
            scenario_probabilities_and_gates=scenarios,
            scenario_gate_results={name: gate for name in REQUIRED_SCENARIOS},
            source="SecondOrderGame.production_context",
        )

    def _detect_policy(
        self,
        end_date: date,
        *,
        fallback: str,
    ) -> tuple[str, dict[str, Any] | None]:
        """Run the policy-environment detector, or keep the caller default.

        Returns ``(environment, audit_material)``.  The audit material is
        injected into the decision materials so every downstream consumer can
        see which evidence (hard / soft / system) chose the multiplier group.
        When the detector is not configured, the fallback environment is
        returned unchanged with no audit material.
        """
        detector = self._policy_detector
        if detector is None:
            return fallback, None
        materials: dict[str, Any] = {}
        if self._material_cache is not None:
            preview = self._material_cache.preview()
            if isinstance(preview, Mapping):
                materials = {str(category): dict(items) for category, items in preview.items() if isinstance(items, Mapping)}
        try:
            detection = detector.detect(DailyMaterialSnapshot(end_date, materials))
        except Exception as exc:  # noqa: BLE001 — policy sensing must not block a decision
            return fallback, {
                "status": "error",
                "environment": fallback,
                "error": str(exc) or type(exc).__name__,
            }
        return detection.environment, {
            "status": "detected",
            "environment": detection.environment,
            "multipliers": dict(detection.multipliers),
            "hard_branch_enabled": detection.hard_branch_enabled,
            "soft_branch_enabled": detection.soft_branch_enabled,
            "evidence": [
                {
                    "channel": item.channel,
                    "environment": item.environment,
                    "summary": item.summary,
                }
                for item in detection.evidence
            ],
        }

    def _insufficient_context(
        self,
        pa: PAStage2Input,
        cycle: str,
        belief: Mapping[str, float],
        participant_priors: Mapping[str, Mapping[str, float]],
        signal_payload: Mapping[str, Any],
        opening: Any,
        decision_point: DecisionPoint,
        reason: str,
        news: Mapping[str, Any],
        subject_purpose: Mapping[str, Any] | None,
        position_cases: Mapping[str, str],
        cache_info: Mapping[str, Any],
        market_material: Mapping[str, Any],
        sector_material: Mapping[str, Any],
        *,
        code: str,
        as_of: date,
        policy_environment: str,
        policy_detection: Mapping[str, Any] | None,
    ) -> BridgeContext:
        stage1_analysis = _stage1_analysis(pa.payload)
        gate = T1GateResult(
            status=T1GateStatus.INSUFFICIENT_DATA,
            decision_point=decision_point,
            executable_actions=(),
            reason=reason,
            favorable_opening_probability=None,
            neutral_opening_probability=None,
            adverse_opening_probability=None,
            target_first_probability=None,
            stop_first_probability=None,
            neither_probability=None,
            config_version=load_t1_gate_config().config_version,
        )
        scenarios = _scenarios(_available_probabilities(opening), None, gate)
        return BridgeContext(
            cycle_position=cycle,
            policy_environment=policy_environment,
            materials={
                "material_cache": cache_info["status"],
                "material_snapshot": cache_info["snapshot"],
                "market_analysis": dict(market_material),
                "sentiment_breadth": self._sentiment_breadth_from_cache(),
                **({"pa_stage1_analysis": stage1_analysis} if stage1_analysis else {}),
                "sector_analysis": dict(sector_material),
                "user_context": list(pa.payload.get("user_context") or ()),
                "news": dict(news),
                **(
                    {"subject_purpose": dict(subject_purpose)}
                    if subject_purpose is not None
                    else {}
                ),
                **(
                    {"policy_detection": dict(policy_detection)}
                    if policy_detection is not None
                    else {}
                ),
                "position_cases": dict(position_cases),
                "participant_priors": dict(participant_priors),
                "capital_flow": self._capital_flow_material(code, as_of),
                "probability_chain": {
                    "opening_distribution": _serialize_opening(opening),
                    "first_passage": None,
                    "t1_gate": gate.to_dict(),
                    "reason": reason,
                }
            },
            game_signals=dict(signal_payload),
            sector_belief=dict(belief),
            prior_weight=1.0,
            scenario_probabilities_and_gates=scenarios,
            scenario_gate_results={name: gate for name in REQUIRED_SCENARIOS},
            source="SecondOrderGame.production_context",
        )

    def _freeze_materials(
        self,
        *,
        symbol: str,
        sector_code: str,
        sector_name: str,
        market: Mapping[str, Any],
        sector: Mapping[str, Any],
        news: Mapping[str, Any],
        scored_news: Mapping[str, Any],
        subject_purpose: Mapping[str, Any] | None,
        signals: Mapping[str, Any],
    ) -> dict[str, Any]:
        """Fill and freeze all decision materials at one deterministic boundary."""
        if self._material_cache is None:
            return {
                "status": {
                    "status": "not_configured",
                    "state": "not_configured",
                    "trading_date": self._today().isoformat(),
                    "categories": {},
                    "decision_snapshot_created": False,
                    "lifecycle": "当日共享；15:00 后归档；下一交易日重建",
                },
                "snapshot": {},
            }
        try:
            materials = {
                "market": {"global": dict(market)},
                "sector": {sector_name: dict(sector)},
                "scored_news": {sector_code: dict(scored_news)},
                "sector_registry": {sector_code: {"sector_name": sector_name}},
                "stock_signals": {symbol: dict(signals)},
            }
            # Prefetch owns the canonical ``news/<sector>`` value and keeps it
            # as a sequence of news rows.  A decision snapshot may add a missing
            # entry for standalone callers, but must never replace that sequence
            # with the ``status/source/items`` decision wrapper.
            if self._material_cache.get("news", sector_name) is None:
                news_items = _news_items(news)
                materials["news"] = {sector_name: news_items}
            if subject_purpose is not None:
                materials["subject_purpose"] = {
                    sector_name: dict(subject_purpose)
                }
            snapshot = self._material_cache.put_many_and_snapshot(materials)
            return {
                "status": {
                    **self._material_cache.status(),
                    "status": "decision_snapshot",
                    "state": "decision_snapshot",
                    "lifecycle": "当日共享；15:00 后归档；下一交易日重建",
                },
                "snapshot": _snapshot_to_dict(
                    snapshot,
                    symbol=symbol,
                    sector_code=sector_code,
                    sector_name=sector_name,
                ),
            }
        except Exception as exc:  # optional cache must not hide deterministic results
            return {
                "status": {
                    "status": "unavailable",
                    "state": "unavailable",
                    "trading_date": self._today().isoformat(),
                    "categories": {},
                    "decision_snapshot_created": False,
                    "error": str(exc) or type(exc).__name__,
                    "lifecycle": "当日共享；15:00 后归档；下一交易日重建",
                },
                "snapshot": {},
            }

    def _news_for_decision(self, sector_name: str) -> dict[str, Any]:
        """Read the production cache; never block a decision on news I/O."""
        if self._material_cache is None:
            return _news_material(self._source, sector_name)
        cached = self._material_cache.get("news", sector_name)
        if cached is None:
            return {
                "status": "not_prefetched",
                "source": "daily_material_cache",
                "items": [],
                "error": "该板块尚未完成消息预取",
            }
        if isinstance(cached, Mapping) and "status" in cached:
            return {**dict(cached), "source": "daily_material_cache"}
        return {
            "status": "ready" if cached else "empty",
            "source": "daily_material_cache",
            "items": _plain_value(cached if isinstance(cached, list | tuple) else [cached]),
            "error": None,
        }

    def _subject_purpose_for_decision(
        self, sector_name: str
    ) -> dict[str, Any] | None:
        if self._material_cache is None:
            return None
        cached = self._material_cache.get("subject_purpose", sector_name)
        return dict(cached) if isinstance(cached, Mapping) else None

    def _scored_news_for_decision(
        self,
        sector_code: str,
        sector_name: str,
        symbol: str,
        news: Mapping[str, Any],
        subject_purpose: Mapping[str, Any] | None,
        *,
        updated_at: datetime,
    ) -> dict[str, Any]:
        source_fingerprint = _news_batch_fingerprint(news)
        if self._material_cache is not None:
            cached = self._material_cache.get("scored_news", sector_code)
            if (
                isinstance(cached, Mapping)
                and cached.get("source_fingerprint") == source_fingerprint
            ):
                return dict(cached)
        events = NewsSentimentAnalyzer.from_file().analyze(
            _news_items(news),
            sector_code=sector_code,
            sector_name=sector_name,
            target_security=_futu_symbol(symbol),
            as_of=updated_at,
            subject_purpose=subject_purpose,
        )
        return {
            "status": "ready" if events else "empty",
            "sector_code": sector_code,
            "source_fingerprint": source_fingerprint,
            "items": [asdict(event) for event in events],
        }

    def _calculate_sentiment(
        self,
        sector_code: str,
        scored_news: Mapping[str, Any],
        price_action: Mapping[str, Any],
        is_trading_day: bool,
        *,
        session_source: str,
        fundamental_baseline: float | None,
        updated_at: datetime,
    ):
        """Compute once per trading day from the ledger plus cached materials."""
        self._sentiment_database.parent.mkdir(parents=True, exist_ok=True)
        calculator = SentimentCalculator.from_file()
        with SentimentLedger(self._sentiment_database) as ledger:
            previous = ledger.load(sector_code)
            if not is_trading_day:
                return calculator.hold(
                    sector_code=sector_code,
                    previous_index=previous.sentiment_index if previous else None,
                    updated_at=updated_at,
                    status=(
                        "market_data_unavailable"
                        if session_source == "unavailable"
                        else "non_trading_day"
                    ),
                )
            prior_index = ledger.daily_base(
                sector_code,
                updated_at.date().isoformat(),
                previous.sentiment_index
                if previous
                else calculator.baseline if fundamental_baseline is None else fundamental_baseline,
            )
            result = calculator.calculate(
                sector_code=sector_code,
                previous_index=prior_index,
                news=_news_items(scored_news),
                price_action=price_action,
                updated_at=updated_at,
            )
            ledger.save(SentimentState(sector_code, result.sentiment_index, updated_at))
            return result

    def _sentiment_breadth(
        self, sector_code: str, cycle_position: str, sentiment_index: float
    ) -> dict[str, Any]:
        registry = {sector_code}
        cycles = {sector_code: cycle_position}
        registry_complete = False
        if self._material_cache is not None:
            preview = self._material_cache.preview()
            registry.update(preview.get("sector_registry", {}))
            meta = preview.get("sector_registry_meta", {}).get("current")
            registry_complete = isinstance(meta, Mapping) and meta.get("complete") is True
            for material in preview.get("sector", {}).values():
                if isinstance(material, Mapping):
                    code = material.get("sector_code")
                    cycle = material.get("cycle_position")
                    if isinstance(code, str) and isinstance(cycle, str):
                        cycles[code] = cycle
        with SentimentLedger(self._sentiment_database) as ledger:
            states = list(ledger.list_states())
        if not any(state.sector_code == sector_code for state in states):
            states.append(SentimentState(sector_code, sentiment_index, datetime.now()))
        return SentimentBreadthCalculator().calculate(
            states,
            registered_sector_codes=tuple(sorted(registry)),
            cycle_positions=cycles,
            registry_complete=registry_complete,
        ).to_dict()

    def _sentiment_breadth_from_cache(self) -> dict[str, Any]:
        if self._material_cache is None:
            return {"status": "insufficient_data"}
        preview = self._material_cache.preview()
        registry = tuple(preview.get("sector_registry", {}))
        meta = preview.get("sector_registry_meta", {}).get("current")
        registry_complete = isinstance(meta, Mapping) and meta.get("complete") is True
        cycles: dict[str, str] = {}
        for material in preview.get("sector", {}).values():
            if isinstance(material, Mapping):
                code, cycle = material.get("sector_code"), material.get("cycle_position")
                if isinstance(code, str) and isinstance(cycle, str):
                    cycles[code] = cycle
        with SentimentLedger(self._sentiment_database) as ledger:
            states = ledger.list_states()
        return SentimentBreadthCalculator().calculate(
            states,
            registered_sector_codes=registry,
            cycle_positions=cycles,
            registry_complete=registry_complete,
        ).to_dict()

    def _board_analysis_material(
        self, sector_code: str, sector_name: str, as_of: date
    ) -> dict[str, Any]:
        """板块级资金流 / 连板 / 龙虎榜聚合，供大模型注入（软材料）。

        复用 :class:`SectorAnalysisService` 的采集逻辑：资金流读 P0-8 台账
        （按板块代码），连板与龙虎榜用板块成分股过滤。该聚合同时服务于 UI
        展示与大模型注入，属于证据性材料——任一维度失败只降级为 status /
        errors，不阻断确定性链路（板块 K 线才是硬门禁）。
        """
        from src.integration.sector_analysis_service import SectorAnalysisService

        try:
            service = SectorAnalysisService(
                self._source,
                capital_flow_database=self._capital_flow_ledger,
            )
            bundle = service.collect(
                sector_code=sector_code,
                sector_name=sector_name,
                date=as_of.isoformat(),
            )
            return bundle.to_dict()
        except Exception as exc:  # noqa: BLE001 — evidence material must not break the chain
            return {
                "sector_code": sector_code,
                "sector_name": sector_name,
                "date": as_of.isoformat(),
                "status": "unavailable",
                "capital_flow": [],
                "limit_pool": [],
                "dragon_tiger": [],
                "errors": [str(exc) or type(exc).__name__],
            }

    def _capital_flow_material(self, code: str, as_of: date) -> dict[str, Any]:
        """Read the P0-8 ledger window for one code into a decision material.

        The ledger is the bounded short-term store (40 trading days); any
        missing or unreadable ledger yields a non-blocking status so the
        deterministic chain never depends on capital-flow availability.
        """
        ledger_path = self._capital_flow_ledger
        if ledger_path is None:
            return {"status": "not_configured", "code": code}
        try:
            with CapitalFlowLedger(ledger_path) as ledger:
                flows = tuple(
                    flow for flow in ledger.flows_for(code) if flow.date <= as_of.isoformat()
                )
        except Exception as exc:  # noqa: BLE001 — optional material must not break the chain
            return {
                "status": "unavailable",
                "code": code,
                "error": str(exc) or type(exc).__name__,
            }
        if not flows:
            return {"status": "no_data", "code": code}
        items = [
            {
                "date": flow.date,
                "main_in_flow": flow.main_in_flow,
                "super_in_flow": flow.super_in_flow,
                "big_in_flow": flow.big_in_flow,
                "mid_in_flow": flow.mid_in_flow,
                "sml_in_flow": flow.sml_in_flow,
            }
            for flow in flows
        ]
        return {
            "status": "ready",
            "code": code,
            "window_days": len(items),
            "items": items,
            "main_flow_5d": _sum_main_flow(flows[-5:]),
            "main_flow_10d": _sum_main_flow(flows[-10:]),
            "main_flow_20d": _sum_main_flow(flows[-20:]),
            "latest_main_flow": flows[-1].main_in_flow,
        }


def _load_calibrated_hmm() -> dict[str, Any]:
    """Load HMM config fused with accumulated label counts (best effort).

    A fresh system (empty count stores) returns the hand-written prior, so
    behavior is identical to ``load_hmm_config()`` until labels accumulate.
    """
    try:
        from src.labeler.calibration import load_production_hmm_config

        return load_production_hmm_config()
    except Exception:  # noqa: BLE001 — calibration must never break a decision
        return load_hmm_config()


def _scenarios(
    opening: Mapping[str, float] | None,
    passage: Mapping[str, float] | None,
    gate: T1GateResult,
) -> dict[str, ScenarioInputs]:
    return {
        name: ScenarioInputs(
            behavior_forecasts={},
            opening_distribution=opening,
            first_passage=passage,
            gate_status=gate.status,
            executable_actions=gate.executable_actions,
            gate_reason=gate.reason,
        )
        for name in REQUIRED_SCENARIOS
    }


def _snapshot_to_dict(
    snapshot: DailyMaterialSnapshot,
    *,
    symbol: str,
    sector_code: str,
    sector_name: str,
) -> dict[str, Any]:
    """Convert the immutable cache view into a JSON-safe result projection."""
    selected: dict[str, dict[str, Any]] = {}
    for category, key in (
        ("market", "global"),
        ("sector", sector_name),
        ("news", sector_name),
        ("scored_news", sector_code),
        ("sector_registry", sector_code),
        ("subject_purpose", sector_name),
        ("stock_signals", symbol),
    ):
        items = snapshot.materials.get(category, {})
        if key in items:
            selected[category] = {key: items[key]}
    return {
        "trading_date": snapshot.trading_date.isoformat(),
        "materials": _plain_value(selected),
    }


def _plain_value(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _plain_value(item) for key, item in value.items()}
    if isinstance(value, list | tuple):
        return [_plain_value(item) for item in value]
    if isinstance(value, date | datetime):
        return value.isoformat()
    if is_dataclass(value) and not isinstance(value, type):
        return _plain_value(asdict(value))
    return value


def _news_material(source: Any, keyword: str) -> dict[str, Any]:
    search = getattr(source, "search_news", None)
    if not callable(search):
        return {"status": "unavailable", "items": [], "error": "数据源不支持新闻搜索"}
    try:
        items = list(search(keyword) or ())
    except Exception as exc:  # optional material must not break the deterministic chain
        return {"status": "error", "items": [], "error": str(exc) or type(exc).__name__}
    return {
        "status": "ready" if items else "empty",
        "items": [
            asdict(item) if is_dataclass(item) else str(item)
            for item in items
        ],
        "error": None,
    }


def _news_items(news: Mapping[str, Any]) -> tuple[Mapping[str, Any], ...]:
    items = news.get("items")
    if not isinstance(items, list | tuple):
        return ()
    return tuple(item for item in items if isinstance(item, Mapping))


def _news_batch_fingerprint(news: Mapping[str, Any]) -> str:
    payload = json.dumps(_plain_value(news.get("items") or ()), ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _position_cases() -> dict[str, str]:
    return {
        "sellable_existing": "已有持仓：当前可按闸门结论处理可卖旧仓",
        "today_locked": "今日锁定：今日买入部分不可卖，下一交易日再按新决策处理",
        "no_position": "通常情况（无持仓）：仅评估是否允许新增买入",
    }


def _decision_point(value: PADecisionPoint) -> DecisionPoint:
    return DecisionPoint.MIDDAY if value is PADecisionPoint.MIDDAY else DecisionPoint.CLOSE


def _futu_symbol(symbol: str) -> str:
    raw = symbol.strip().upper()
    if raw.startswith(("SH.", "SZ.", "HK.", "US.")):
        return raw
    if "." not in raw:
        return f"{'SH' if raw.startswith(('5', '6', '9')) else 'SZ'}.{raw}"
    code, exchange = raw.rsplit(".", 1)
    return f"{exchange}.{code}"


def _cycle_position(payload: Mapping[str, Any]) -> str | None:
    value = _nested_value(payload, "cycle_position", "cyclePosition", "market_phase")
    return str(value) if value in CYCLE_STATES else None


def _sector_code(payload: Mapping[str, Any]) -> str:
    value = _nested_value(payload, "sector_code", "sectorCode", "industry_code", "industryCode")
    if not isinstance(value, str) or not value.strip():
        raise ValueError("PA 设置缺少必填的 sector_code")
    return value.strip()


def _sector_name(payload: Mapping[str, Any], sector_code: str) -> str:
    value = _nested_value(
        payload, "sector_name", "sectorName", "industry_name", "industryName"
    )
    return str(value).strip() if isinstance(value, str) and value.strip() else sector_code


def _fundamental_baseline(payload: Mapping[str, Any]) -> float | None:
    value = _nested_value(
        payload,
        "sector_fundamental_baseline",
        "sectorFundamentalBaseline",
        "fundamental_baseline",
    )
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise ValueError("sector_fundamental_baseline 必须是 0 到 100 的数字")
    number = float(value)
    if not math.isfinite(number) or not 0 <= number <= 100:
        raise ValueError("sector_fundamental_baseline 必须是 0 到 100 的数字")
    return number


def _market_material(
    payload: Mapping[str, Any], *, as_of: datetime | None = None
) -> dict[str, Any]:
    value = _nested_value(payload, "market_analysis", "marketAnalysis", "market_context")
    if isinstance(value, Mapping):
        if "status" in value:
            return dict(value)
        return {"status": "ready", "source": "DSA", "data": dict(value)}
    from src.integration.dsa_market_context import load_latest_dsa_market_context

    configured_path = _nested_value(
        payload, "dsa_database_path", "dsaDatabasePath", "market_cache_path"
    )
    if isinstance(configured_path, str) and configured_path.strip():
        return load_latest_dsa_market_context(configured_path.strip(), as_of=as_of)
    return load_latest_dsa_market_context(as_of=as_of)


def _decision_datetime(trading_date: date, point: PADecisionPoint) -> datetime:
    return datetime.combine(trading_date, point.at)


def _signal_credibility(signal: Mapping[str, Any]) -> dict[str, Any]:
    if signal.get("status") == "insufficient_data":
        return {"state": "数据不足", "reason": signal.get("reason")}
    features = signal.get("features")
    institutional = signal.get("institutional_flow")
    if not isinstance(features, Mapping) or not isinstance(institutional, Mapping):
        return {"state": "未确认", "reason": "缺少完整资金与博弈特征"}
    aligned = bool(
        (features.get("contrarian_buy") and institutional.get("accumulation"))
        or (features.get("contrarian_sell") and institutional.get("distribution"))
    )
    return {
        "state": "分离" if aligned else "混同",
        "reason": "资金行为与反向信号同向确认" if aligned else "公开信号尚不能区分参与者意图",
    }


def _sector_kline_120m(
    source: Any, sector_code: str, start: str, end: str, *, count: int = 60
) -> dict[str, Any]:
    """板块指数 K_120M 最近 ``count`` 根已收盘（供大模型读趋势，不发个股序列）。"""
    try:
        bars = tuple(source.get_kline(sector_code, "K_120M", start, end))
    except Exception as exc:  # noqa: BLE001
        return {
            "status": "unavailable",
            "bars": [],
            "error": str(exc) or type(exc).__name__,
        }
    if not bars:
        return {"status": "insufficient_data", "bars": [], "error": "板块指数无 K_120M 数据"}
    recent = bars[-count:]
    return {
        "status": "ready",
        "bars": [
            {
                "time": bar.time_key,
                "open": bar.open,
                "high": bar.high,
                "low": bar.low,
                "close": bar.close,
                "volume": bar.volume,
                "turnover": bar.turnover,
            }
            for bar in recent
        ],
    }


def _dragon_tiger_material(source: Any, code: str, date: str) -> dict[str, Any]:
    """龙虎榜材料：信号分档 + 原始席位明细，二者都注入大模型（见 schema §3.5）。"""
    from src.signals.dragon_tiger import extract_dragon_tiger_signals

    try:
        record = source.get_dragon_tiger(code, date)
    except Exception as exc:  # noqa: BLE001
        return {"status": "source_error", "error": str(exc) or type(exc).__name__}
    if record is None:
        return {"status": "no_data"}
    signal = extract_dragon_tiger_signals(record, code=code, date=date)
    return {
        "status": signal.status.value,
        "signal": {
            "date": signal.date,
            "code": signal.code,
            "institution_net_buy": signal.institution_net_buy,
            "institution_net_sell": signal.institution_net_sell,
            "hot_money_net_buy": signal.hot_money_net_buy,
            "hot_money_net_sell": signal.hot_money_net_sell,
            "institution_seats": list(signal.institution_seats),
            "hot_money_seats": list(signal.hot_money_seats),
            "reasons": list(signal.reasons),
            "source": signal.source,
            "source_reference": signal.source_reference,
            "notes": list(signal.notes),
        },
        "raw": {
            "date": record.date,
            "code": record.code,
            "reason": record.reason,
            "net_buy_amount": record.net_buy_amount,
            "buy_amount": record.buy_amount,
            "sell_amount": record.sell_amount,
            "institution_net_buy": record.institution_net_buy,
            "institution_net_sell": record.institution_net_sell,
            "hot_money_net_buy": record.hot_money_net_buy,
            "hot_money_net_sell": record.hot_money_net_sell,
            "institution_seats": list(record.institution_seats),
            "hot_money_seats": list(record.hot_money_seats),
            "buy_seats": list(record.buy_seats),
            "sell_seats": list(record.sell_seats),
            "source": record.source,
            "source_reference": record.source_reference,
        },
    }


def _limit_pool_material(source: Any, date: str) -> dict[str, Any]:
    """连板池（涨停/跌停）：市场情绪广度的证据，聚合分档 + 原始池一并注入。"""
    try:
        records = tuple(source.get_limit_pool(date))
    except Exception as exc:  # noqa: BLE001
        return {"status": "source_error", "error": str(exc) or type(exc).__name__}
    if not records:
        return {"status": "no_data", "date": date}
    rise = [record for record in records if record.direction == "rise"]
    fall = [record for record in records if record.direction == "fall"]
    return {
        "status": "ready",
        "date": date,
        "rise_count": len(rise),
        "fall_count": len(fall),
        "max_rise_streak": max((record.limit_streak for record in rise), default=0),
        "max_fall_streak": max((record.limit_streak for record in fall), default=0),
        "rise_pool": [
            {"code": record.code, "limit_streak": record.limit_streak}
            for record in rise
        ],
        "fall_pool": [
            {"code": record.code, "limit_streak": record.limit_streak}
            for record in fall
        ],
    }


def _policy_environment(payload: Mapping[str, Any]) -> str:
    value = _nested_value(payload, "policy_environment", "policyEnvironment")
    return str(value) if isinstance(value, str) and value.strip() else "无干预"


def _stage1_analysis(payload: Mapping[str, Any]) -> dict[str, Any]:
    value = _nested_value(payload, "stage1_diagnosis", "stage1Diagnosis")
    return dict(value) if isinstance(value, Mapping) else {}


def _nested_value(payload: Mapping[str, Any], *keys: str) -> Any:
    containers: list[Mapping[str, Any]] = [payload]
    for name in ("stage1_diagnosis", "stage2_decision", "decision", "analysis_record"):
        item = payload.get(name)
        if isinstance(item, Mapping):
            containers.append(item)
            nested = item.get("decision")
            if isinstance(nested, Mapping):
                containers.append(nested)
    for container in containers:
        for key in keys:
            if key in container:
                return container[key]
    return None


def _number_from_payload(payload: Mapping[str, Any], *keys: str) -> float | None:
    value = _nested_value(payload, *keys)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    return number if math.isfinite(number) else None


def _serialize_opening(value: Any) -> Any:
    if isinstance(value, InsufficientData):
        return value.to_dict()
    return [item.to_dict() for item in value]


def _available_probabilities(value: Any) -> dict[str, float] | None:
    if isinstance(value, InsufficientData):
        return None
    return {item.outcome: item.probability for item in value}


def _available_first_passage(value: Any) -> dict[str, float] | None:
    if isinstance(value.target_first, InsufficientData) or isinstance(
        value.stop_first, InsufficientData
    ):
        return None
    return {
        "target_first": value.target_first.probability,
        "stop_first": value.stop_first.probability,
        "neither": max(
            0.0, 1.0 - value.target_first.probability - value.stop_first.probability
        ),
    }


def _sum_main_flow(flows: Sequence[Any]) -> float | None:
    """Net main-flow sum over a slice, or None when the slice is empty."""
    values = [float(flow.main_in_flow) for flow in flows]
    return None if not values else round(sum(values), 6)


__all__ = [
    "ProductionContextBuilder",
    "ProductionProbabilityConfig",
    "load_production_probability_config",
]
