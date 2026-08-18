from __future__ import annotations

import json
from typing import Any

import pytest

from src.data.daily_cache import DailyMaterialCache
from src.data.models import NewsItem
from src.integration.model_adapter import ModelResponse
from src.integration.pa_embedded_service import PAEmbeddedService, normalize_pa_payload
from src.integration.pa_link import PAStage2Input
from src.integration.production_context import ProductionContextBuilder


@pytest.fixture(autouse=True)
def _isolate_pa_service(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Never read the deployed scope file, and keep auto-archive off in tests."""
    monkeypatch.setattr(
        "src.data.capital_flow_daily.DEFAULT_SCOPE_FILE", tmp_path / "scope.json"
    )
    original_init = PAEmbeddedService.__init__

    def isolated_init(self, *args, **kwargs):
        kwargs.setdefault("material_auto_archive", False)
        original_init(self, *args, **kwargs)

    monkeypatch.setattr(PAEmbeddedService, "__init__", isolated_init)


def test_embedded_service_uses_pa_model_client_and_normalizes_nested_decision(tmp_path) -> None:
    captured = {}

    class Context:
        pass

    class Builder:
        def __init__(self, source):
            captured["source"] = source

        def build(self, pa):
            captured["pa"] = pa
            return Context()

    class Result:
        def to_dict(self):
            return {"scenario_tree": {"branches": []}}

    class Orchestrator:
        def run(self, pa, *, context):
            captured["model_client"] = model_client
            captured["context"] = context
            return Result()

        def close(self):
            captured["closed"] = True

    market_source = object()
    model_client = object()
    cache = DailyMaterialCache(tmp_path / "archives")
    cache.put("news", "半导体", (NewsItem("测试新闻", "", "", "2026-08-14"),))
    service = PAEmbeddedService(
        market_source=market_source,
        model_client=model_client,
        context_builder_factory=Builder,
        orchestrator_factory=lambda client, **_: Orchestrator(),
        material_cache=cache,
        history_database=tmp_path / "history.db",
    )

    result = service.run_analysis(
        {
            "symbol": "000001.SZ",
            "stage2_decision": {
                "decision": {
                    "order_type": "限价单",
                    "entry_price": 10.2,
                    "take_profit_price": 11.0,
                    "stop_loss_price": 9.8,
                    "estimated_win_rate": 63,
                }
            },
        }
    )

    assert result["ok"] is True
    assert result["news_details"]["半导体"]["count"] == 1
    assert result["llm_trace"] == []
    assert captured["source"] is market_source
    assert captured["model_client"] is model_client
    assert captured["pa"].decision_point.value == "close"
    assert captured["pa"].should_trade is True
    assert captured["pa"].order_type == "限价单"
    assert captured["pa"].entry_price == 10.2
    assert captured["pa"].take_profit_price == 11.0
    assert captured["pa"].stop_loss_price == 9.8
    assert captured["closed"] is True


def test_nested_stage2_no_trade_contract_preserves_null_plan_fields() -> None:
    normalized = normalize_pa_payload(
        {
            "symbol": "000001.SZ",
            "decision_point": "midday",
            "stage2_decision": {
                "decision": {
                    "order_type": "不下单",
                    "order_direction": None,
                    "entry_price": None,
                    "take_profit_price": None,
                    "stop_loss_price": None,
                    "estimated_win_rate": None,
                }
            },
        }
    )

    assert normalized["order_type"] == "不下单"
    assert normalized["should_trade"] is False
    pa = PAStage2Input.from_pa_payload(normalized)
    assert pa.order_type == "不下单"
    assert pa.should_trade is False
    assert pa.order_direction is None
    assert pa.entry_price is None
    assert pa.take_profit_price is None
    assert pa.stop_loss_price is None


def test_embedded_service_records_and_resolves_history(tmp_path) -> None:
    history_db = tmp_path / "history.db"

    class Result:
        def to_dict(self):
            return {
                "input": {
                    "symbol": "000001.SZ",
                    "decision_point": "close",
                    "materials": {"sector_analysis": {"sector_name": "半导体"}},
                },
                "completed_at": "2026-08-13T15:00:00",
            }

    class Orchestrator:
        def run(self, pa, *, context):
            return Result()

        def close(self):
            pass

    service = PAEmbeddedService(
        market_source=object(),
        model_client=object(),
        context_builder_factory=lambda _source: type(
            "Builder", (), {"build": lambda _self, _pa: object()}
        )(),
        orchestrator_factory=lambda _client, **_: Orchestrator(),
        history_database=history_db,
    )

    result = service.run_analysis({"symbol": "000001.SZ"})
    assert result["ok"] is True
    assert result["history_id"] == 1
    assert service.list_history("000001.SZ", database=history_db)[0]["actual_result"] is None
    assert service.resolve_history(1, "win", database=history_db) is True
    assert service.resolve_history(1, "win", database=history_db) is False
    summary = service.history_summary("000001.SZ", database=history_db)
    assert summary["status"] == "insufficient_data"
    assert summary["win_rate"] is None


def test_embedded_service_deletes_history(tmp_path) -> None:
    history_db = tmp_path / "history.db"

    class Result:
        def to_dict(self):
            return {
                "input": {
                    "symbol": "000001.SZ",
                    "decision_point": "close",
                    "materials": {"sector_analysis": {"sector_name": "半导体"}},
                },
                "completed_at": "2026-08-13T15:00:00",
            }

    class Orchestrator:
        def run(self, pa, *, context):
            return Result()

        def close(self):
            pass

    service = PAEmbeddedService(
        market_source=object(),
        model_client=object(),
        context_builder_factory=lambda _source: type(
            "Builder", (), {"build": lambda _self, _pa: object()}
        )(),
        orchestrator_factory=lambda _client, **_: Orchestrator(),
        history_database=history_db,
    )

    assert service.run_analysis({"symbol": "000001.SZ"})["history_id"] == 1
    assert service.list_history("000001.SZ", database=history_db)

    assert service.delete_history(1, database=history_db) is True
    assert service.list_history("000001.SZ", database=history_db) == []
    assert service.delete_history(1, database=history_db) is False


def test_history_does_not_depend_on_transient_position_quantities(tmp_path) -> None:
    history_db = tmp_path / "history.db"

    class Result:
        def to_dict(self):
            return {
                "input": {
                    "symbol": "000001.SZ",
                    "decision_point": "close",
                    "materials": {},
                },
                "completed_at": "2026-08-13T15:00:00",
            }

    class Orchestrator:
        def run(self, pa, *, context):
            return Result()

        def close(self):
            pass

    service = PAEmbeddedService(
        market_source=object(),
        model_client=object(),
        context_builder_factory=lambda _source: type(
            "Builder", (), {"build": lambda _self, _pa: object()}
        )(),
        orchestrator_factory=lambda _client, **_: Orchestrator(),
        history_database=history_db,
    )

    result = service.run_analysis(
        {
            "symbol": "000001.SZ",
            "sellable_quantity": 800,
            "today_locked_quantity": 300,
        }
    )

    assert result["ok"] is True
    saved = service.list_history("000001.SZ", database=history_db)[0]["result"]
    assert "position_snapshot" not in saved


def test_position_quantities_are_removed_from_the_production_handoff() -> None:
    normalized = normalize_pa_payload(
        {
            "symbol": "000001.SZ",
            "sellable_quantity": 800,
            "today_locked_quantity": 300,
        }
    )

    assert "sellable_quantity" not in normalized
    assert "today_locked_quantity" not in normalized


def test_embedded_service_returns_structured_error() -> None:
    model_client = type(
        "ModelClient",
        (),
        {"request_log": [{"request": "CycleModelOutput", "response": {"content": "bad"}}]},
    )()
    service = PAEmbeddedService(
        market_source=object(),
        model_client=model_client,
        context_builder_factory=lambda _source: (_ for _ in ()).throw(RuntimeError("context failed")),
        orchestrator_factory=lambda _client, **_: object(),
    )

    result = service.run_analysis({"symbol": "000001.SZ"})

    assert result == {
        "ok": False,
        "status": "error",
        "error": "context failed",
        "llm_trace": [
            {"request": "CycleModelOutput", "response": {"content": "bad"}}
        ],
    }


def test_service_runs_news_prefetch_and_exposes_cache_preview(tmp_path) -> None:
    source = type(
        "Source",
        (),
        {
            "search_news": lambda self, keyword: [
                NewsItem(
                    f"{keyword}更新",
                    "",
                    "https://example.test/news",
                    "2026-08-13 10:00:00",
                )
            ]
        },
    )()
    cache = DailyMaterialCache(tmp_path / "archives")
    service = PAEmbeddedService(
        market_source=source,
        model_client=object(),
        material_cache=cache,
    )

    result = service.prefetch_news(("半导体",))

    assert result["ok"] is True
    assert result["task"]["last_round"]["cached_sectors"] == ["半导体"]
    assert result["cache"]["categories"]["news"] == 1
    assert result["preview"]["news"]["半导体"][0].title == "半导体更新"


def test_service_can_search_by_stock_name_but_cache_by_sector(tmp_path) -> None:
    keywords: list[str] = []

    class Source:
        def search_news(self, keyword):
            keywords.append(keyword)
            return [NewsItem("summary", "embedded summary", "", "2026-08-13")]

    cache = DailyMaterialCache(tmp_path / "archives")
    service = PAEmbeddedService(
        market_source=Source(), model_client=object(), material_cache=cache
    )

    result = service.prefetch_news(
        ("liquor",), search_keywords={"liquor": "Kweichow Moutai"}
    )

    assert result["ok"] is True
    assert keywords == ["Kweichow Moutai"]
    assert cache.get("news", "liquor")[0].snippet == "embedded summary"


def test_news_prefetch_can_freeze_the_canonical_sector_registry(tmp_path) -> None:
    searched = []

    class Source:
        def search_news(self, keyword):
            searched.append(keyword)
            return []

    cache = DailyMaterialCache(tmp_path / "archives")
    service = PAEmbeddedService(
        market_source=Source(), model_client=object(), material_cache=cache
    )

    result = service.prefetch_news(
        ("半导体", "芯片", "银行"),
        sector_codes={
            "半导体": "US.SEMICONDUCTORS",
            "芯片": "US.SEMICONDUCTORS",
            "银行": "HK.HSI Constituent",
        },
    )

    assert result["ok"] is True
    assert set(cache.preview()["sector_registry"]) == {
        "US.SEMICONDUCTORS",
        "HK.HSI Constituent",
    }
    assert cache.get("sector_registry_meta", "current")["complete"] is True
    assert searched == ["半导体", "银行"]


def test_preanalysis_writes_subject_purpose_to_material_cache(tmp_path) -> None:
    class ModelClient:
        def __init__(self) -> None:
            self.requests = []

        def complete(self, request):
            self.requests.append(request)
            if request.response_schema.__name__ == "NewsEmotionModelOutput":
                payload = {
                    "items": [
                        {
                            "index": 0,
                            "sentiment_score": 0.6,
                            "relevance": 1.0,
                            "credibility": 0.85,
                        }
                    ]
                }
            else:
                payload = {
                    "message_type": "政策暖风",
                    "publisher": "政府监管",
                    "true_purpose": "产业扶持和市场托底",
                    "confidence": "高",
                    "signal_type": "分离均衡",
                    "key_evidence": ["补贴标准明确"],
                    "follow_up_expectation": "板块启动倾向增强",
                }
            return ModelResponse(
                output=request.response_schema.model_validate(payload),
                provider="fake",
                model="fake-model",
                usage={},
            )

    class Context:
        materials = {"news": {"status": "ready"}}
        game_signals = {}

    class Builder:
        def build(self, _pa):
            return Context()

    cache = DailyMaterialCache(tmp_path / "archives")
    cache.put(
        "news",
        "半导体",
        (NewsItem("产业支持政策发布", "明确补贴标准", "", "2026-08-14"),),
    )
    client = ModelClient()
    service = PAEmbeddedService(
        market_source=object(),
        model_client=client,
        context_builder_factory=lambda _source: Builder(),
        material_cache=cache,
    )

    result = service.prepare_materials(
        {
            "symbol": "000001.SZ",
            "sector_code": "HK.HSI Constituent",
            "sector_name": "半导体",
        }
    )

    assert result["ok"] is True
    cached = cache.get("subject_purpose", "半导体")
    assert cached["status"] == "ready"
    assert cached["true_purpose"] == "产业扶持和市场托底"
    assert cache.get("scored_news", "HK.HSI Constituent")["items"]
    assert result["preview"]["subject_purpose"]["半导体"] == cached
    assert "产业支持政策发布" in client.requests[0].user_prompt


def test_preanalysis_exposes_news_details_and_llm_trace(tmp_path) -> None:
    class ModelClient:
        def __init__(self) -> None:
            self.request_log: list[dict[str, Any]] = []

        def complete(self, request):
            self.request_log.append(
                {
                    "request": request.response_schema.__name__,
                    "prompt_files": list(request.prompt_sources),
                    "messages": [
                        {"role": "system", "content": request.system_prompt},
                        {"role": "user", "content": request.user_prompt},
                    ],
                }
            )
            if request.response_schema.__name__ == "NewsEmotionModelOutput":
                payload = {
                    "items": [
                        {
                            "index": 0,
                            "sentiment_score": 0.6,
                            "relevance": 1.0,
                            "credibility": 0.85,
                        }
                    ]
                }
            else:
                payload = {
                    "message_type": "政策暖风",
                    "publisher": "政府监管",
                    "true_purpose": "产业扶持和市场托底",
                    "confidence": "高",
                    "signal_type": "分离均衡",
                    "key_evidence": ["补贴标准明确"],
                    "follow_up_expectation": "板块启动倾向增强",
                }
            return ModelResponse(
                output=request.response_schema.model_validate(payload),
                provider="fake",
                model="fake-model",
                usage={},
            )

    class Context:
        materials = {"news": {"status": "ready"}}
        game_signals = {}

    class Builder:
        def build(self, _pa):
            return Context()

    cache = DailyMaterialCache(tmp_path / "archives")
    cache.put(
        "news",
        "半导体",
        (
            NewsItem(
                "产业支持政策发布",
                "明确补贴标准",
                "https://example.test/1",
                "2026-08-14",
                "新华社",
                ("SH.600519",),
            ),
        ),
    )
    client = ModelClient()
    service = PAEmbeddedService(
        market_source=object(),
        model_client=client,
        context_builder_factory=lambda _source: Builder(),
        material_cache=cache,
    )

    result = service.prepare_materials(
        {
            "symbol": "000001.SZ",
            "sector_code": "HK.HSI Constituent",
            "sector_name": "半导体",
        }
    )

    assert result["ok"] is True
    details = result["news_details"]["半导体"]
    assert details["sector_code"] == "HK.HSI Constituent"
    assert details["count"] == 1
    item = details["items"][0]
    assert item["title"] == "产业支持政策发布"
    assert item["url"] == "https://example.test/1"
    assert item["published_date"] == "2026-08-14 00:00"
    assert item["code"] == ["SH.600519"]
    assert item["source"] == "新华社"
    cached_score = cache.get("scored_news", "HK.HSI Constituent")["items"][0]
    assert item["sentiment_score"] == cached_score["sentiment_score"]
    assert item["sentiment_score"] != 0.6
    assert details["sentiment_sum"] == item["sentiment_score"]
    assert result["llm_trace"][0]["request"] == "SubjectPurposeModelOutput"
    assert any(
        "主体目的分析" in str(path) for path in result["llm_trace"][0]["prompt_files"]
    )


def test_decision_freeze_preserves_news_rows_and_weighted_sector_sum(tmp_path) -> None:
    cache = DailyMaterialCache(tmp_path / "archives")
    raw_news = (
        NewsItem(
            "产业支持政策发布",
            "明确补贴标准",
            "https://example.test/1",
            "2026-08-14",
            "新华社",
            ("SH.600519",),
        ),
        NewsItem(
            "行业需求短期承压",
            "终端需求低于预期",
            "https://example.test/2",
            "2026-08-14",
            "财联社",
            ("SZ.000001",),
        ),
    )
    cache.put("news", "半导体", raw_news)
    cache.put("sector_registry", "SH.BK0001", {"sector_name": "半导体"})
    scored_news = {
        "status": "ready",
        "sector_code": "SH.BK0001",
        "items": [
            {
                "title": "产业支持政策发布",
                "sentiment_score": 3.4,
                "relevance": 1.0,
                "validity": 1.0,
                "source_credibility": 0.85,
                "subject_purpose": "产业扶持",
            },
            {
                "title": "行业需求短期承压",
                "sentiment_score": -1.3,
                "relevance": 1.0,
                "validity": 1.0,
                "source_credibility": 0.65,
                "subject_purpose": "风险提示",
            },
        ],
    }
    cache.put("scored_news", "SH.BK0001", scored_news)
    service = PAEmbeddedService(
        market_source=object(),
        model_client=object(),
        material_cache=cache,
    )

    ProductionContextBuilder(object(), material_cache=cache)._freeze_materials(
        symbol="000001.SZ",
        sector_code="SH.BK0001",
        sector_name="半导体",
        market={},
        sector={},
        news={
            "status": "ready",
            "source": "daily_material_cache",
            "items": [
                {
                    "title": item.title,
                    "snippet": item.snippet,
                    "url": item.url,
                    "published_date": item.published_date,
                    "source": item.source,
                    "related_securities": list(item.related_securities),
                }
                for item in raw_news
            ],
        },
        scored_news=scored_news,
        subject_purpose={"true_purpose": "板块消息集合"},
        signals={},
    )

    details = service.material_cache_news()["半导体"]
    assert cache.get("news", "半导体") == raw_news
    assert details["count"] == 2
    assert [item["title"] for item in details["items"]] == [
        "产业支持政策发布",
        "行业需求短期承压",
    ]
    assert [item["sentiment_score"] for item in details["items"]] == [3.4, -1.3]
    assert [item["snippet"] for item in details["items"]] == [
        raw_news[0].snippet,
        raw_news[1].snippet,
    ]
    assert details["sentiment_sum"] == 2.1


def test_preanalysis_returns_provider_sector_lookup_error(tmp_path) -> None:
    class Source:
        def validate_sector_code(self, sector_code: str) -> None:
            raise RuntimeError(
                f"Futu OpenD 板块查询错误 {sector_code}: unknown plate code"
            )

    service = PAEmbeddedService(
        market_source=Source(),
        model_client=object(),
        material_cache=DailyMaterialCache(tmp_path / "archives"),
    )

    result = service.prepare_materials(
        {
            "symbol": "US.AAPL",
            "sector_code": "US.ANYTHING",
            "sector_name": "Test sector",
        }
    )

    assert result["ok"] is False
    assert result["status"] == "error"
    assert result["error"] == (
        "Futu OpenD 板块查询错误 US.ANYTHING: unknown plate code"
    )


def test_service_preanalysis_without_cached_news_does_not_run_llm(tmp_path) -> None:
    class Context:
        materials = {
            "news": {"status": "ready"},
            "sector_analysis": {"sector_name": "半导体"},
        }
        game_signals = {"bar_time": "2026-08-13 11:30:00"}

    class Builder:
        def build(self, _pa):
            return Context()

    cache = DailyMaterialCache(tmp_path / "archives")
    service = PAEmbeddedService(
        market_source=object(),
        model_client=object(),
        context_builder_factory=lambda _source: Builder(),
        material_cache=cache,
    )

    result = service.prepare_materials(
        {
            "symbol": "000001.SZ",
            "sector_name": "半导体",
            "decision_point": "midday",
            "should_trade": False,
        }
    )

    assert result == {
        "ok": True,
        "status": "ready",
        "materials": Context.materials,
        "game_signals": Context.game_signals,
        "cache": service.material_cache_status(),
        "preview": {},
        "news_details": {},
        "llm_trace": [],
    }


def test_run_reuses_context_prepared_for_the_same_symbol(tmp_path) -> None:
    builds: list[str] = []

    class Context:
        materials = {}
        game_signals = {}

    class Builder:
        def build(self, pa):
            builds.append(pa.symbol)
            return Context()

    class Result:
        def to_dict(self):
            return {"scenario_tree": {"branches": []}}

    class Orchestrator:
        def run(self, _pa, *, context):
            assert isinstance(context, Context)
            return Result()

        def close(self):
            pass

    service = PAEmbeddedService(
        market_source=object(),
        model_client=object(),
        context_builder_factory=lambda _source: Builder(),
        orchestrator_factory=lambda _client, **_: Orchestrator(),
        material_cache=DailyMaterialCache(tmp_path / "archives"),
        history_database=tmp_path / "history.db",
    )
    payload = {"symbol": "000001.SZ", "decision_point": "close"}

    assert service.prepare_materials(payload)["ok"] is True
    assert service.run_analysis(payload)["ok"] is True
    assert builds == ["000001.SZ"]


def test_run_discards_prepared_context_when_payload_changes(tmp_path) -> None:
    builds: list[str] = []

    class Context:
        materials = {}
        game_signals = {}

    class Builder:
        def build(self, pa):
            builds.append(pa.symbol)
            return Context()

    class Result:
        def to_dict(self):
            return {"scenario_tree": {"branches": []}}

    class Orchestrator:
        def run(self, _pa, *, context):
            return Result()

        def close(self):
            pass

    service = PAEmbeddedService(
        market_source=object(),
        model_client=object(),
        context_builder_factory=lambda _source: Builder(),
        orchestrator_factory=lambda _client, **_: Orchestrator(),
        material_cache=DailyMaterialCache(tmp_path / "archives"),
        history_database=tmp_path / "history.db",
    )
    service.prepare_materials({"symbol": "000001.SZ", "sector_name": "银行"})

    result = service.run_analysis({"symbol": "000001.SZ", "sector_name": "证券"})

    assert result["ok"] is True
    assert builds == ["000001.SZ", "000001.SZ"]


def test_embedded_service_persists_progress_events(tmp_path) -> None:
    from datetime import datetime

    from src.integration.progress import ProgressEvent, ProgressSink

    class Context:
        pass

    class Builder:
        def build(self, pa):
            return Context()

    class Result:
        def to_dict(self):
            return {
                "input": {
                    "symbol": "000001.SZ",
                    "decision_point": "close",
                    "materials": {"sector_analysis": {"sector_name": "半导体"}},
                },
                "completed_at": "2026-08-13T15:00:00",
            }

    class Orchestrator:
        def __init__(self, sink):
            self._sink = sink

        def run(self, _pa, *, context):
            self._sink.emit(
                ProgressEvent(
                    ts=datetime(2026, 8, 13, 15, 0, 0),
                    kind="stage",
                    stage="finish",
                    message="二阶推演完成",
                    source="orchestrator",
                )
            )
            return Result()

        def close(self):
            pass

    sink = ProgressSink()
    service = PAEmbeddedService(
        market_source=object(),
        model_client=object(),
        context_builder_factory=lambda _source: Builder(),
        orchestrator_factory=lambda _client, **_: Orchestrator(sink),
        progress_sink=sink,
        material_cache=DailyMaterialCache(tmp_path / "archives"),
        history_database=tmp_path / "history.db",
    )

    result = service.run_analysis({"symbol": "000001.SZ", "decision_point": "close"})

    assert result["ok"] is True
    events = result["result"].get("progress_events")
    assert isinstance(events, list) and events
    assert events[0]["kind"] == "stage"
    assert "二阶推演完成" in events[0]["message"]



def test_archive_materials_persists_the_day_snapshot(tmp_path) -> None:
    from datetime import datetime
    import json

    cache = DailyMaterialCache(
        tmp_path / "archives", clock=lambda: datetime(2026, 8, 14, 15, 30)
    )
    cache.put_many_and_snapshot(
        {"sector": {"半导体": {"sector_code": "SH.LIST0022"}}}
    )
    service = PAEmbeddedService(
        market_source=object(),
        model_client=object(),
        material_cache=cache,
    )

    result = service.archive_materials()

    assert result["ok"] is True
    archive_file = tmp_path / "archives" / "2026-08-14.json"
    assert archive_file.exists()
    payload = json.loads(archive_file.read_text(encoding="utf-8"))
    assert payload["trading_date"] == "2026-08-14"
    assert payload["materials"]["sector"]["半导体"]["sector_code"] == "SH.LIST0022"
    # Write-once: the second call reports the same archive.
    again = service.archive_materials()
    assert again.get("already_archived") is True


def test_archive_materials_waits_for_a_decision_snapshot(tmp_path) -> None:
    from datetime import datetime

    cache = DailyMaterialCache(
        tmp_path / "archives", clock=lambda: datetime(2026, 8, 14, 15, 30)
    )
    service = PAEmbeddedService(
        market_source=object(),
        model_client=object(),
        material_cache=cache,
    )

    result = service.archive_materials()

    assert result["ok"] is False
    assert result["reason"] == "no_decision_snapshot"
    assert not (tmp_path / "archives").exists()


def test_associated_sector_names_are_available_to_capital_flow_catchup(tmp_path) -> None:
    settings = tmp_path / "settings.json"
    settings.write_text(
        json.dumps(
            {
                "second_order": {
                    "symbol_preferences": {
                        "SZ.159732": {
                            "sector_code": "SH.LIST0022",
                            "sector_name": "半导体",
                        }
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    service = PAEmbeddedService(
        market_source=object(),
        model_client=object(),
        material_cache=DailyMaterialCache(tmp_path / "archives"),
        pa_settings_path=settings,
        labeler_catchup=False,
        capital_flow_catchup=False,
    )

    assert service._sector_capital_flow_names(("SH.LIST0022",)) == {
        "SH.LIST0022": "半导体"
    }
    assert service._sector_capital_flow_focus_codes(("SH.LIST0022",)) == {
        "SH.LIST0022": ("SZ.159732",)
    }


def test_archive_materials_refuses_before_market_close(tmp_path) -> None:
    from datetime import datetime

    cache = DailyMaterialCache(
        tmp_path / "archives", clock=lambda: datetime(2026, 8, 14, 14, 0)
    )
    cache.put_many_and_snapshot(
        {"sector": {"半导体": {"sector_code": "SH.LIST0022"}}}
    )
    service = PAEmbeddedService(
        market_source=object(),
        model_client=object(),
        material_cache=cache,
    )

    result = service.archive_materials()

    assert result["ok"] is False
    assert result["state"] == "pre_close"
    assert not (tmp_path / "archives").exists()


def test_auto_archive_triggers_from_the_lifecycle_poll(tmp_path) -> None:
    from datetime import datetime

    cache = DailyMaterialCache(
        tmp_path / "archives", clock=lambda: datetime(2026, 8, 14, 15, 30)
    )
    cache.put_many_and_snapshot(
        {"sector": {"半导体": {"sector_code": "SH.LIST0022"}}}
    )
    service = PAEmbeddedService(
        market_source=object(),
        model_client=object(),
        material_cache=cache,
        material_auto_archive=True,
    )

    status = service.material_cache_status()

    assert status["state"] == "archived"
    assert (tmp_path / "archives" / "2026-08-14.json").exists()
