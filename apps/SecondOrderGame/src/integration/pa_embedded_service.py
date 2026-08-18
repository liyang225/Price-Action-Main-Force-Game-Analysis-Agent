"""Headless application service used only by PA's embedded workspace."""
from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from dataclasses import asdict
from datetime import datetime
import hashlib
import json
from pathlib import Path
from typing import Any

from src.data.daily_cache import DailyMaterialCache
from src.data.models import NewsItem
from src.data.protocol import DataSourceError
from src.integration.labeler_status import (
    LabelerStatus,
    LabelerStatusTracker,
    LoadState,
    RunState,
)
from src.integration.pa_link import PAStage2Input, sanitize_pa_payload
from src.integration.progress import ProgressEvent, ProgressSink


ROOT = Path(__file__).resolve().parents[2]
_SHARED_MATERIAL_CACHE = DailyMaterialCache(ROOT / "runtime" / "material_archives")


class PAEmbeddedService:
    """Run SecondOrderGame with PA-owned market and model boundaries."""

    def __init__(
        self,
        *,
        market_source: Any,
        model_client: Any,
        context_builder_factory: Callable[[Any], Any] | None = None,
        orchestrator_factory: Callable[..., Any] | None = None,
        subject_purpose_analyzer_factory: Callable[[Any], Any] | None = None,
        news_emotion_analyzer_factory: Callable[[Any], Any] | None = None,
        material_cache: DailyMaterialCache | None = None,
        history_database: Path | str | None = None,
        dsa_runtime_enabled: bool = False,
        dsa_refresher: Callable[[Mapping[str, Any]], Mapping[str, Any]] | None = None,
        progress_sink: ProgressSink | None = None,
        llm_observation_sink: Callable[[str, str, str], None] | None = None,
        labeler_catchup: bool = True,
        labeler_catchup_sectors: Callable[[], Mapping[str, str]] | None = None,
        labeler_catchup_sink: Callable[[str], None] | None = None,
        labeler_status_tracker: LabelerStatusTracker | None = None,
        capital_flow_catchup: bool = True,
        capital_flow_database: Path | str | None = None,
        capital_flow_scope: Callable[[], tuple[Iterable[str], Iterable[str]]] | None = None,
        capital_flow_catchup_sink: Callable[[str], None] | None = None,
        pa_settings_path: Path | str | None = None,
        material_auto_archive: bool = True,
    ) -> None:
        self._market_source = market_source
        self._model_client = model_client
        self._material_cache = material_cache or _SHARED_MATERIAL_CACHE
        self._history_database = history_database
        self._dsa_runtime_enabled = bool(dsa_runtime_enabled)
        self._dsa_refresher = dsa_refresher
        self._progress_sink = progress_sink if progress_sink is not None else ProgressSink()
        self._llm_observation_sink = llm_observation_sink
        self._labeler_catchup = bool(labeler_catchup)
        self._labeler_catchup_sectors = labeler_catchup_sectors
        self._labeler_catchup_sink = labeler_catchup_sink or self._emit_labeler_catchup
        self._labeler_status = labeler_status_tracker or LabelerStatusTracker()
        self._capital_flow_catchup = bool(capital_flow_catchup)
        self._capital_flow_scope = capital_flow_scope
        self._capital_flow_catchup_sink = capital_flow_catchup_sink or self._emit_capital_flow_catchup
        self._capital_flow_db = _resolve_capital_flow_database(capital_flow_database)
        self._pa_settings_path = (
            Path(pa_settings_path) if pa_settings_path is not None else None
        )
        self._material_auto_archive = bool(material_auto_archive)
        self._prepared_context: Any | None = None
        self._prepared_payload: dict[str, Any] | None = None
        self._subject_purpose_analyzer: Any | None = None
        self._news_emotion_analyzer: Any | None = None
        self._context_builder_factory = context_builder_factory or (
            lambda source: self._create_context_builder(
                source, self._material_cache, capital_flow_ledger=self._capital_flow_db
            )
        )
        self._orchestrator_factory = orchestrator_factory or self._create_orchestrator
        self._subject_purpose_analyzer_factory = (
            subject_purpose_analyzer_factory or self._create_subject_purpose_analyzer
        )
        self._news_emotion_analyzer_factory = (
            news_emotion_analyzer_factory or self._create_news_emotion_analyzer
        )
        self._start_labeler_catchup()
        self._start_capital_flow_catchup()

    def _emit_labeler_catchup(self, message: str) -> None:
        self._emit("info", message, stage="labeler", source="catchup")

    def _emit_capital_flow_catchup(self, message: str) -> None:
        self._emit("info", message, stage="capital_flow", source="catchup")

    def _start_capital_flow_catchup(self) -> None:
        """Background catch-up of the P0-8 capital-flow ledger window.

        Safe on every startup: the staleness scan is a per-code ledger query,
        and only codes missing from the recent 40-trading-day window trigger
        incremental network work.  Runs on a daemon thread so opening the
        workspace never blocks.
        """
        if not self._capital_flow_catchup:
            return
        scope_provider = self._capital_flow_scope
        if scope_provider is None:
            scope_provider = self._default_capital_flow_scope
        try:
            watchlist, sectors = scope_provider()
        except Exception as exc:  # noqa: BLE001
            self._capital_flow_catchup_sink(
                f"资金流补采集跳过：采集范围不可用（{exc}）"
            )
            return
        sector_names = self._sector_capital_flow_names(sectors)
        sector_focus_codes = self._sector_capital_flow_focus_codes(sectors)
        if not tuple(watchlist) and not tuple(sectors):
            return
        market_source = self._market_source
        sink = self._capital_flow_catchup_sink
        database = self._capital_flow_db

        def sweep() -> None:
            from src.data.capital_flow_daily import (
                PRODUCTION_RATE_LIMITER,
                run_capital_flow_catchup,
            )

            try:
                report = run_capital_flow_catchup(
                    market_source,
                    as_of=datetime.now().date(),
                    watchlist_codes=tuple(watchlist),
                    sector_codes=tuple(sectors),
                    sector_names=sector_names,
                    sector_focus_codes=sector_focus_codes,
                    ledger_database=database,
                    rate_limiter=PRODUCTION_RATE_LIMITER,
                    progress=sink,
                )
            except Exception as exc:  # noqa: BLE001 — catch-up must never break startup
                sink(f"资金流补采集失败：{exc}")
                return
            if report.errors:
                sink(f"资金流补采集未完成：{report.errors[0]}")
            elif report.caught_up:
                sink("资金流台账已是最新（40 交易日窗口）")
            else:
                sink(
                    f"资金流补采集完成：新增 {report.inserted_count} 条，"
                    f"跳过 {report.skipped_count} 条，失败 {len(report.failures)} 条"
                )

        import threading

        thread = threading.Thread(
            target=sweep, name="second-order-capital-flow-catchup", daemon=True
        )
        thread.start()

    def _default_capital_flow_scope(self) -> tuple[Iterable[str], Iterable[str]]:
        """(watchlist, sectors) — scope file + PA 设置关联板块 + 材料缓存 registry.

        板块代码无需手动维护，自动从三处合并：
        1. scope 文件 ``sectors``（历史持久化的板块清单）
        2. PA 二阶设置 ``symbol_preferences`` 里每个自选标的的 ``sector_code``
        3. 材料缓存的 sector_registry（预取/分析时登记的板块）

        否则板块资金流永不采集，UI 会显示「台账中无该板块资金流记录」。
        """
        watchlist: Iterable[str] = ()
        sectors: Iterable[str] = ()
        try:
            from src.data.capital_flow_daily import DEFAULT_SCOPE_FILE, load_scope

            watchlist, sectors = load_scope(DEFAULT_SCOPE_FILE)
        except Exception:  # noqa: BLE001 — scope file is optional
            pass
        registry_sectors = tuple(self._cached_sector_registry())
        pa_sectors = self._pa_settings_sectors()
        merged_sectors = tuple(
            dict.fromkeys((*tuple(sectors), *registry_sectors, *pa_sectors))
        )
        return tuple(watchlist), merged_sectors

    def _pa_settings_sectors(self) -> tuple[str, ...]:
        """Extract associated sector codes from PA 二阶设置 symbol_preferences."""
        if self._pa_settings_path is None:
            return ()
        try:
            from src.data.capital_flow_daily import load_pa_settings_mapping

            mapping = load_pa_settings_mapping(self._pa_settings_path)
        except Exception:  # noqa: BLE001 — settings mapping is best-effort
            return ()
        return tuple(
            dict.fromkeys(
                str(meta.get("sector_code") or "").strip()
                for meta in mapping.values()
                if isinstance(meta, Mapping)
                and str(meta.get("sector_code") or "").strip()
            )
        )

    def _sector_capital_flow_names(
        self, sector_codes: Iterable[str]
    ) -> dict[str, str]:
        """Map Futu plate codes to the associated names entered in PA settings."""
        names = dict(self._cached_sector_registry())
        if self._pa_settings_path is not None:
            try:
                from src.data.capital_flow_daily import load_pa_settings_mapping

                for meta in load_pa_settings_mapping(self._pa_settings_path).values():
                    code = str(meta.get("sector_code") or "").strip()
                    name = str(meta.get("sector_name") or "").strip()
                    if code and name:
                        names[code] = name
            except Exception:  # noqa: BLE001 -- optional settings enrichment
                pass
        return {
            code: names[code]
            for code in dict.fromkeys(str(value).strip() for value in sector_codes)
            if code and str(names.get(code) or "").strip()
        }

    def _sector_capital_flow_focus_codes(
        self, sector_codes: Iterable[str]
    ) -> dict[str, tuple[str, ...]]:
        """Keep each PA stock in its associated sector's representative basket."""
        if self._pa_settings_path is None:
            return {}
        try:
            from src.data.capital_flow_daily import load_pa_settings_mapping

            mapping = load_pa_settings_mapping(self._pa_settings_path)
        except Exception:  # noqa: BLE001 -- settings enrichment is best-effort
            return {}
        allowed = {str(code).strip() for code in sector_codes if str(code).strip()}
        grouped: dict[str, list[str]] = {}
        for stock_code, meta in mapping.items():
            sector_code = str(meta.get("sector_code") or "").strip()
            if sector_code in allowed:
                grouped.setdefault(sector_code, []).append(stock_code)
        return {
            sector_code: tuple(dict.fromkeys(stock_codes))
            for sector_code, stock_codes in grouped.items()
        }

    def _watchlist_with_sectors(self) -> dict[str, dict[str, str]]:
        """Map the scope-file watchlist to PA 二阶设置 associated sectors.

        The labeled stock scope is the watchlist itself; the associated
        sector (from ``symbol_preferences``) is only the benchmark.  Codes
        without a usable mapping survive in the result with empty sector
        fields and are reported as skipped by the sweep.
        """
        from src.data.capital_flow_daily import (
            DEFAULT_SCOPE_FILE,
            load_pa_settings_mapping,
            load_scope,
        )

        watchlist, _ = load_scope(DEFAULT_SCOPE_FILE)
        mapping = load_pa_settings_mapping(self._pa_settings_path)
        return {code: mapping.get(code, {}) for code in watchlist}

    def _start_labeler_catchup(self) -> None:
        """Background catch-up of missed post-hoc labels (ADR-0007 Day 0).

        Two scopes are supported.  With ``pa_settings_path`` configured the
        catch-up runs in watchlist mode: the labeled scope is the watchlist
        (scope file), each symbol benchmarked by its associated sector from
        PA 二阶设置 ``symbol_preferences``.  Otherwise it falls back to the
        material-cache sector registry.  Safe on every startup: the gap scan
        is a single ledger query, and only lagging entities trigger network
        work.  The sweep runs on a daemon thread so opening the workspace
        never blocks.

        The shared :class:`LabelerStatusTracker` is advanced along the way:
        the constructor thread reports the load state (rules + scope), the
        sweep thread reports the run state (catch-up progress).  When several
        service instances share one tracker, ``mark_running`` guarantees only
        one sweep is active at a time and the others reuse its status.
        """
        status = self._labeler_status
        if not self._labeler_catchup:
            status.set_run(RunState.SKIPPED, "标注器补跑已禁用")
            return
        status.set_load(LoadState.LOADING, "正在加载标注器规则与标注范围…")
        market_source = self._market_source
        sink = self._labeler_catchup_sink
        watchlist: Mapping[str, Mapping[str, str]] | None = None
        sectors: Mapping[str, str] = {}
        if self._pa_settings_path:
            try:
                watchlist = self._watchlist_with_sectors()
            except Exception as exc:  # noqa: BLE001
                status.set_load(LoadState.LOAD_FAILED, f"自选池关联板块不可用（{exc}）")
                status.set_run(RunState.SKIPPED, f"标注器补跑跳过：自选池关联板块不可用（{exc}）")
                sink(f"标注器补跑跳过：自选池关联板块不可用（{exc}）")
                return
            if not watchlist:
                status.set_load(LoadState.LOAD_FAILED, "自选池无有效关联板块映射")
                status.set_run(RunState.SKIPPED, "标注器补跑跳过：自选池无有效关联板块映射")
                sink("标注器补跑跳过：自选池无有效关联板块映射")
                return
        else:
            sectors_provider = self._labeler_catchup_sectors
            if sectors_provider is None:
                sectors_provider = self._cached_sector_registry
            try:
                sectors = dict(sectors_provider())
            except Exception as exc:  # noqa: BLE001
                status.set_load(LoadState.LOAD_FAILED, f"板块列表不可用（{exc}）")
                status.set_run(RunState.SKIPPED, f"标注器补跑跳过：板块列表不可用（{exc}）")
                sink(f"标注器补跑跳过：板块列表不可用（{exc}）")
                return
            if not sectors:
                status.set_load(LoadState.LOADED, "标注范围为空（无在册板块）")
                status.set_run(RunState.SKIPPED, "标注器补跑跳过：标注范围为空")
                return
        status.set_load(LoadState.LOADED, "标注器规则与标注范围已就绪")
        if not status.mark_running():
            return  # 另一线程正在补跑：复用其状态，不重复启动

        def sweep() -> None:
            from src.labeler.nightly import run_labeler_catchup

            try:
                report = run_labeler_catchup(
                    market_source,
                    trading_date=datetime.now().date(),
                    sectors=sectors,
                    progress=sink,
                    capital_flow_database=self._capital_flow_db,
                    watchlist=watchlist,
                )
            except DataSourceError as exc:  # noqa: BLE001 — data source unavailable
                status.set_run(RunState.SOURCE_UNAVAILABLE, f"标注器补跑失败：{exc}")
                sink(f"标注器补跑失败：{exc}")
                return
            except Exception as exc:  # noqa: BLE001 — catch-up must never break startup
                status.set_run(RunState.PARTIAL_FAILURE, f"标注器补跑失败：{exc}")
                sink(f"标注器补跑失败：{exc}")
                return
            finally:
                status.clear_running()
            details = {
                "as_of": report.as_of,
                "caught_up_to": report.caught_up_to,
                "ran_sweeps": report.ran_sweeps,
                "missed_dates": tuple(report.missed_dates),
                "errors": tuple(report.errors),
                "skipped": tuple(report.skipped),
            }
            if report.missed_dates:
                message = f"标注器补跑未完成：{len(report.missed_dates)} 个交易日失败"
                status.set_run(RunState.PARTIAL_FAILURE, message, details)
                sink(message)
            elif report.ran_sweeps:
                message = (
                    f"标注器补跑完成：补齐 {report.ran_sweeps} 个交易日"
                    + (f"，{len(report.errors)} 个错误" if report.errors else "")
                )
                status.set_run(
                    RunState.PARTIAL_FAILURE if report.errors else RunState.COMPLETED,
                    message,
                    details,
                )
                sink(message)
            else:
                message = f"标注器已是最新（{report.caught_up_to or '无标签'}）"
                status.set_run(RunState.COMPLETED, message, details)
                sink(message)

        import threading

        thread = threading.Thread(
            target=sweep, name="second-order-labeler-catchup", daemon=True
        )
        thread.start()

    def labeler_status(self) -> LabelerStatus:
        """Return the current structured OHLCV labeler status snapshot."""
        return self._labeler_status.snapshot()

    def collect_sector_analysis(
        self,
        sector_code: str,
        sector_name: str = "",
        date: str | None = None,
    ) -> dict[str, Any]:
        """Collect the display-only board-analysis bundle (资金流/连板/龙虎榜).

        Never raises: every network step degrades into ``errors`` inside the
        returned bundle dict.  The bundle is display data only and never
        enters the reasoning pipeline.
        """
        from src.integration.sector_analysis_service import SectorAnalysisService

        service = SectorAnalysisService(
            self._market_source,
            capital_flow_database=self._capital_flow_db,
        )
        bundle = service.collect(
            sector_code=sector_code,
            sector_name=sector_name,
            date=date or datetime.now().date(),
        )
        return bundle.to_dict()

    def _cached_sector_registry(self) -> Mapping[str, str]:
        """Sector code -> name from the daily material cache, if populated."""
        try:
            preview = self._material_cache.preview()
        except Exception:  # noqa: BLE001
            return {}
        registry = preview.get("sector_registry") if isinstance(preview, Mapping) else None
        if not isinstance(registry, Mapping):
            return {}
        return {
            str(code): str(meta.get("sector_name") or code)
            for code, meta in registry.items()
            if isinstance(meta, Mapping)
        }

    def close(self) -> None:
        closer = getattr(self._market_source, "close", None)
        if callable(closer):
            closer()

    def _emit(
        self,
        kind: str,
        message: str,
        *,
        stage: str = "",
        source: str = "",
        symbol: str = "",
    ) -> None:
        self._progress_sink.emit(
            ProgressEvent(
                ts=datetime.now(),
                symbol=symbol,
                kind=kind,
                stage=stage,
                message=message,
                source=source,
            )
        )

    @staticmethod
    def _create_context_builder(
        source: Any,
        material_cache: DailyMaterialCache | None = None,
        *,
        capital_flow_ledger: Path | str | None = None,
    ) -> Any:
        from src.integration.production_context import ProductionContextBuilder
        from src.reasoning.policy_detector import build_policy_detector

        policy_detector = build_policy_detector(source, root=ROOT)
        return ProductionContextBuilder(
            source,
            material_cache=material_cache,
            policy_detector=policy_detector,
            capital_flow_ledger=capital_flow_ledger,
        )

    def archive_materials(self) -> dict[str, Any]:
        """Persist the current day's materials to the archive directory.

        Idempotent and safe to call repeatedly: the cache is write-once after
        15:00 (per the documented close lifecycle), so the first successful
        archive freezes the day's materials and later calls report
        ``already_archived``.  Returns a status dict; never raises.
        """
        try:
            cache = self._material_cache
            status = cache.status()
            if status.get("state") == "archived":
                return {**status, "ok": True, "already_archived": True}
            if not status.get("decision_snapshot_created"):
                return {**status, "ok": False, "reason": "no_decision_snapshot"}
            path = cache.archive()
            archived = cache.status()
            self._emit(
                "info",
                f"当日材料已归档：{path}",
                stage="material",
                source="archive",
            )
            return {**archived, "ok": True, "archive_path": str(path)}
        except ValueError as exc:
            # Before 15:00 the cache refuses to archive; the lifecycle poll
            # retries later in the day.
            return {"ok": False, "state": "pre_close", "reason": str(exc)}
        except Exception as exc:  # noqa: BLE001 — archiving must never break the UI
            return {"ok": False, "state": "error", "error": str(exc) or type(exc).__name__}

    def _maybe_archive_materials(self) -> None:
        """Auto-archive once per day when a snapshot exists and it is past 15:00.

        Triggered by the lifecycle poll (``material_cache_status`` /
        ``material_cache_preview``) and by the end of an analysis; idempotent,
        so the close-time decision itself is never disturbed.
        """
        if not self._material_auto_archive:
            return
        self.archive_materials()

    def material_cache_status(self) -> dict[str, Any]:
        """Expose only lifecycle metadata for the PA material-cache tab."""
        self._maybe_archive_materials()
        return self._material_cache.status()

    def material_cache_preview(self) -> dict[str, dict[str, Any]]:
        """Return a detached preview for the lifecycle console."""
        self._maybe_archive_materials()
        return self._material_cache.preview()

    def material_cache_news(self) -> dict[str, Any]:
        """Return a JSON-safe news projection for the PA material-cache tab.

        Each cached sector maps to its sector code, message count, and a list of
        message records exposing title / url / published date / related code and,
        when already preanalyzed, the ScoredNewsEvent fields.
        """
        preview = self._material_cache.preview()
        if not isinstance(preview, Mapping):
            return {}
        news_by_sector = preview.get("news", {})
        scored_by_code = preview.get("scored_news", {})
        registry = preview.get("sector_registry", {})
        subject_by_name = preview.get("subject_purpose", {})
        code_by_name = _sector_code_by_name(registry)
        result: dict[str, Any] = {}
        if not isinstance(news_by_sector, Mapping):
            return result
        for sector_name, raw_items in news_by_sector.items():
            name = str(sector_name)
            items = _cached_news_items(raw_items)
            code = code_by_name.get(name)
            scored_items = _scored_news_items(scored_by_code.get(code))
            scored_by_title = {_news_title(item): item for item in scored_items}
            cleaned = [
                _clean_news_item(
                    item,
                    sector_code=code,
                    scored=scored_by_title.get(_news_title(item)),
                )
                for item in items
            ]
            sentiment_sum = _sum_sentiment(cleaned)
            sector_purpose = _sector_purpose(subject_by_name.get(name))
            result[name] = {
                "sector_code": code,
                "count": len(items),
                "sentiment_sum": sentiment_sum,
                "subject_purpose": sector_purpose,
                "items": cleaned,
            }
        return result

    def prefetch_news(
        self,
        sectors: tuple[str, ...] | list[str],
        *,
        search_keywords: Mapping[str, str] | None = None,
        sector_codes: Mapping[str, str] | None = None,
    ) -> dict[str, Any]:
        """Fetch one round, optionally searching by stock name while caching by sector."""
        try:
            from src.data.news_prefetch import NewsPrefetchTask

            normalized = tuple(
                dict.fromkeys(
                    str(sector).strip()
                    for sector in sectors
                    if isinstance(sector, str) and sector.strip()
                )
            )
            if not normalized:
                raise ValueError("消息预取至少需要一个关联板块名称")
            identities: dict[str, str] = {}
            if sector_codes is not None:
                code_to_name: dict[str, str] = {}
                for name in normalized:
                    code = str(sector_codes.get(name) or "").strip()
                    if not code:
                        raise ValueError("每个预取板块都必须配置非空 sector_code")
                    code_to_name.setdefault(code, name)
                identities = {name: code for code, name in code_to_name.items()}
                normalized = tuple(identities)
            source = self._market_source
            aliases = {
                str(key).strip(): str(value).strip()
                for key, value in (search_keywords or {}).items()
                if str(key).strip() and str(value).strip()
            }
            if aliases:
                source = _AliasedNewsSource(source, aliases)
            task = NewsPrefetchTask(
                source,
                self._material_cache,
                interval_seconds=0,
                round_interval_seconds=0,
            )
            task.run_round(normalized)
            if identities:
                for name, code in identities.items():
                    self._material_cache.put(
                        "sector_registry", code, {"sector_name": name}
                    )
                self._material_cache.put(
                    "sector_registry_meta",
                    "current",
                    {"complete": True, "sector_codes": tuple(identities.values())},
                )
            return {
                "ok": True,
                "status": "ready",
                "task": task.status(),
                "cache": self.material_cache_status(),
                "preview": self.material_cache_preview(),
                "news_details": self.material_cache_news(),
            }
        except Exception as exc:
            return {
                "ok": False,
                "status": "error",
                "error": str(exc) or type(exc).__name__,
                "cache": self.material_cache_status(),
            }

    def import_news(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        """Insert a user-authored news item at the top of a sector's cache.

        The item is cached exactly like a prefetched ``NewsItem``: it joins the
        next preanalysis round (subject purpose + sentiment scoring) and is
        dropped with the daily archive at close.
        """
        try:
            sector_name = str(payload.get("sector_name") or "").strip()
            snippet = str(payload.get("snippet") or payload.get("content") or "").strip()
            title = str(payload.get("title") or "").strip()
            published_date = str(payload.get("published_date") or "").strip()
            source = str(payload.get("source") or "").strip() or "用户导入"
            if not sector_name:
                raise ValueError("导入消息需要板块名称")
            if not snippet:
                raise ValueError("导入消息需要正文内容")
            item = NewsItem(
                title=title or snippet[:15],
                snippet=snippet,
                url="",
                published_date=published_date,
                source=source,
            )
            existing = _cached_news_items(self._material_cache.get("news", sector_name))
            self._material_cache.put("news", sector_name, (item,) + existing)
            return {
                "ok": True,
                "status": "ready",
                "sector_name": sector_name,
                "title": item.title,
                "cache": self.material_cache_status(),
                "preview": self.material_cache_preview(),
                "news_details": self.material_cache_news(),
            }
        except Exception as exc:
            return {
                "ok": False,
                "status": "error",
                "error": str(exc) or type(exc).__name__,
                "cache": self.material_cache_status(),
                "news_details": self.material_cache_news(),
            }

    def prepare_materials(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        """Preanalyze cached news, then freeze the complete decision materials."""
        try:
            normalized = normalize_pa_payload(payload)
            pa = PAStage2Input.from_pa_payload(normalized)
            self._preanalyze_cached_news(normalized, pa.symbol)
            context = self._context_builder_factory(self._market_source).build(pa)
            self._prepared_context = context
            self._prepared_payload = normalized
            return {
                "ok": True,
                "status": "ready",
                "materials": dict(context.materials),
                "game_signals": dict(context.game_signals),
                "cache": self.material_cache_status(),
                "preview": self.material_cache_preview(),
                "news_details": self.material_cache_news(),
                "llm_trace": list(getattr(self._model_client, "request_log", ())),
            }
        except Exception as exc:
            return {
                "ok": False,
                "status": "error",
                "error": str(exc) or type(exc).__name__,
                "cache": self.material_cache_status(),
                "news_details": self.material_cache_news(),
                "llm_trace": list(getattr(self._model_client, "request_log", ())),
            }

    def _preanalyze_cached_news(
        self, payload: Mapping[str, Any], symbol: str
    ) -> None:
        from src.reasoning.subject_purpose_analyzer import extract_cached_news_items
        from src.data.news_sentiment import NewsSentimentAnalyzer

        sector_name = _sector_name(payload, symbol)
        news_items = extract_cached_news_items(
            self._material_cache.get("news", sector_name)
        )
        if not news_items:
            return
        self._emit(
            "info",
            f"开始消息主体目的与情绪预分析（{len(news_items)} 条）",
            stage="sentiment",
            source="prepare_materials",
            symbol=symbol,
        )
        # The subject-purpose step is the news → LLM injection the user must be
        # able to audit.  It only needs the sector name, so run it before any
        # sector-code requirement; a missing code must never block the LLM call.
        if self._subject_purpose_analyzer is None:
            self._subject_purpose_analyzer = self._subject_purpose_analyzer_factory(
                self._model_client
            )
        try:
            material = self._subject_purpose_analyzer.analyze(
                sector_name, news_items
            )
        except Exception as exc:
            self._material_cache.put(
                "subject_purpose",
                sector_name,
                {
                    "status": "error",
                    "sector_name": sector_name,
                    "news_count": len(news_items),
                    "error": str(exc) or type(exc).__name__,
                },
            )
            raise
        self._material_cache.put("subject_purpose", sector_name, material)
        # LLM-scored news sentiment (ADR-0023/0024): the model judges each
        # message's direction/strength plus its relevance and source
        # credibility; the deterministic validity factor is still applied by
        # NewsSentimentAnalyzer below, with deterministic relevance/credibility
        # as the fallback when the model omits them.
        if self._news_emotion_analyzer is None:
            self._news_emotion_analyzer = self._news_emotion_analyzer_factory(
                self._model_client
            )
        model_judgments = self._news_emotion_analyzer.analyze(
            sector_name, symbol, news_items
        )
        # Deterministic ScoredNewsEvent scoring keys the cache by sector code but
        # only uses it for relevance matching; fall back to the symbol so a
        # missing PA sector_code never blanks the sentiment column in the UI.
        try:
            sector_code = _sector_code(payload)
        except ValueError:
            sector_code = symbol or sector_name
        events = NewsSentimentAnalyzer.from_file().analyze(
            news_items,
            sector_code=sector_code,
            sector_name=sector_name,
            target_security=symbol,
            as_of=datetime.now(),
            subject_purpose=material,
            model_judgments=model_judgments,
        )
        self._material_cache.put(
            "scored_news",
            sector_code,
            {
                "status": "ready" if events else "empty",
                "sector_code": sector_code,
                "source_fingerprint": hashlib.sha256(
                    json.dumps(
                        [
                            asdict(item)
                            if hasattr(item, "__dataclass_fields__")
                            else dict(item) if isinstance(item, Mapping) else str(item)
                            for item in news_items
                        ],
                        ensure_ascii=False,
                        sort_keys=True,
                    ).encode("utf-8")
                ).hexdigest(),
                "items": [asdict(event) for event in events],
            },
        )
        self._material_cache.put(
            "sector_registry", sector_code, {"sector_name": sector_name}
        )

    def ensure_market_material(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        """Ensure one same-date DSA result before downstream material preparation."""
        if not self._dsa_runtime_enabled:
            return {"status": "disabled", "triggered": False}
        refresher = self._dsa_refresher
        if refresher is None:
            from src.integration.dsa_runtime import ensure_current_dsa_market_review

            refresher = ensure_current_dsa_market_review
        return dict(refresher(payload))

    @staticmethod
    def _create_orchestrator(model_client: Any, **kwargs: Any) -> Any:
        from src.integration.production_orchestrator import build_production_orchestrator

        return build_production_orchestrator(model_client, root=ROOT, **kwargs)

    @staticmethod
    def _create_subject_purpose_analyzer(model_client: Any) -> Any:
        from src.reasoning.prompt_router import load_prompt_router
        from src.reasoning.subject_purpose_analyzer import SubjectPurposeAnalyzer

        router = load_prompt_router(
            ROOT / "config" / "prompt_routing.yaml",
            ROOT / "prompt_engine",
        )
        return SubjectPurposeAnalyzer(model_client, router)

    @staticmethod
    def _create_news_emotion_analyzer(model_client: Any) -> Any:
        from src.reasoning.prompt_router import load_prompt_router
        from src.reasoning.news_emotion_analyzer import NewsEmotionAnalyzer

        router = load_prompt_router(
            ROOT / "config" / "prompt_routing.yaml",
            ROOT / "prompt_engine",
        )
        return NewsEmotionAnalyzer(model_client, router)

    def run_analysis(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        orchestrator = None
        try:
            normalized = normalize_pa_payload(payload)
            if self._dsa_runtime_enabled and not isinstance(
                normalized.get("market_analysis"), Mapping
            ):
                refresh_result = self.ensure_market_material(normalized)
                normalized["dsa_runtime"] = refresh_result
            pa = PAStage2Input.from_pa_payload(normalized)
            if self._prepared_context is not None and self._prepared_payload == normalized:
                context = self._prepared_context
                self._prepared_context = None
                self._prepared_payload = None
            else:
                self._prepared_context = None
                self._prepared_payload = None
                context = self._context_builder_factory(self._market_source).build(pa)
            orchestrator = self._orchestrator_factory(
                self._model_client,
                progress_sink=self._progress_sink,
                llm_observation_sink=self._llm_observation_sink,
            )
            result = orchestrator.run(pa, context=context)
            serialized = result.to_dict()
            serialized["progress_events"] = [
                event.to_dict() for event in self._progress_sink.events()
            ]
            history_id = self._record_history(serialized, database=self._history_database)
            self._maybe_archive_materials()
            return {
                "ok": True,
                "status": "ready",
                "history_id": history_id,
                "result": serialized,
                "news_details": self.material_cache_news(),
                "llm_trace": list(getattr(self._model_client, "request_log", ())),
            }
        except Exception as exc:
            return {
                "ok": False,
                "status": "error",
                "error": str(exc) or type(exc).__name__,
                "llm_trace": list(getattr(self._model_client, "request_log", ())),
            }
        finally:
            if orchestrator is not None:
                close = getattr(orchestrator, "close", None)
                if callable(close):
                    close()

    @staticmethod
    def _record_history(
        result: Mapping[str, Any], *, database: Path | str | None = None
    ) -> int | None:
        try:
            from src.integration.analysis_history import AnalysisHistoryStore

            store = AnalysisHistoryStore(database)
            try:
                return store.append(result)
            finally:
                store.close()
        except Exception:
            # History is important but must not discard a completed analysis.
            return None

    @staticmethod
    def list_history(
        symbol: str | None = None,
        limit: int = 50,
        database: Path | str | None = None,
    ) -> list[dict[str, Any]]:
        from src.integration.analysis_history import AnalysisHistoryStore

        store = AnalysisHistoryStore(database)
        try:
            return store.list_recent(symbol=symbol, limit=limit)
        finally:
            store.close()

    @staticmethod
    def resolve_history(
        record_id: int, actual_result: str, database: Path | str | None = None
    ) -> bool:
        from src.integration.analysis_history import AnalysisHistoryStore

        store = AnalysisHistoryStore(database)
        try:
            return store.resolve(record_id, actual_result)
        finally:
            store.close()

    @staticmethod
    def delete_history(
        record_id: int, database: Path | str | None = None
    ) -> bool:
        from src.integration.analysis_history import AnalysisHistoryStore

        store = AnalysisHistoryStore(database)
        try:
            return store.delete(record_id)
        finally:
            store.close()

    @staticmethod
    def history_summary(
        symbol: str | None = None, database: Path | str | None = None
    ) -> dict[str, Any]:
        from src.integration.analysis_history import AnalysisHistoryStore

        store = AnalysisHistoryStore(database)
        try:
            return store.summary(symbol=symbol)
        finally:
            store.close()


def normalize_pa_payload(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Flatten PA's structured trade handoff without model-session internals."""
    value = sanitize_pa_payload(payload)
    stage2 = value.get("stage2_decision")
    decision = stage2.get("decision") if isinstance(stage2, Mapping) else None
    nested = decision if isinstance(decision, Mapping) else stage2
    if not isinstance(nested, Mapping):
        nested = {}

    point = value.get("decision_point", value.get("decisionPoint", "close"))
    aliases = {"收盘": "close", "午盘": "midday", "15:00": "close", "11:30": "midday"}
    value["decision_point"] = aliases.get(str(point), point)
    order_type = nested.get("order_type", nested.get("orderType"))
    if value.get("order_type") is None and order_type is not None:
        value["order_type"] = order_type
    if "should_trade" not in value and "shouldTrade" not in value:
        value["should_trade"] = bool(
            order_type and str(order_type) not in {"不下单", "none", "None"}
        )
    fields = {
        "entry_price": ("entry_price", "entryPrice", "price"),
        "stop_loss_price": ("stop_loss_price", "stopLossPrice", "stop_price"),
        "estimated_win_rate": ("estimated_win_rate", "estimatedWinRate", "win_rate"),
        "take_profit_price": ("take_profit_price", "takeProfitPrice", "target_price"),
        "order_direction": ("order_direction", "orderDirection", "direction"),
    }
    for target, candidates in fields.items():
        if value.get(target) is not None:
            continue
        for candidate in candidates:
            if nested.get(candidate) is not None:
                value[target] = nested[candidate]
                break
    value.pop("sellable_quantity", None)
    value.pop("today_locked_quantity", None)
    return value


class _AliasedNewsSource:
    """Keep cache identity stable while using a more useful search keyword."""

    def __init__(self, source: Any, aliases: Mapping[str, str]) -> None:
        self._source = source
        self._aliases = dict(aliases)

    def search_news(self, cache_key: str) -> Any:
        return self._source.search_news(self._aliases.get(cache_key, cache_key))


def _sector_code_by_name(registry: Any) -> dict[str, str]:
    result: dict[str, str] = {}
    if isinstance(registry, Mapping):
        for code, meta in registry.items():
            if isinstance(meta, Mapping) and meta.get("sector_name"):
                result[str(meta["sector_name"])] = str(code)
    return result


def _scored_news_items(scored: Any) -> tuple[Any, ...]:
    if isinstance(scored, Mapping):
        items = scored.get("items")
        if isinstance(items, list | tuple):
            return tuple(items)
    return ()


def _cached_news_items(cached: Any) -> tuple[Any, ...]:
    """Normalize both raw cache rows and legacy decision-news wrappers."""
    if isinstance(cached, Mapping):
        items = cached.get("items")
        return tuple(items) if isinstance(items, list | tuple) else ()
    if isinstance(cached, list | tuple):
        return tuple(cached)
    return (cached,)


def _news_title(item: Any) -> str:
    if hasattr(item, "__dataclass_fields__") and not isinstance(item, type):
        return str(getattr(item, "title", "") or "")
    if isinstance(item, Mapping):
        return str(item.get("title") or "")
    return str(item)


def _clean_news_item(
    item: Any, *, sector_code: str | None, scored: Mapping[str, Any] | None
) -> dict[str, Any]:
    if hasattr(item, "__dataclass_fields__") and not isinstance(item, type):
        raw = asdict(item)
    elif isinstance(item, Mapping):
        raw = dict(item)
    else:
        raw = {"title": str(item)}
    related = raw.get("related_securities") or ()
    if isinstance(related, str):
        related = [related]
    scored = scored if isinstance(scored, Mapping) else {}
    return {
        "title": raw.get("title"),
        "url": raw.get("url"),
        "published_date": _format_published_date(raw.get("published_date")),
        "code": list(related),
        "sector_code": sector_code,
        "source": raw.get("source"),
        "snippet": raw.get("snippet"),
        "sentiment_score": scored.get("sentiment_score"),
        "subject_purpose": scored.get("subject_purpose"),
        "relevance": scored.get("relevance"),
        "validity": scored.get("validity"),
        "source_credibility": scored.get("source_credibility"),
    }


def _format_published_date(value: Any) -> str | None:
    """Render a provider timestamp as a compact, locale-friendly string.

    Tavily emits RFC 2822 (``"Sat, 15 Aug 2026 00:00:00 GMT"``); Futu emits
    opaque ``M/D`` strings.  Reuse the proven parser and fall back to the raw
    value when it cannot be normalized.
    """
    if value is None:
        return None
    from src.data.news_sentiment import _parse_datetime

    parsed = _parse_datetime(value)
    if parsed is None:
        return str(value)
    return parsed.strftime("%Y-%m-%d %H:%M")


def _sum_sentiment(items: Any) -> float | None:
    """Sum per-message sentiment scores, or None when none are scored yet."""
    if not isinstance(items, list | tuple):
        return None
    total: float | None = None
    for item in items:
        if not isinstance(item, Mapping):
            continue
        value = item.get("sentiment_score")
        if isinstance(value, int | float) and not isinstance(value, bool):
            total = float(value) if total is None else total + float(value)
    return round(total, 6) if total is not None else None


def _sector_purpose(subject: Any) -> str | None:
    if isinstance(subject, Mapping):
        purpose = subject.get("true_purpose")
        if isinstance(purpose, str) and purpose.strip():
            return purpose.strip()
    return None


def _sector_name(payload: Mapping[str, Any], symbol: str) -> str:
    containers: list[Mapping[str, Any]] = [payload]
    for name in ("stage1_diagnosis", "stage2_decision", "analysis_record"):
        container = payload.get(name)
        if isinstance(container, Mapping):
            containers.append(container)
            decision = container.get("decision")
            if isinstance(decision, Mapping):
                containers.append(decision)
    for container in containers:
        if not isinstance(container, Mapping):
            continue
        for key in ("sector_name", "sectorName", "industry_name", "industryName"):
            value = container.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
    return symbol


def _sector_code(payload: Mapping[str, Any]) -> str:
    containers: list[Mapping[str, Any]] = [payload]
    for name in ("stage1_diagnosis", "stage2_decision", "analysis_record"):
        container = payload.get(name)
        if isinstance(container, Mapping):
            containers.append(container)
            decision = container.get("decision")
            if isinstance(decision, Mapping):
                containers.append(decision)
    for container in containers:
        for key in ("sector_code", "sectorCode", "industry_code", "industryCode"):
            value = container.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
    raise ValueError("PA 设置缺少必填的 sector_code")


def _resolve_capital_flow_database(
    database: Path | str | None,
) -> Path:
    """Resolve the P0-8 ledger path, defaulting to the runtime location."""
    if database is not None:
        return Path(database)
    from src.data.capital_flow_daily import DEFAULT_CAPITAL_FLOW_DB

    return DEFAULT_CAPITAL_FLOW_DB


__all__ = ["PAEmbeddedService", "normalize_pa_payload"]
