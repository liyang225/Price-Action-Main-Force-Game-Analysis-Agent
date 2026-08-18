"""Structured, injectable boundary around PA's existing model client."""

from __future__ import annotations

import json
import re
import time
from copy import deepcopy
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from enum import Enum
from numbers import Real
from types import MappingProxyType
from typing import Any, Generic, Protocol, TypeVar, runtime_checkable

from pydantic import BaseModel, ConfigDict, ValidationError


OutputT = TypeVar("OutputT", bound="StrictModelOutput")


class ModelFailureCode(str, Enum):
    TIMEOUT = "timeout"
    PROVIDER_ERROR = "provider_error"
    INVALID_RESPONSE_FORMAT = "invalid_response_format"
    SCHEMA_VALIDATION_FAILED = "schema_validation_failed"
    ILLEGAL_ENUM = "illegal_enum"
    PROBABILITY_NUMBER_FORBIDDEN = "probability_number_forbidden"


class ModelAdapterError(RuntimeError):
    """Base class for failures crossing the model boundary."""

    code: ModelFailureCode

    def __init__(self, message: str) -> None:
        super().__init__(message)


class ModelTimeoutError(ModelAdapterError):
    code = ModelFailureCode.TIMEOUT


class ModelProviderError(ModelAdapterError):
    code = ModelFailureCode.PROVIDER_ERROR


class ModelResponseFormatError(ModelAdapterError):
    code = ModelFailureCode.INVALID_RESPONSE_FORMAT


class ModelResponseSchemaError(ModelAdapterError):
    code = ModelFailureCode.SCHEMA_VALIDATION_FAILED


class ModelIllegalEnumError(ModelResponseSchemaError):
    code = ModelFailureCode.ILLEGAL_ENUM


class ModelProbabilityViolationError(ModelResponseSchemaError):
    code = ModelFailureCode.PROBABILITY_NUMBER_FORBIDDEN


class StrictModelOutput(BaseModel):
    """Base for model outputs: immutable and closed to undeclared fields."""

    model_config = ConfigDict(extra="forbid", frozen=True)


@dataclass(frozen=True, slots=True)
class ModelRequest(Generic[OutputT]):
    """Provider-neutral request with an executable response contract."""

    system_prompt: str
    user_prompt: str
    response_schema: type[OutputT]
    timeout_seconds: float = 30.0
    prompt_sources: tuple[str, ...] = ()
    # Field names (by trailing path segment) whose numeric values are accepted
    # despite the default "no numeric model output" rule.  Reserved for the
    # LLM-scored news sentiment, whose score is a signed strength, not a
    # probability.  See ADR-0023.
    allow_numeric_fields: frozenset[str] = frozenset()

    def __post_init__(self) -> None:
        if not isinstance(self.system_prompt, str) or not self.system_prompt.strip():
            raise ValueError("system_prompt must be a non-empty string")
        if not isinstance(self.user_prompt, str) or not self.user_prompt.strip():
            raise ValueError("user_prompt must be a non-empty string")
        if not isinstance(self.response_schema, type) or not issubclass(
            self.response_schema, StrictModelOutput
        ):
            raise TypeError("response_schema must inherit StrictModelOutput")
        if (
            isinstance(self.timeout_seconds, bool)
            or not isinstance(self.timeout_seconds, Real)
            or self.timeout_seconds <= 0
        ):
            raise ValueError("timeout_seconds must be a positive number")
        if not isinstance(self.prompt_sources, tuple) or any(
            not isinstance(source, str) or not source.strip()
            for source in self.prompt_sources
        ):
            raise ValueError("prompt_sources must contain non-empty strings")
        if isinstance(self.allow_numeric_fields, str) or not isinstance(
            self.allow_numeric_fields, frozenset
        ):
            raise TypeError("allow_numeric_fields must be a frozenset of field names")
        if any(
            not isinstance(field, str) or not field.strip()
            for field in self.allow_numeric_fields
        ):
            raise ValueError("allow_numeric_fields entries must be non-empty strings")
        object.__setattr__(
            self,
            "allow_numeric_fields",
            frozenset(field.strip() for field in self.allow_numeric_fields),
        )


@dataclass(frozen=True, slots=True)
class ModelResponse(Generic[OutputT]):
    """Validated output plus non-business provider diagnostics."""

    output: OutputT
    provider: str
    model: str
    usage: Mapping[str, Any]

    def __post_init__(self) -> None:
        if not isinstance(self.output, StrictModelOutput):
            raise TypeError("output must inherit StrictModelOutput")
        if not isinstance(self.provider, str) or not self.provider.strip():
            raise ValueError("provider must be a non-empty string")
        if not isinstance(self.model, str) or not self.model.strip():
            raise ValueError("model must be a non-empty string")
        if not isinstance(self.usage, Mapping):
            raise TypeError("usage must be a mapping")
        object.__setattr__(self, "usage", MappingProxyType(dict(self.usage)))


@runtime_checkable
class StructuredModelClient(Protocol):
    """The only model boundary consumed by SecondOrderGame services."""

    def complete(self, request: ModelRequest[OutputT]) -> ModelResponse[OutputT]: ...


class PAModelClient(Protocol):
    """Narrow view of PA's existing LLMToolAdapter used by this project."""

    def call_text(
        self,
        messages: list[dict[str, Any]],
        *,
        timeout: float | None = None,
    ) -> Any: ...


class PAModelAdapter:
    """Adapt an already-configured PA client without owning provider setup."""

    def __init__(self, pa_client: PAModelClient) -> None:
        if not callable(getattr(pa_client, "call_text", None)):
            raise TypeError("pa_client must expose call_text(messages, timeout=...)")
        self._pa_client = pa_client
        self._request_log: list[dict[str, Any]] = []

    @property
    def request_log(self) -> list[dict[str, Any]]:
        return deepcopy(self._request_log)

    def _fetch(
        self,
        messages: list[dict[str, Any]],
        request: ModelRequest[OutputT],
        trace: dict[str, Any],
    ) -> tuple[Any, str, str, Any]:
        try:
            raw_response = self._pa_client.call_text(
                messages, timeout=float(request.timeout_seconds)
            )
        except Exception as exc:
            if _is_timeout(exc):
                error: ModelAdapterError = ModelTimeoutError(
                    str(exc) or "PA model client timed out"
                )
            else:
                error = ModelProviderError(str(exc) or type(exc).__name__)
            _record_trace_error(trace, error)
            raise error from exc
        content = getattr(raw_response, "content", None)
        provider = str(getattr(raw_response, "provider", "") or "")
        model = str(getattr(raw_response, "model", "") or "")
        usage = getattr(raw_response, "usage", {}) or {}
        return content, provider, model, usage

    def _decode(
        self,
        request: ModelRequest[OutputT],
        content: Any,
        provider: str,
        model: str,
        usage: Any,
    ) -> OutputT:
        output = _decode_payload(request, content)
        _reject_model_probability_numbers(
            output.model_dump(mode="json"),
            allowed_fields=request.allow_numeric_fields,
        )
        if not isinstance(usage, Mapping):
            raise ModelResponseFormatError("PA model response usage must be a mapping")
        if not provider.strip():
            raise ModelResponseFormatError("PA model response is missing provider metadata")
        if not model.strip():
            raise ModelResponseFormatError("PA model response is missing model metadata")
        return output

    def complete(self, request: ModelRequest[OutputT]) -> ModelResponse[OutputT]:
        if not isinstance(request, ModelRequest):
            raise TypeError("request must be a ModelRequest")

        messages = [
            {
                "role": "system",
                "content": _system_prompt_with_contract(request),
            },
            {"role": "user", "content": request.user_prompt},
        ]
        trace = _request_trace(request, messages)
        self._request_log.append(trace)

        content, provider, model, usage = self._fetch(messages, request, trace)
        trace["response"] = _response_trace(
            content=content,
            provider=provider,
            model=model,
            usage=usage,
        )
        _raise_on_provider_error(provider, content, trace)

        try:
            output = self._decode(request, content, provider, model, usage)
        except (ModelResponseFormatError, ModelResponseSchemaError):
            retry_messages = _with_retry_instruction(messages)
            content, provider, model, usage = self._fetch(retry_messages, request, trace)
            trace["retry"] = _response_trace(
                content=content,
                provider=provider,
                model=model,
                usage=usage,
            )
            _raise_on_provider_error(provider, content, trace)
            try:
                output = self._decode(request, content, provider, model, usage)
            except ModelAdapterError as exc:
                _record_trace_error(trace, exc)
                raise
        except ModelAdapterError as exc:
            _record_trace_error(trace, exc)
            raise
        trace["validated_output"] = output.model_dump(mode="json")
        return ModelResponse(
            output=output,
            provider=provider,
            model=model,
            usage=usage,
        )


class PAChatClientAdapter:
    """Adapt either PA live-client shape without duplicating provider settings."""

    def __init__(
        self,
        pa_client: Any,
        *,
        provider: str = "PA_Agent",
        activity_callback: Callable[[], None] | None = None,
        token_callback: Callable[[str, str], None] | None = None,
    ) -> None:
        if not any(
            callable(getattr(pa_client, method, None))
            for method in ("chat", "stream_chat")
        ):
            raise TypeError(
                "pa_client must expose chat(...) or stream_chat(...)"
            )
        if token_callback is not None and not callable(token_callback):
            raise TypeError("token_callback must be callable")
        self._pa_client = pa_client
        self._provider = provider
        self._activity_callback = activity_callback
        self._token_callback = token_callback
        self._request_log: list[dict[str, Any]] = []
        self._token_buf: list[tuple[str, str]] = []
        self._token_last_flush = 0.0

    @property
    def request_log(self) -> list[dict[str, Any]]:
        return deepcopy(self._request_log)

    def _on_token(self, kind: str, chunk: str) -> None:
        """Accumulate streamed chunks and flush them throttled to the token callback."""
        if not chunk:
            return
        if self._token_callback is None:
            # Preserve the legacy watchdog heartbeat when no text sink is wired.
            if self._activity_callback is not None:
                self._activity_callback()
            return
        self._token_buf.append((kind, chunk))
        if time.monotonic() - self._token_last_flush >= 0.08:
            self._flush_tokens()

    def _flush_tokens(self) -> None:
        if not self._token_buf:
            return
        items = self._token_buf
        self._token_buf = []
        self._token_last_flush = time.monotonic()
        merged: dict[str, str] = {}
        for kind, chunk in items:
            merged[kind] = merged.get(kind, "") + chunk
        for kind in ("thinking", "content"):
            text = merged.get(kind)
            if text:
                self._token_callback(kind, text)
        if self._activity_callback is not None:
            self._activity_callback()

    def _fetch(
        self,
        messages: list[dict[str, Any]],
        request: ModelRequest[OutputT],
        trace: dict[str, Any],
    ) -> tuple[Any, str, Any]:
        try:
            chat = getattr(self._pa_client, "chat", None)
            stream_chat = getattr(self._pa_client, "stream_chat", None)
            if callable(stream_chat):
                self._token_buf = []
                self._token_last_flush = time.monotonic()

                def on_reasoning(chunk: str) -> None:
                    self._on_token("thinking", chunk)

                def on_content(chunk: str) -> None:
                    self._on_token("content", chunk)

                reply = stream_chat(
                    messages,
                    on_reasoning_token=on_reasoning,
                    on_content_token=on_content,
                    timeout_s=float(request.timeout_seconds),
                )
                self._flush_tokens()
            elif callable(chat):
                reply = chat(messages, timeout_s=float(request.timeout_seconds))
            else:
                reply = stream_chat(
                    messages,
                    on_reasoning_token=None,
                    on_content_token=None,
                    timeout_s=float(request.timeout_seconds),
                )
        except Exception as exc:
            if _is_timeout(exc):
                error: ModelAdapterError = ModelTimeoutError(
                    str(exc) or "PA model client timed out"
                )
            else:
                error = ModelProviderError(str(exc) or type(exc).__name__)
            _record_trace_error(trace, error)
            raise error from exc

        usage_value = getattr(reply, "usage", None)
        usage = {
            name: int(getattr(usage_value, name, 0) or 0)
            for name in (
                "prompt_tokens",
                "cached_prompt_tokens",
                "completion_tokens",
                "total_tokens",
            )
        }
        settings = getattr(self._pa_client, "_settings", None)
        model = str(getattr(settings, "model", "") or "PA configured model")
        content = getattr(reply, "content", None)
        return content, model, usage

    def complete(self, request: ModelRequest[OutputT]) -> ModelResponse[OutputT]:
        messages = [
            {"role": "system", "content": _system_prompt_with_contract(request)},
            {"role": "user", "content": request.user_prompt},
        ]
        trace = _request_trace(request, messages)
        self._request_log.append(trace)

        content, model, usage = self._fetch(messages, request, trace)
        trace["response"] = _response_trace(
            content=content,
            provider=self._provider,
            model=model,
            usage=usage,
        )
        try:
            output = _decode_payload(request, content)
        except (ModelResponseFormatError, ModelResponseSchemaError):
            retry_messages = _with_retry_instruction(messages)
            content, model, usage = self._fetch(retry_messages, request, trace)
            trace["retry"] = _response_trace(
                content=content,
                provider=self._provider,
                model=model,
                usage=usage,
            )
            try:
                output = _decode_payload(request, content)
            except ModelAdapterError as exc:
                _record_trace_error(trace, exc)
                raise
        except ModelAdapterError as exc:
            _record_trace_error(trace, exc)
            raise
        trace["validated_output"] = output.model_dump(mode="json")
        return ModelResponse(
            output=output,
            provider=self._provider,
            model=model,
            usage=usage,
        )


def _requires_streaming(pa_client: Any) -> bool:
    """Return whether the configured gateway rejects non-stream chat calls."""
    settings = getattr(pa_client, "_settings", None)
    base_url = str(getattr(settings, "base_url", "") or "").casefold()
    return "sensenova.cn" in base_url


def _request_trace(
    request: ModelRequest[Any], messages: list[dict[str, Any]]
) -> dict[str, Any]:
    """Capture the exact provider-bound text for the embedded audit UI."""
    return {
        "request": request.response_schema.__name__,
        "prompt_files": list(request.prompt_sources),
        "messages": [dict(message) for message in messages],
    }


def _response_trace(
    *, content: Any, provider: str, model: str, usage: Any
) -> dict[str, Any]:
    """Capture the provider reply before JSON parsing or schema validation."""
    return {
        "content": content,
        "provider": provider,
        "model": model,
        "usage": dict(usage) if isinstance(usage, Mapping) else usage,
    }


def _record_trace_error(trace: dict[str, Any], error: ModelAdapterError) -> None:
    trace["error"] = {
        "type": type(error).__name__,
        "code": error.code.value,
        "message": str(error),
    }


def _raise_on_provider_error(
    provider: str, content: Any, trace: dict[str, Any]
) -> None:
    """Raise the normalized error when the provider reports a failure string."""
    if provider != "error":
        return
    message = str(content or "PA model client returned an unspecified error")
    if _message_is_timeout(message):
        error: ModelAdapterError = ModelTimeoutError(message)
    else:
        error = ModelProviderError(message)
    _record_trace_error(trace, error)
    raise error


def _system_prompt_with_contract(request: ModelRequest[Any]) -> str:
    schema = json.dumps(
        request.response_schema.model_json_schema(),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return (
        f"{request.system_prompt.rstrip()}\n\n"
        "只返回一个 JSON 对象，不要使用 Markdown 代码块或添加解释。"
        "所有离散字段必须使用 schema 中的固定枚举。"
        "不得输出 JSON Schema 未声明的任何额外字段。"
        "不得输出概率、胜率、置信度或似然数字。\n"
        f"JSON Schema ({request.response_schema.__name__}): {schema}"
    )


def _extract_first_json_object(text: str) -> str | None:
    """Return the first balanced ``{...}`` substring in *text*, or None.

    Walks from the first opening brace, tracking string literals so that
    braces inside JSON string values do not break the balance.  A well-formed
    object followed by trailing prose (the common ``Extra data`` failure) is
    recovered by returning only the object and dropping the tail.
    """
    start = text.find("{")
    if start == -1:
        return None
    depth = 0
    in_string = False
    escaped = False
    for index in range(start, len(text)):
        char = text[index]
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return text[start : index + 1]
    return None


def _parse_json_object(content: Any) -> dict[str, Any]:
    if not isinstance(content, str) or not content.strip():
        raise ModelResponseFormatError("model response content must be non-empty JSON")
    try:
        payload = json.loads(content)
    except json.JSONDecodeError as exc:
        first_object = _extract_first_json_object(content)
        if first_object is None:
            raise ModelResponseFormatError(
                f"model response is not a standalone JSON object: {exc.msg}"
            ) from exc
        try:
            payload = json.loads(first_object)
        except json.JSONDecodeError:
            raise ModelResponseFormatError(
                f"model response is not a standalone JSON object: {exc.msg}"
            ) from exc
    if not isinstance(payload, dict):
        raise ModelResponseFormatError("model response must be a top-level JSON object")
    return payload


def _decode_payload(request: ModelRequest[Any], content: Any) -> Any:
    """Parse raw model text and validate it against the request schema."""
    payload = _parse_json_object(content)
    _reject_model_probability_numbers(
        payload, allowed_fields=request.allow_numeric_fields
    )
    return _validate_output(request.response_schema, payload)


_RETRY_INSTRUCTION = (
    "你上一次的输出无法被解析为单个 JSON 对象或未通过 JSON Schema 校验。"
    "请严格只输出一个符合 JSON Schema 的 JSON 对象，"
    "不要包含任何 Schema 之外的字段，不要使用 Markdown 代码块，"
    "不要在对象之后追加任何文字、情景树或解释。"
)


def _with_retry_instruction(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Return a copy of *messages* with a corrective note appended to system."""
    corrected = deepcopy(messages)
    for message in corrected:
        if message.get("role") == "system":
            message["content"] = f"{message['content'].rstrip()}\n\n{_RETRY_INSTRUCTION}"
    return corrected


def _validate_output(
    schema: type[OutputT], payload: Mapping[str, Any]
) -> OutputT:
    try:
        return schema.model_validate(payload)
    except ValidationError as exc:
        enum_errors = [
            error for error in exc.errors() if error.get("type") in {"enum", "literal_error"}
        ]
        if enum_errors:
            paths = ", ".join(_location(error.get("loc", ())) for error in enum_errors)
            raise ModelIllegalEnumError(
                f"model response contains illegal fixed enum at {paths}"
            ) from exc
        raise ModelResponseSchemaError(
            f"model response failed {schema.__name__} validation: {exc}"
        ) from exc


def _reject_model_probability_numbers(
    payload: Mapping[str, Any], *, allowed_fields: frozenset[str] = frozenset()
) -> None:
    def visit(value: Any, path: tuple[str, ...]) -> None:
        if isinstance(value, bool):
            return
        if isinstance(value, Real):
            if path and str(path[-1]) in allowed_fields:
                return
            raise ModelProbabilityViolationError(
                "model response contains a forbidden numeric value at "
                + (_location(path))
            )
        if isinstance(value, str):
            if _path_is_evidence_text(path):
                return
            if _contains_probability_text(value) or (
                _path_indicates_probability(path) and _is_numeric_text(value)
            ):
                raise ModelProbabilityViolationError(
                    "model response contains a forbidden probability number at "
                    + (_location(path))
                )
        if isinstance(value, Mapping):
            for key, child in value.items():
                key_text = str(key)
                child_path = (*path, key_text)
                visit(child, child_path)
        elif isinstance(value, Sequence) and not isinstance(value, str | bytes):
            for index, child in enumerate(value):
                visit(child, (*path, str(index)))

    visit(payload, ())


_PROBABILITY_TERM = (
    r"(?:prob(?:ability|abilities)?|chance|likelihood|confidence|odds|"
    r"win[\s_-]*rate|prior|posterior|概率|胜率|置信度|可能性|似然|赔率|先验|后验)"
)
_NUMBER = r"(?:[+-]?(?:\d+(?:\.\d+)?|\.\d+)\s*%?)"
_PROBABILITY_TEXT_RE = re.compile(
    rf"(?:{_PROBABILITY_TERM}.{{0,16}}?{_NUMBER}|{_NUMBER}.{{0,16}}?{_PROBABILITY_TERM})",
    re.IGNORECASE,
)
_NUMERIC_TEXT_RE = re.compile(rf"^\s*{_NUMBER}\s*$")
_PROBABILITY_PATH_MARKERS = (
    "probability",
    "chance",
    "likelihood",
    "confidence",
    "odds",
    "winrate",
    "prior",
    "posterior",
    "概率",
    "胜率",
    "置信度",
    "可能性",
    "似然",
    "赔率",
    "先验",
    "后验",
)


def _contains_probability_text(value: str) -> bool:
    return bool(_PROBABILITY_TEXT_RE.search(value))


def _path_indicates_probability(path: Sequence[str]) -> bool:
    for part in path:
        normalized = re.sub(r"[\s_-]+", "", part).casefold()
        if normalized.startswith("prob") or any(
            marker in normalized for marker in _PROBABILITY_PATH_MARKERS
        ):
            return True
    return False


def _path_is_evidence_text(path: Sequence[str]) -> bool:
    """Evidence may quote numeric market facts without becoming a probability input."""
    return any(
        re.sub(r"[\s_-]+", "", part).casefold()
        in {"evidence", "keyevidence", "contraevidence"}
        for part in path
    )


def _is_numeric_text(value: str) -> bool:
    return bool(_NUMERIC_TEXT_RE.fullmatch(value))


def _location(location: Sequence[Any]) -> str:
    return ".".join(str(part) for part in location) or "<root>"


def _is_timeout(exc: Exception) -> bool:
    return isinstance(exc, TimeoutError) or "timeout" in type(exc).__name__.casefold()


def _message_is_timeout(message: str) -> bool:
    normalized = message.casefold()
    return "timeout" in normalized or "timed out" in normalized


__all__ = [
    "ModelAdapterError",
    "ModelFailureCode",
    "ModelIllegalEnumError",
    "ModelProbabilityViolationError",
    "ModelProviderError",
    "ModelRequest",
    "ModelResponse",
    "ModelResponseFormatError",
    "ModelResponseSchemaError",
    "ModelTimeoutError",
    "PAModelAdapter",
    "PAModelClient",
    "PAChatClientAdapter",
    "StrictModelOutput",
    "StructuredModelClient",
]
