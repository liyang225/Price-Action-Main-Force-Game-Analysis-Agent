from __future__ import annotations

import json
from pathlib import Path

from src.data.models import NewsItem
from src.integration.model_adapter import ModelResponse
from src.reasoning.prompt_router import load_prompt_router
from src.reasoning.subject_purpose_analyzer import SubjectPurposeAnalyzer


ROOT = Path(__file__).parents[1]


class RecordingModelClient:
    def __init__(self) -> None:
        self.requests = []

    def complete(self, request):
        self.requests.append(request)
        return ModelResponse(
            output=request.response_schema.model_validate(
                {
                    "message_type": "利好公告",
                    "publisher": "上市公司",
                    "true_purpose": "借利好出货",
                    "confidence": "高",
                    "signal_type": "混同均衡",
                    "key_evidence": ["消息发布时股价处于高位"],
                    "follow_up_expectation": "冲高回落",
                }
            ),
            provider="fake",
            model="fake-model",
            usage={},
        )


def test_subject_purpose_analyzer_uses_prompt_as_system_and_news_as_user() -> None:
    client = RecordingModelClient()
    router = load_prompt_router(
        ROOT / "config" / "prompt_routing.yaml", ROOT / "prompt_engine"
    )
    analyzer = SubjectPurposeAnalyzer(client, router)

    material = analyzer.analyze(
        "半导体",
        (
            NewsItem(
                title="公司公告获得大额订单",
                snippet="订单金额未披露",
                url="https://example.test/news",
                published_date="2026-08-14 10:00:00",
                source="交易所公告",
            ),
        ),
    )

    request = client.requests[0]
    prompt_path = router.common("主体目的分析")
    assert request.system_prompt == prompt_path.read_text(encoding="utf-8")
    assert request.prompt_sources == (str(prompt_path),)
    user_payload = json.loads(request.user_prompt)
    assert user_payload["sector_name"] == "半导体"
    assert user_payload["news_items"][0]["title"] == "公司公告获得大额订单"
    assert user_payload["news_items"][0]["snippet"] == "订单金额未披露"
    assert material["true_purpose"] == "借利好出货"
    assert material["news_count"] == 1
