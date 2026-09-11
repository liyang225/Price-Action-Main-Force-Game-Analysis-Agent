from __future__ import annotations

import json
from typing import Any, Literal

import pytest

from src.integration.model_adapter import (
    FixedTimeoutModelClient,
    ModelIllegalEnumError,
    ModelProbabilityViolationError,
    ModelProviderError,
    ModelRequest,
    ModelResponse,
    ModelResponseFormatError,
    ModelResponseSchemaError,
    ModelTimeoutError,
    PAModelAdapter,
    PAChatClientAdapter,
    StrictModelOutput,
    StructuredModelClient,
)


class ParticipantDecision(StrictModelOutput):
    participant: Literal["主力", "散户"]
    behavior: Literal["建仓", "震仓", "拉升", "出货", "观望", "狩猎止损"]
    evidence: tuple[str, ...]


class FlexibleDecision(StrictModelOutput):
    participant: Literal["主力", "散户"]
    metadata: dict[str, Any]


class PAResponse:
    def __init__(
        self,
        content: str | None,
        *,
        provider: str = "openai",
        model: str = "openai/test-model",
        usage: dict[str, Any] | None = None,
    ) -> None:
        self.content = content
        self.provider = provider
        self.model = model
        self.usage = usage or {}


class FakePAClient:
    def __init__(
        self,
        response: PAResponse | None = None,
        *,
        failure: Exception | None = None,
    ) -> None:
        self.response = response
        self.failure = failure
        self.calls: list[dict[str, Any]] = []

    def call_text(
        self,
        messages: list[dict[str, Any]],
        *,
        timeout: float | None = None,
    ) -> PAResponse:
        self.calls.append({"messages": messages, "timeout": timeout})
        if self.failure is not None:
            raise self.failure
        assert self.response is not None
        return self.response


class FakeUsage:
    prompt_tokens = 10
    cached_prompt_tokens = 2
    completion_tokens = 8
    total_tokens = 18


class FakeChatClient:
    def __init__(self, content: str) -> None:
        self.content = content
        self.calls: list[dict[str, Any]] = []
        self._settings = type("Settings", (), {"model": "configured-model"})()

    def chat(self, messages, *, timeout_s):
        self.calls.append({"messages": messages, "timeout_s": timeout_s})
        return type("Reply", (), {"content": self.content, "usage": FakeUsage()})()


class FakeStreamChatClient:
    def __init__(self, content: str) -> None:
        self.content = content
        self.calls: list[dict[str, Any]] = []
        self._settings = type("Settings", (), {"model": "cursor-configured-model"})()

    def stream_chat(self, messages, **kwargs):
        self.calls.append({"messages": messages, **kwargs})
        return type("Reply", (), {"content": self.content, "usage": FakeUsage()})()


def _request() -> ModelRequest[ParticipantDecision]:
    return ModelRequest(
        system_prompt="只识别参与者行为。",
        user_prompt="根据冻结材料判断当前行为。",
        response_schema=ParticipantDecision,
        timeout_seconds=12.5,
    )


def test_pa_adapter_reuses_injected_client_and_returns_validated_output() -> None:
    pa_client = FakePAClient(
        PAResponse(
            '{"participant":"主力","behavior":"震仓","evidence":["放量回撤"]}',
            usage={"total_tokens": 42},
        )
    )
    adapter = PAModelAdapter(pa_client)

    response = adapter.complete(_request())

    assert isinstance(adapter, StructuredModelClient)
    assert response.output == ParticipantDecision(
        participant="主力", behavior="震仓", evidence=("放量回撤",)
    )
    assert response.provider == "openai"
    assert response.model == "openai/test-model"
    assert dict(response.usage) == {"total_tokens": 42}
    assert pa_client.calls[0]["timeout"] == 12.5
    messages = pa_client.calls[0]["messages"]
    assert [message["role"] for message in messages] == ["system", "user"]
    assert "ParticipantDecision" in messages[0]["content"]
    assert "只返回一个 JSON 对象" in messages[0]["content"]
    assert messages[1]["content"] == "根据冻结材料判断当前行为。"


def test_pa_adapter_records_exact_sent_messages_and_prompt_sources() -> None:
    pa_client = FakePAClient(
        PAResponse('{"participant":"主力","behavior":"震仓","evidence":["放量回撤"]}')
    )
    adapter = PAModelAdapter(pa_client)
    request = ModelRequest(
        system_prompt="只识别参与者行为。",
        user_prompt="根据冻结材料判断当前行为。",
        response_schema=ParticipantDecision,
        prompt_sources=(r"C:\prompts\参与者识别.txt",),
    )

    adapter.complete(request)

    trace = adapter.request_log[0]
    assert trace["request"] == "ParticipantDecision"
    assert trace["prompt_files"] == [r"C:\prompts\参与者识别.txt"]
    assert trace["messages"] == pa_client.calls[0]["messages"]
    assert "JSON Schema" in trace["messages"][0]["content"]
    assert trace["response"] == {
        "content": '{"participant":"主力","behavior":"震仓","evidence":["放量回撤"]}',
        "provider": "openai",
        "model": "openai/test-model",
        "usage": {},
    }
    assert trace["validated_output"]["behavior"] == "震仓"


def test_pa_chat_adapter_uses_only_the_current_request_messages() -> None:
    client = FakeChatClient(
        '{"participant":"主力","behavior":"震仓","evidence":["独立材料"]}'
    )
    adapter = PAChatClientAdapter(client, provider="PA_Agent.second_order")

    response = adapter.complete(_request())

    assert response.output.behavior == "震仓"
    assert response.provider == "PA_Agent.second_order"
    assert response.model == "configured-model"
    assert client.calls[0]["timeout_s"] == 12.5
    assert [item["role"] for item in client.calls[0]["messages"]] == ["system", "user"]
    assert all("PA 技术分析历史" not in item["content"] for item in client.calls[0]["messages"])


def test_pa_chat_adapter_supports_pa_cursor_stream_client_without_sharing_callbacks() -> None:
    client = FakeStreamChatClient(
        '{"participant":"主力","behavior":"震仓","evidence":["独立材料"]}'
    )
    adapter = PAChatClientAdapter(client, provider="PA_Agent.second_order")

    response = adapter.complete(_request())

    assert response.model == "cursor-configured-model"
    assert client.calls[0]["timeout_s"] == 12.5
    # The adapter always installs its own token hooks so it can capture the
    # streamed text; it never forwards the client's own callbacks through.
    assert callable(client.calls[0]["on_reasoning_token"])
    assert callable(client.calls[0]["on_content_token"])
    assert [item["role"] for item in client.calls[0]["messages"]] == ["system", "user"]


def test_pa_chat_adapter_forwards_streamed_tokens_to_token_callback() -> None:
    class StreamingClient(FakeStreamChatClient):
        def stream_chat(self, messages, **kwargs):
            self.calls.append({"messages": messages, **kwargs})
            kwargs["on_reasoning_token"]("思考")
            kwargs["on_reasoning_token"]("中")
            kwargs["on_content_token"]('{"participant":')
            kwargs["on_content_token"]('"主力","behavior":"震仓","evidence":[]}')
            return type("Reply", (), {"content": self.content, "usage": FakeUsage()})()

    tokens: list[tuple[str, str]] = []
    client = StreamingClient(
        '{"participant":"主力","behavior":"震仓","evidence":[]}'
    )
    adapter = PAChatClientAdapter(
        client,
        token_callback=lambda kind, chunk: tokens.append((kind, chunk)),
    )

    adapter.complete(_request())

    thinking = "".join(chunk for kind, chunk in tokens if kind == "thinking")
    content = "".join(chunk for kind, chunk in tokens if kind == "content")
    assert thinking == "思考中"
    assert content == '{"participant":"主力","behavior":"震仓","evidence":[]}'


def test_pa_chat_adapter_reports_stream_activity_without_changing_the_timeout() -> None:
    class ActiveStreamClient(FakeStreamChatClient):
        def stream_chat(self, messages, **kwargs):
            self.calls.append({"messages": messages, **kwargs})
            kwargs["on_reasoning_token"]("thinking")
            kwargs["on_content_token"]("content")
            return type("Reply", (), {"content": self.content, "usage": FakeUsage()})()

    activity: list[str] = []
    client = ActiveStreamClient(
        '{"participant":"主力","behavior":"震仓","evidence":["独立材料"]}'
    )

    response = PAChatClientAdapter(
        client,
        provider="PA_Agent.second_order",
        activity_callback=lambda: activity.append("token"),
    ).complete(_request())

    assert response.output.behavior == "震仓"
    assert client.calls[0]["timeout_s"] == 12.5
    assert activity == ["token", "token"]


def test_business_code_can_replace_pa_adapter_with_an_offline_fake() -> None:
    class OfflineFake:
        def complete(
            self, request: ModelRequest[ParticipantDecision]
        ) -> ModelResponse[ParticipantDecision]:
            return ModelResponse(
                output=request.response_schema.model_validate(
                    {
                        "participant": "散户",
                        "behavior": "观望",
                        "evidence": ["离线夹具"],
                    }
                ),
                provider="offline",
                model="fixture",
                usage={},
            )

    client: StructuredModelClient = OfflineFake()

    response = client.complete(_request())

    assert response.output.behavior == "观望"
    assert response.provider == "offline"


@pytest.mark.parametrize(
    "content",
    [
        "not json",
        "[]",
        "",
    ],
)
def test_invalid_json_is_an_explicit_format_failure(content: str) -> None:
    adapter = PAModelAdapter(FakePAClient(PAResponse(content)))

    with pytest.raises(ModelResponseFormatError):
        adapter.complete(_request())

    trace = adapter.request_log[0]
    assert trace["response"]["content"] == content
    assert trace["error"]["code"] == "invalid_response_format"
    assert trace["error"]["type"] == "ModelResponseFormatError"


@pytest.mark.parametrize(
    "content",
    [
        '```json\n{"participant":"主力","behavior":"建仓","evidence":["放量回撤"]}\n```',
        (
            '{"participant":"主力","behavior":"建仓","evidence":["放量回撤"]}\n\n'
            "情景树:\n- 超预期: 继续下探\n- 符合预期: 横盘震仓"
        ),
    ],
)
def test_wrapped_or_trailing_json_is_tolerated(content: str) -> None:
    adapter = PAModelAdapter(FakePAClient(PAResponse(content)))

    response = adapter.complete(_request())

    assert response.output.participant == "主力"
    assert response.output.behavior == "建仓"
    assert "retry" not in adapter.request_log[0]


def test_format_failure_retries_once_with_corrective_instruction() -> None:
    class QueuePAClient:
        def __init__(self) -> None:
            self.calls: list[dict[str, Any]] = []

        def call_text(self, messages, *, timeout=None):
            self.calls.append({"messages": messages, "timeout": timeout})
            if len(self.calls) == 1:
                return PAResponse("not json")
            return PAResponse(
                '{"participant":"主力","behavior":"建仓","evidence":["放量回撤"]}'
            )

    client = QueuePAClient()
    adapter = PAModelAdapter(client)

    response = adapter.complete(_request())

    assert response.output.participant == "主力"
    assert len(client.calls) == 2
    trace = adapter.request_log[0]
    assert "retry" in trace
    assert "error" not in trace
    assert trace["validated_output"]["behavior"] == "建仓"
    retry_system = client.calls[1]["messages"][0]["content"]
    assert "上一次的输出无法被解析" in retry_system


def test_illegal_fixed_enum_is_an_explicit_failure() -> None:
    adapter = PAModelAdapter(
        FakePAClient(
            PAResponse(
                '{"participant":"机构","behavior":"建仓","evidence":["大单流入"]}'
            )
        )
    )

    with pytest.raises(ModelIllegalEnumError, match="participant"):
        adapter.complete(_request())


def test_schema_extra_field_failure_retries_once_and_succeeds() -> None:
    """回归：模型在契约字段外附加说明/补充分析时，重试一次后成功。

    对应线上报错 "Extra inputs are not permitted"（participant_note /
    additional_analysis 未被 ParticipantModelOutput 声明）。
    """
    from src.reasoning.participant_classifier import ParticipantModelOutput

    class QueuePAClient:
        def __init__(self) -> None:
            self.calls: list[dict[str, Any]] = []

        def call_text(self, messages, *, timeout=None):
            self.calls.append({"messages": messages, "timeout": timeout})
            if len(self.calls) == 1:
                # 模型自由发挥：附加说明与补充分析两个未声明字段
                return PAResponse(
                    '{"participant":"主力","confidence":"中",'
                    '"behavior_candidates":["震仓"],'
                    '"key_evidence":["放量回撤"],"contra_evidence":[],'
                    '"participant_note":"当前处于震荡区间,主力一致性主导方。",'
                    '"additional_analysis":{"period_position":"区间震荡或理性跟随状态"}}'
                )
            return PAResponse(
                '{"participant":"主力","confidence":"中",'
                '"behavior_candidates":["震仓"],'
                '"key_evidence":["放量回撤"],"contra_evidence":[]}'
            )

    client = QueuePAClient()
    adapter = PAModelAdapter(client)

    response = adapter.complete(
        ModelRequest(
            system_prompt="识别主导参与者。",
            user_prompt="判断参与者。",
            response_schema=ParticipantModelOutput,
        )
    )

    assert response.output.participant == "主力"
    assert response.output.confidence == "中"
    assert len(client.calls) == 2
    trace = adapter.request_log[0]
    assert "retry" in trace
    assert "error" not in trace
    retry_system = client.calls[1]["messages"][0]["content"]
    assert "Schema 之外" in retry_system


def test_participant_confidence_field_matches_the_prompt_schema() -> None:
    from src.reasoning.participant_classifier import ParticipantModelOutput

    content = (
        '{"participant":"主力","confidence":"中",'
        '"behavior_candidates":["震仓"],"key_evidence":["放量回撤"],'
        '"contra_evidence":[]}'
    )
    adapter = PAModelAdapter(FakePAClient(PAResponse(content)))

    response = adapter.complete(
        ModelRequest(
            system_prompt="识别主导参与者。",
            user_prompt="判断参与者。",
            response_schema=ParticipantModelOutput,
        )
    )

    assert response.output.confidence == "中"
    assert adapter.request_log[0]["validated_output"]["confidence"] == "中"


@pytest.mark.parametrize(
    "metadata",
    [
        {"probability": 0.73},
        {"estimatedWinRate": 73},
        {"estimatedWinRate": "73"},
        {"prob": "0.73"},
        {"odds": 1.8},
        {"score": 0.73},
        {"note": "上涨概率 73%"},
    ],
)
def test_model_probability_numbers_are_rejected_before_business_use(
    metadata: dict[str, Any],
) -> None:
    request = ModelRequest(
        system_prompt="只输出离散结论。",
        user_prompt="判断参与者。",
        response_schema=FlexibleDecision,
    )
    content = '{"participant":"主力","metadata":' + json.dumps(
        metadata, ensure_ascii=False
    ) + "}"
    adapter = PAModelAdapter(FakePAClient(PAResponse(content)))

    with pytest.raises(ModelProbabilityViolationError, match="metadata"):
        adapter.complete(request)


def test_probability_language_is_allowed_when_it_is_only_quoted_evidence() -> None:
    from src.reasoning.participant_classifier import ParticipantModelOutput

    request = ModelRequest(
        system_prompt="识别主导参与者。",
        user_prompt="判断参与者。",
        response_schema=ParticipantModelOutput,
    )
    adapter = PAModelAdapter(
        FakePAClient(
            PAResponse(
                '{"participant":"主力","behavior_candidates":["建仓"],'
                '"key_evidence":["大单成交占比达到63%"],'
                '"contra_evidence":["技术分析曾给出上涨概率55%，仅作反证"]}'
            )
        )
    )

    response = adapter.complete(request)

    assert response.output.participant == "主力"
    assert "上涨概率55%" in response.output.contra_evidence[0]


def test_timeout_from_pa_client_is_not_swallowed() -> None:
    adapter = PAModelAdapter(
        FakePAClient(failure=TimeoutError("provider timeout")),
        timeout_backoff_seconds=0,
    )

    with pytest.raises(ModelTimeoutError, match="provider timeout"):
        adapter.complete(_request())

    trace = adapter.request_log[0]
    assert "response" not in trace
    assert trace["error"]["code"] == "timeout"


_VALID_DECISION = (
    '{"participant":"主力","behavior":"震仓","evidence":["放量回撤"]}'
)


class FlakyTimeoutPAClient:
    """Fails with ``TimeoutError`` for the first N calls, then succeeds."""

    def __init__(self, response: PAResponse | None = None, *, failures: int) -> None:
        self.response = response
        self.remaining_failures = failures
        self.calls: list[dict[str, Any]] = []

    def call_text(
        self,
        messages: list[dict[str, Any]],
        *,
        timeout: float | None = None,
    ) -> PAResponse:
        self.calls.append({"messages": messages, "timeout": timeout})
        if self.remaining_failures > 0:
            self.remaining_failures -= 1
            raise TimeoutError("provider timeout")
        assert self.response is not None
        return self.response


def test_timeout_is_retried_once_and_success_is_recorded() -> None:
    client = FlakyTimeoutPAClient(PAResponse(_VALID_DECISION), failures=1)
    adapter = PAModelAdapter(client, timeout_retries=1, timeout_backoff_seconds=0)

    response = adapter.complete(_request())

    assert response.output.participant == "主力"
    assert len(client.calls) == 2
    trace = adapter.request_log[0]
    assert trace["timeout_retries"] == [{"attempt": 1, "backoff_seconds": 0.0}]
    assert "error" not in trace
    assert trace["response"]["content"].startswith("{")


def test_timeout_retry_exhaustion_raises_with_failure_trace() -> None:
    client = FlakyTimeoutPAClient(failures=99)
    adapter = PAModelAdapter(client, timeout_retries=2, timeout_backoff_seconds=0)

    with pytest.raises(ModelTimeoutError):
        adapter.complete(_request())

    assert len(client.calls) == 3
    trace = adapter.request_log[0]
    assert [entry["attempt"] for entry in trace["timeout_retries"]] == [1, 2]
    assert trace["error"]["code"] == "timeout"


def test_timeout_retries_can_be_disabled() -> None:
    client = FlakyTimeoutPAClient(failures=99)
    adapter = PAModelAdapter(client, timeout_retries=0, timeout_backoff_seconds=0)

    with pytest.raises(ModelTimeoutError):
        adapter.complete(_request())

    assert len(client.calls) == 1


def test_chat_adapter_timeout_is_retried_once() -> None:
    class FlakyStreamClient:
        def __init__(self) -> None:
            self.calls = 0
            self._settings = type("Settings", (), {"model": "configured-model"})()

        def stream_chat(self, messages, **kwargs):
            self.calls += 1
            if self.calls == 1:
                raise TimeoutError("stream stalled")
            return type(
                "Reply",
                (),
                {"content": _VALID_DECISION, "usage": FakeUsage()},
            )()

    client = FlakyStreamClient()
    adapter = PAChatClientAdapter(client, timeout_retries=1, timeout_backoff_seconds=0)

    response = adapter.complete(_request())

    assert response.output.participant == "主力"
    assert client.calls == 2
    trace = adapter.request_log[0]
    assert trace["timeout_retries"] == [{"attempt": 1, "backoff_seconds": 0.0}]
    assert "error" not in trace


def test_timeout_retry_is_published_as_a_status_line_and_keeps_the_heartbeat() -> None:
    import time

    class StallingStreamClient:
        def __init__(self) -> None:
            self.calls = 0
            self._settings = type("Settings", (), {"model": "configured-model"})()

        def stream_chat(self, messages, **kwargs):
            self.calls += 1
            if self.calls == 1:
                kwargs["on_content_token"]("半截")
                time.sleep(0.09)  # let the throttle flush the aborted attempt
                kwargs["on_content_token"]("回答")
                raise TimeoutError("stream stalled")
            return type(
                "Reply",
                (),
                {"content": _VALID_DECISION, "usage": FakeUsage()},
            )()

    tokens: list[tuple[str, str]] = []
    activity: list[str] = []
    adapter = PAChatClientAdapter(
        StallingStreamClient(),
        timeout_retries=1,
        timeout_backoff_seconds=0,
        token_callback=lambda kind, chunk: tokens.append((kind, chunk)),
        activity_callback=lambda: activity.append("alive"),
    )

    adapter.complete(_request())

    # The notice must not arrive as reasoning/content text: the host renders
    # each kind as its own transcript block, so the aborted attempt's partial
    # answer is never glued onto the retried answer.
    assert tokens[-1] == ("info", "大模型响应超时，正在重试（第 1 次）…")
    assert ("content", "半截回答") in tokens
    # One heartbeat from the aborted attempt's flush, one from the retry.
    assert len(activity) == 2


def test_sensenova_gateway_uses_streaming_even_when_chat_exists() -> None:
    from src.reasoning.participant_classifier import ParticipantModelOutput

    class Settings:
        base_url = "https://token.sensenova.cn/v1"
        model = "deepseek-v4-flash"

    class Client:
        _settings = Settings()

        def __init__(self) -> None:
            self.chat_called = False
            self.stream_called = False

        def chat(self, *_args: Any, **_kwargs: Any) -> Any:
            self.chat_called = True
            raise AssertionError("SenseNova Token Plan must not receive non-stream chat")

        def stream_chat(self, *_args: Any, **_kwargs: Any) -> Any:
            self.stream_called = True
            return type(
                "Reply",
                (),
                {
                    "content": (
                        '{"participant":"主力","behavior_candidates":["建仓"],'
                        '"key_evidence":["资金持续流入"],"contra_evidence":[]}'
                    ),
                    "usage": None,
                },
            )()

    client = Client()

    response = PAChatClientAdapter(client).complete(
        ModelRequest(
            system_prompt="只输出离散结论。",
            user_prompt="判断参与者。",
            response_schema=ParticipantModelOutput,
        )
    )

    assert response.output.participant == "主力"
    assert client.stream_called is True
    assert client.chat_called is False


def test_timeout_returned_by_pa_fallback_is_normalized() -> None:
    adapter = PAModelAdapter(
        FakePAClient(
            PAResponse(
                "All LLM models failed. Last error: completion timed out",
                provider="error",
                model="",
            )
        )
    )

    with pytest.raises(ModelTimeoutError, match="timed out"):
        adapter.complete(_request())


def test_non_timeout_provider_failure_is_not_swallowed() -> None:
    adapter = PAModelAdapter(
        FakePAClient(PAResponse("backend not configured", provider="error", model=""))
    )

    with pytest.raises(ModelProviderError, match="backend not configured"):
        adapter.complete(_request())


def test_request_rejects_non_strict_schema_and_invalid_timeout() -> None:
    from pydantic import BaseModel

    class LooseOutput(BaseModel):
        label: str

    with pytest.raises(TypeError, match="StrictModelOutput"):
        ModelRequest(
            system_prompt="system",
            user_prompt="user",
            response_schema=LooseOutput,
        )
    with pytest.raises(ValueError, match="timeout_seconds"):
        ModelRequest(
            system_prompt="system",
            user_prompt="user",
            response_schema=ParticipantDecision,
            timeout_seconds=0,
        )


def test_fixed_timeout_model_client_pins_configured_wait_time() -> None:
    client = FakePAClient(PAResponse(_VALID_DECISION))
    adapter = PAModelAdapter(client)
    wrapper = FixedTimeoutModelClient(adapter, timeout_seconds=120)

    response = wrapper.complete(_request())  # request itself asks for 12.5s

    assert response.output.participant == "主力"
    assert client.calls[0]["timeout"] == 120.0
    assert wrapper.request_log == adapter.request_log


def test_fixed_timeout_model_client_validates_timeout() -> None:
    adapter = PAModelAdapter(FakePAClient(PAResponse("unused")))

    with pytest.raises(ValueError, match="timeout_seconds"):
        FixedTimeoutModelClient(adapter, timeout_seconds=0)
