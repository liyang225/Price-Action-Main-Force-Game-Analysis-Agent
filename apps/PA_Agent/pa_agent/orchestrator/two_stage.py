"""Two-stage AI analysis orchestrator.

Coordinates the full Stage 1 (diagnosis) → Stage 2 (decision) pipeline:
  1. Build Stage 1 prompt via PromptAssembler
  2. Call DeepSeekClient
  3. Validate Stage 1 JSON
  4. Route strategy files
  5. Load experience entries
  6. Build Stage 2 prompt
  7. Call DeepSeekClient
  8. Validate Stage 2 JSON
  9. Persist full record

Cancel checks are performed before each stage and after each API call.
Network/timeout errors are caught and recorded on the partial record.

On validation failure, ``validation_retry`` may append a feedback user turn and
re-call the API (see ``ValidationSettings.retry_*``). Semantic / safety errors
are not retried; immutable-field cheat detection rejects suspicious retries.
"""
from __future__ import annotations

# Legacy flag kept for tests/docs; retry is governed by ValidationSettings.
STAGE2_VALIDATION_AUTO_RETRY = False

import copy
import dataclasses
import json
import logging
import threading
from datetime import datetime
from typing import TYPE_CHECKING, Any, Callable, Optional

if TYPE_CHECKING:
    from pa_agent.ai.deepseek_client import DeepSeekClient
    from pa_agent.ai.json_validator import JsonValidator
    from pa_agent.ai.prompt_assembler import PromptAssembler
    from pa_agent.config.settings import Settings
    from pa_agent.records.experience_reader import ExperienceReader
    from pa_agent.records.pending_writer import PendingWriter

from pa_agent.ai.json_validator import Ok, ValidationError
from pa_agent.orchestrator.validation_retry import validate_with_retry
from pa_agent.data.base import KlineFrame
from pa_agent.records.schema import AnalysisRecord, RecordMeta
from pa_agent.util.threading import CancelToken, OrchestratorEvent
from pa_agent.util.timefmt import now_local_ms

logger = logging.getLogger(__name__)

_provider_switch_notification_lock = threading.Lock()
_provider_switch_notification_sent_for_process = False


def _claim_provider_switch_notification() -> bool:
    """Claim the single provider-switch notification slot for this process."""
    global _provider_switch_notification_sent_for_process
    with _provider_switch_notification_lock:
        if _provider_switch_notification_sent_for_process:
            return False
        _provider_switch_notification_sent_for_process = True
        return True


def _latency_ms_label(latency_ms: object) -> str:
    """Format API latency for console logs; tolerate mocks or missing values."""
    try:
        return f"{float(latency_ms):.0f}ms"
    except (TypeError, ValueError):
        return "?"

# When the gateway buffers the full reply, emit pseudo-stream chunks to the UI.
_FALLBACK_STREAM_CHUNK = 48


def _json_truncation_hint(content: str, err: ValidationError) -> str | None:
    """Detect incomplete JSON (stream stopped mid-object) vs a stray syntax typo."""
    if err.category != "a":
        return None
    stripped = (content or "").strip()
    if not stripped.startswith("{"):
        return None
    depth = 0
    in_string = False
    escape = False
    for ch in stripped:
        if in_string:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_string = False
            continue
        if ch == '"':
            in_string = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
    if depth > 0 or not stripped.rstrip().endswith("}"):
        return (
            f"阶段 JSON 正文约 {len(stripped)} 字符，在输出过程中被截断"
            f"（未闭合对象约 {max(depth, 1)} 层，解析位置 {err.parse_position}）。"
            " 常见原因：completion 额度主要在思考区用尽，正文 JSON 只写了一小段。"
        )
    return None


def _enrich_stage2_validation_message(err: ValidationError, reply: Any) -> str:
    """Add actionable context for empty content or truncated JSON."""
    from pa_agent.ai.provider_errors import (
        PROVIDER_QUOTA_USER_MESSAGE,
        is_provider_quota_exhausted,
    )

    if err.category == "e" or is_provider_quota_exhausted(err.raw_text):
        return err.message or PROVIDER_QUOTA_USER_MESSAGE
    from pa_agent.ai.validation_messages import format_validation_errors

    detail = format_validation_errors(
        err.invalid_fields, missing_fields=err.missing_fields
    )
    content = (getattr(reply, "content", None) or "").strip()
    trunc = _json_truncation_hint(content, err)
    if trunc:
        usage = getattr(reply, "usage", None)
        completion = getattr(usage, "completion_tokens", 0) if usage else 0
        reasoning_len = len(getattr(reply, "reasoning_content", None) or "")
        msg = f"{err.message}。{trunc} completion_tokens≈{completion}，思考区约 {reasoning_len} 字。"
        return f"{msg}。{detail}" if detail else msg
    if err.category != "d" or (err.raw_text or "").strip():
        return f"{err.message}。{detail}" if detail else err.message
    if content:
        return f"{err.message}。{detail}" if detail else err.message
    reasoning = getattr(reply, "reasoning_content", None) or ""
    usage = getattr(reply, "usage", None)
    completion = getattr(usage, "completion_tokens", 0) if usage else 0
    if reasoning and "{" not in reasoning:
        return (
            f"{err.message}：扩展思考已输出约 {len(reasoning)} 字，但正文 content 为空，"
            f"且思考中未见 JSON（completion_tokens≈{completion}）。"
            " 常见原因是思考在输出阶段二 JSON 前被截断或网关提前结束流。"
            " 请缩短 prompt、检查 Packy 分组限额，或调整模型/Reasoning Effort 后重新分析。"
        )
    if reasoning:
        return (
            f"{err.message}：正文 content 为空；请把阶段二 JSON 写在 content 正文，"
            "不要只写在思考区。"
        )
    return f"{err.message}。{detail}" if detail else err.message


def _enrich_stage1_validation_message(err: ValidationError, reply: Any) -> str:
    """Add actionable context for empty content or truncated JSON."""
    from pa_agent.ai.provider_errors import (
        PROVIDER_QUOTA_USER_MESSAGE,
        is_provider_quota_exhausted,
    )

    if err.category == "e" or is_provider_quota_exhausted(err.raw_text):
        return err.message or PROVIDER_QUOTA_USER_MESSAGE
    from pa_agent.ai.validation_messages import format_validation_errors

    detail = format_validation_errors(
        err.invalid_fields, missing_fields=err.missing_fields
    )
    content = (getattr(reply, "content", None) or "").strip()
    trunc = _json_truncation_hint(content, err)
    if trunc:
        usage = getattr(reply, "usage", None)
        completion = getattr(usage, "completion_tokens", 0) if usage else 0
        reasoning_len = len(getattr(reply, "reasoning_content", None) or "")
        msg = f"{err.message}。{trunc} completion_tokens≈{completion}，思考区约 {reasoning_len} 字。"
        return f"{msg}。{detail}" if detail else msg
    if err.category != "d" or (err.raw_text or "").strip():
        return f"{err.message}。{detail}" if detail else err.message
    if content:
        return f"{err.message}。{detail}" if detail else err.message
    reasoning = getattr(reply, "reasoning_content", None) or ""
    usage = getattr(reply, "usage", None)
    completion = getattr(usage, "completion_tokens", 0) if usage else 0
    if reasoning and "{" not in reasoning:
        return (
            f"{err.message}：扩展思考已输出约 {len(reasoning)} 字，但正文 content 为空，"
            f"且思考中未见 JSON（completion_tokens≈{completion}）。"
            " 常见原因是思考占满输出额度后被截断。"
            " 请缩短 prompt、检查网关输出上限，或调整 Reasoning Effort 后重新分析。"
        )
    if reasoning:
        return (
            f"{err.message}：正文 content 为空；请把阶段一 JSON 写在 content 正文，"
            "不要只写在思考区。"
        )
    return f"{err.message}。{detail}" if detail else err.message


def _emit_buffered_stream(
    text: str,
    on_token: Callable[[str], None] | None,
    *,
    chunk_size: int = _FALLBACK_STREAM_CHUNK,
) -> bool:
    """Push *text* through *on_token* in slices if the API did not stream deltas."""
    if on_token is None or not text:
        return False
    for i in range(0, len(text), chunk_size):
        on_token(text[i : i + chunk_size])
    return True


def _build_empty_record(
    frame: KlineFrame,
    settings: Optional["Settings"],
) -> AnalysisRecord:
    """Build a partial AnalysisRecord with meta populated from the frame."""
    ts_ms = now_local_ms()
    ts_iso = datetime.fromtimestamp(ts_ms / 1000).isoformat(timespec="milliseconds")

    # Build masked provider snapshot
    ai_provider: dict[str, Any] = {}
    if settings is not None:
        from pa_agent.util.mask_secret import mask_secret
        p = settings.provider
        ai_provider = {
            "model": p.model,
            "base_url": p.base_url,
            "api_key": mask_secret(p.api_key) if p.api_key else "****",
            "thinking": p.thinking,
            "reasoning_effort": p.reasoning_effort,
            "context_window": p.context_window,
        }

    # Serialize kline bars
    kline_data: list[dict] = []
    for bar in frame.bars:
        if dataclasses.is_dataclass(bar) and not isinstance(bar, type):
            kline_data.append(dataclasses.asdict(bar))
        else:
            kline_data.append(bar.__dict__)

    from pa_agent.ai.decision_stance import normalize_stance

    decision_stance = "conservative"
    if settings is not None:
        decision_stance = normalize_stance(
            getattr(settings.general, "decision_stance", "conservative")
        )

    meta = RecordMeta(
        timestamp_local_iso=ts_iso,
        timestamp_local_ms=ts_ms,
        symbol=frame.symbol,
        timeframe=frame.timeframe,
        bar_count=len(frame.bars),
        ai_provider=ai_provider,
        decision_stance=decision_stance,
    )

    return AnalysisRecord(
        meta=meta,
        kline_data=kline_data,
        htf_text="",
        stage1_messages=[],
        stage1_response=None,
        stage1_diagnosis=None,
        stage2_messages=[],
        stage2_response=None,
        stage2_decision=None,
        strategy_files_used=[],
        experience_loaded=[],
        exception=None,
        usage_total={},
    )


def _accumulate_usage(current: dict, reply_usage: Any) -> dict:
    """Merge an AIUsage object into the running usage_total dict."""
    result = dict(current)
    result["prompt_tokens"] = (
        result.get("prompt_tokens", 0) + getattr(reply_usage, "prompt_tokens", 0)
    )
    result["cached_prompt_tokens"] = (
        result.get("cached_prompt_tokens", 0)
        + getattr(reply_usage, "cached_prompt_tokens", 0)
    )
    result["completion_tokens"] = (
        result.get("completion_tokens", 0) + getattr(reply_usage, "completion_tokens", 0)
    )
    result["total_tokens"] = (
        result.get("total_tokens", 0) + getattr(reply_usage, "total_tokens", 0)
    )
    return result


def _accumulate_usage_calls(current: dict, usage_calls: list[Any]) -> dict:
    total = dict(current)
    for usage in usage_calls:
        if usage is not None:
            total = _accumulate_usage(total, usage)
    return total


class TwoStageOrchestrator:
    """Orchestrates the two-stage AI analysis pipeline.

    Parameters
    ----------
    client:
        DeepSeekClient instance for API calls.
    assembler:
        PromptAssembler for building Stage 1 and Stage 2 message lists.
    router:
        Either the ``route_strategy_files`` function or an object with a
        ``.route()`` method.
    validator:
        JsonValidator for validating Stage 1 and Stage 2 responses.
    pending_writer:
        PendingWriter for persisting full and partial records.
    exp_reader:
        ExperienceReader for loading top-5 experience entries.
    settings:
        Optional Settings object; used for ``ai_provider`` meta and
        ``reasoning_effort`` forwarding.
    """

    def __init__(
        self,
        client: "DeepSeekClient",
        assembler: "PromptAssembler",
        router: Any,
        validator: "JsonValidator",
        pending_writer: "PendingWriter",
        exp_reader: "ExperienceReader",
        settings: Optional["Settings"] = None,
    ) -> None:
        self._client = client
        self._assembler = assembler
        self._router = router
        self._validator = validator
        self._pending_writer = pending_writer
        self._exp_reader = exp_reader
        self._settings = settings
        self._active_provider = getattr(settings, "provider", None)
        # Tracks whether this analysis has already attempted the primary route.
        # It prevents stage 2 from restarting at the primary after stage 1 has
        # switched to the backup route.
        self._primary_attempted = False
        self._failover_switch_notification_sent = False
        self._failover_notification_sent = False

    def _validation_settings(self) -> Any:
        if self._settings is not None and hasattr(self._settings, "validation"):
            return self._settings.validation
        from pa_agent.config.settings import ValidationSettings

        return ValidationSettings()

    # ── Public API ────────────────────────────────────────────────────────────

    def submit(
        self,
        frame: KlineFrame,
        cancel_token: CancelToken,
        on_event: Callable[[OrchestratorEvent], None],
        *,
        on_stage1_reasoning: Callable[[str], None] | None = None,
        on_stage1_content: Callable[[str], None] | None = None,
        on_stage2_reasoning: Callable[[str], None] | None = None,
        on_stage2_content: Callable[[str], None] | None = None,
        on_stage_prompt: Callable[[str, str, str], None] | None = None,
        on_stage2_files: Callable[[list[str]], None] | None = None,
        previous_record: AnalysisRecord | None = None,
        incremental_new_bar_count: int | None = None,
    ) -> AnalysisRecord:
        """Run the two-stage analysis pipeline and return an AnalysisRecord.

        The ``on_event`` callback is called synchronously at each pipeline
        milestone.  The returned record is always fully populated with
        whatever data was collected before the pipeline terminated (whether
        by success, validation failure, cancellation, or network error).

        Parameters
        ----------
        frame:
            Immutable KlineFrame snapshot to analyse.
        cancel_token:
            Token checked before each stage and after each API call.
        on_event:
            Callback invoked with OrchestratorEvent values.

        Returns
        -------
        AnalysisRecord
            Fully or partially populated record.
        """
        # ── Step 1: Build partial record ──────────────────────────────────────
        record = _build_empty_record(frame, self._settings)

        # ── Step 2: Pre-Stage-1 cancel check ─────────────────────────────────
        if cancel_token.is_set():
            self._pending_writer.save_partial(record, "user_cancelled")
            on_event(OrchestratorEvent.Cancelled)
            return record

        # ── Step 2.5: Preflight data gate (before Stage1Started) ─────────────
        from pa_agent.ai.decision_nodes import check_preflight_data
        pf = check_preflight_data(frame)
        if not pf.ok:
            record = record.model_copy(update={
                "exception": {
                    "type": "insufficient_data",
                    "stage": "preflight",
                    "failed_check": pf.failed_check,
                    "message": pf.reason,
                }
            })
            self._pending_writer.save_partial(record, "insufficient_data")
            on_event(OrchestratorEvent.InsufficientData)
            return record

        # ── Step 3: Stage 1 started ───────────────────────────────────────────
        on_event(OrchestratorEvent.Stage1Started)

        # Resolve analysis mode from settings (default: original)
        analysis_mode = "original"
        if self._settings is not None:
            analysis_mode = str(
                getattr(self._settings.general, "analysis_mode", "original") or "original"
            )

        # ── Step 4: Build Stage 1 messages ───────────────────────────────────
        if previous_record is not None and incremental_new_bar_count is not None:
            messages_s1 = self._assembler.build_incremental_stage1(
                frame,
                previous_record,
                incremental_new_bar_count,
                analysis_mode=analysis_mode,
                provider_settings=getattr(self._settings, "provider", None),
            )
        else:
            messages_s1 = self._assembler.build_stage1(frame, analysis_mode=analysis_mode)

        # ── Step 5: Call AI for Stage 1 ───────────────────────────────────────
        logger.debug("\n" + "="*80)
        logger.debug("【Stage 1 发送的完整 Prompt】")
        logger.debug("="*80)
        for msg in messages_s1:
            role = msg.get("role", "?").upper()
            content = msg.get("content", "")
            logger.debug("\n--- [%s] ---\n%s", role, content)
        logger.debug("="*80 + "\n")

        # Notify conversation tab of the prompt being sent
        if on_stage_prompt is not None:
            s1_system = next((m.get("content", "") for m in messages_s1 if m.get("role") == "system"), "")
            s1_user = next((m.get("content", "") for m in messages_s1 if m.get("role") == "user"), "")
            on_stage_prompt("stage1", s1_system, s1_user)

        _thinking, _effort = self._thinking_params()
        s1_streamed_reasoning = False
        s1_streamed_content = False

        def _on_s1_reasoning(chunk: str) -> None:
            nonlocal s1_streamed_reasoning
            s1_streamed_reasoning = True
            if on_stage1_reasoning is not None:
                on_stage1_reasoning(chunk)

        def _on_s1_content(chunk: str) -> None:
            nonlocal s1_streamed_content
            s1_streamed_content = True
            if on_stage1_content is not None:
                on_stage1_content(chunk)

        try:
            reply_s1 = self._stream_chat_resilient(
                messages_s1,
                on_reasoning_token=_on_s1_reasoning,
                on_content_token=_on_s1_content,
                cancel_token=cancel_token,
                thinking=_thinking,
                reasoning_effort=_effort,
                stage_label="Stage 1",
            )
        except Exception as exc:
            if self._is_network_error(exc):
                logger.warning("Stage 1 network error: %s", exc)
                record = record.model_copy(
                    update={
                        "stage1_messages": messages_s1,
                        "exception": {
                            "type": "network_error",
                            "stage": "stage1",
                            "message": str(exc),
                        },
                    }
                )
                self._pending_writer.save_partial(record, "network_error")
                on_event(OrchestratorEvent.Stage1Failed)
                return record
            raise

        if not s1_streamed_reasoning and reply_s1.reasoning_content:
            _emit_buffered_stream(reply_s1.reasoning_content, on_stage1_reasoning)
        if not s1_streamed_content and reply_s1.content:
            _emit_buffered_stream(reply_s1.content, on_stage1_content)

        # ── Step 6: Post-Stage-1-call cancel check ────────────────────────────
        if cancel_token.is_set():
            record = record.model_copy(
                update={
                    "stage1_messages": messages_s1,
                    "stage1_response": reply_s1.raw,
                    "usage_total": _accumulate_usage(record.usage_total, reply_s1.usage),
                }
            )
            self._pending_writer.save_partial(record, "user_cancelled")
            on_event(OrchestratorEvent.Cancelled)
            return record

        # ── Step 7: Validate Stage 1 ──────────────────────────────────────────
        logger.debug("\n" + "="*80)
        logger.debug("【Stage 1 AI 完整响应】")
        logger.debug("="*80)
        logger.debug(reply_s1.content)
        if reply_s1.reasoning_content:
            logger.debug("\n--- [思考过程] ---\n%s", reply_s1.reasoning_content)
        logger.debug(
            "\n--- [Token 用量] prompt=%s completion=%s latency=%s ---",
            reply_s1.usage.prompt_tokens,
            reply_s1.usage.completion_tokens,
            _latency_ms_label(reply_s1.latency_ms),
        )
        logger.debug("="*80 + "\n")

        prev_s1: dict[str, Any] | None = None
        if previous_record is not None and int(incremental_new_bar_count or 0) > 0:
            prev_s1 = previous_record.stage1_diagnosis

        s1_usage_calls: list[Any] = [getattr(reply_s1, "usage", None)]

        def _call_s1_retry(msgs: list[dict]) -> Any:
            nonlocal s1_streamed_reasoning, s1_streamed_content
            on_event(OrchestratorEvent.Stage1Retry)
            s1_streamed_reasoning = False
            s1_streamed_content = False
            r = self._stream_chat_resilient(
                msgs,
                on_reasoning_token=_on_s1_reasoning,
                on_content_token=_on_s1_content,
                cancel_token=cancel_token,
                thinking=_thinking,
                reasoning_effort=_effort,
                stage_label="Stage 1",
            )
            if not s1_streamed_reasoning and r.reasoning_content:
                _emit_buffered_stream(r.reasoning_content, on_stage1_reasoning)
            if not s1_streamed_content and r.content:
                _emit_buffered_stream(r.content, on_stage1_content)
            s1_usage_calls.append(getattr(r, "usage", None))
            return r

        vr_s1 = validate_with_retry(
            stage="stage1",
            messages=messages_s1,
            reply=reply_s1,
            validator=self._validator,
            validation_settings=self._validation_settings(),
            validate_kwargs={
                "kline_frame": frame,
                "incremental_new_bar_count": int(incremental_new_bar_count or 0),
                "incremental_previous_stage1": prev_s1,
            },
            call_api=_call_s1_retry,
            provider_settings=getattr(self._settings, "provider", None),
        )
        messages_s1 = vr_s1.messages
        reply_s1 = vr_s1.reply
        result_s1 = vr_s1.result
        if vr_s1.attempts > 1:
            logger.info("Stage 1 validation succeeded after %d attempt(s)", vr_s1.attempts)

        if isinstance(result_s1, ValidationError):
            err = result_s1
            err_message = _enrich_stage1_validation_message(err, reply_s1)
            logger.warning(
                "Stage 1 validation failed: category=%s message=%s",
                err.category,
                err_message,
            )
            record = record.model_copy(
                update={
                    "stage1_messages": messages_s1,
                    "stage1_response": reply_s1.raw,
                    "usage_total": _accumulate_usage_calls(record.usage_total, s1_usage_calls),
                    "exception": {
                        "type": "provider_error" if err.category == "e" else "validation_error",
                        "stage": "stage1",
                        "category": err.category,
                        "message": err_message,
                        "missing_fields": err.missing_fields,
                        "invalid_fields": err.invalid_fields,
                        "raw_text": err.raw_text,
                        "parse_position": err.parse_position,
                        "validation_attempts": vr_s1.attempts,
                    },
                }
            )
            self._pending_writer.save_partial(record, f"stage1_{err.category}")
            on_event(OrchestratorEvent.Stage1Failed)
            return record

        # Validation passed — extract the parsed JSON
        assert isinstance(result_s1, Ok)
        stage1_json: dict = result_s1.obj

        # ── Step 9: Stage 1 done ──────────────────────────────────────────────
        on_event(OrchestratorEvent.Stage1Done)

        # ── Step 10: Route strategy files ─────────────────────────────────────
        if callable(self._router) and not hasattr(self._router, "route"):
            strategy_files: list[str] = self._router(stage1_json)
        else:
            strategy_files = self._router.route(stage1_json)

        # ── Step 11: Load experience entries ──────────────────────────────────
        cycle_position: str = stage1_json.get("cycle_position", "unknown")
        direction = str(stage1_json.get("direction", "") or "")
        patterns = stage1_json.get("detected_patterns") or []
        prompt_cfg = getattr(self._settings, "prompt", None) if self._settings else None
        max_exp = getattr(prompt_cfg, "experience_max_entries", 0) if prompt_cfg else 0
        max_chars = (
            getattr(prompt_cfg, "experience_max_chars_per_entry", 400) if prompt_cfg else 400
        )
        if max_exp <= 0:
            experience_entries = []
        elif hasattr(self._exp_reader, "read_for_stage2"):
            experience_entries = self._exp_reader.read_for_stage2(
                cycle_position,
                direction=direction,
                patterns=patterns,
                max_entries=max_exp,
                max_chars_per_entry=max_chars,
            )
        else:
            experience_entries = self._exp_reader.read_top5(cycle_position)[:max_exp]

        # ── Step 12: Pre-Stage-2 cancel check ────────────────────────────────
        if cancel_token.is_set():
            record = record.model_copy(
                update={
                    "stage1_messages": messages_s1,
                    "stage1_response": reply_s1.raw,
                    "stage1_diagnosis": stage1_json,
                    "strategy_files_used": strategy_files,
                    "experience_loaded": [
                        e.model_dump() if hasattr(e, "model_dump") else dict(e)
                        for e in experience_entries
                    ],
                    "usage_total": _accumulate_usage(record.usage_total, reply_s1.usage),
                }
            )
            self._pending_writer.save_partial(record, "user_cancelled")
            on_event(OrchestratorEvent.Cancelled)
            return record

        # ── Step 13: Stage 2 started ──────────────────────────────────────────
        on_event(OrchestratorEvent.Stage2Started)
        if on_stage2_files is not None:
            on_stage2_files(list(strategy_files))

        gate_result = str(stage1_json.get("gate_result", "proceed")).lower()
        if gate_result in ("wait", "unknown"):
            from pa_agent.ai.decision_tree import build_stage2_gate_wait_response

            if on_stage_prompt is not None:
                on_stage_prompt("stage2", "", "（阶段一闸门未通过，跳过阶段二模型调用）")
            short_msg = (
                f"阶段一 gate_result={gate_result}，程序已短路生成阶段二结果，"
                "未向模型发起请求。\n"
            )
            _emit_buffered_stream(short_msg, on_stage2_content)

            stage2_json = build_stage2_gate_wait_response(stage1_json)
            on_event(OrchestratorEvent.Stage2Done)
            logger.info("next_bar_prediction direction=null probs=null/null/null unpredictable=true (gate short-circuit)")
            usage_total = _accumulate_usage(record.usage_total, reply_s1.usage)
            record = record.model_copy(
                update={
                    "stage1_messages": messages_s1,
                    "stage1_response": reply_s1.raw,
                    "stage1_diagnosis": stage1_json,
                    "stage2_messages": [],
                    "stage2_response": None,
                    "stage2_decision": stage2_json,
                    "strategy_files_used": strategy_files,
                    "experience_loaded": [
                        e.model_dump() if hasattr(e, "model_dump") else dict(e)
                        for e in experience_entries
                    ],
                    "usage_total": usage_total,
                    "exception": None,
                }
            )
            self._pending_writer.save_full(record)
            on_event(OrchestratorEvent.RecordSaved)
            return record

        # ── Step 14: Build Stage 2 messages ───────────────────────────────────
        _enable_next_bar = bool(
            getattr(getattr(self._settings, "general", None), "enable_next_bar_prediction", False)
        )
        _flip_cooldown = int(
            getattr(
                getattr(self._settings, "general", None),
                "structure_flip_cooldown_bars",
                3,
            )
            or 3
        )
        messages_s2 = self._assembler.build_stage2_continuation(
            frame=frame,
            stage1_messages=messages_s1,
            stage1_reply_content=reply_s1.content,
            stage1_json=stage1_json,
            strategy_files=strategy_files,
            experience_entries=experience_entries,
            decision_stance=record.meta.decision_stance,
            previous_record=previous_record,
            enable_next_bar_prediction=_enable_next_bar,
            provider_settings=getattr(self._settings, "provider", None),
            structure_flip_cooldown_bars=_flip_cooldown,
        )

        # ── Step 15: Call AI for Stage 2 ──────────────────────────────────────
        logger.debug("\n" + "="*80)
        logger.debug("【Stage 2 发送的完整 Prompt】")
        logger.debug("="*80)
        for msg in messages_s2:
            role = msg.get("role", "?").upper()
            content = msg.get("content", "")
            logger.debug("\n--- [%s] ---\n%s", role, content)
        logger.debug("="*80 + "\n")

        # Notify conversation tab of the prompt being sent
        if on_stage_prompt is not None:
            s2_system = next((m.get("content", "") for m in messages_s2 if m.get("role") == "system"), "")
            s2_user = next((m.get("content", "") for m in reversed(messages_s2) if m.get("role") == "user"), "")
            on_stage_prompt("stage2", s2_system, s2_user)

        s2_streamed_reasoning = False
        s2_streamed_content = False

        def _on_s2_reasoning(chunk: str) -> None:
            nonlocal s2_streamed_reasoning
            s2_streamed_reasoning = True
            if on_stage2_reasoning is not None:
                on_stage2_reasoning(chunk)

        def _on_s2_content(chunk: str) -> None:
            nonlocal s2_streamed_content
            s2_streamed_content = True
            if on_stage2_content is not None:
                on_stage2_content(chunk)

        try:
            reply_s2 = self._stream_chat_resilient(
                messages_s2,
                on_reasoning_token=_on_s2_reasoning,
                on_content_token=_on_s2_content,
                cancel_token=cancel_token,
                thinking=_thinking,
                reasoning_effort=_effort,
                stage_label="Stage 2",
            )
        except Exception as exc:
            if self._is_network_error(exc):
                logger.warning("Stage 2 network error: %s", exc)
                record = record.model_copy(
                    update={
                        "stage1_messages": messages_s1,
                        "stage1_response": reply_s1.raw,
                        "stage1_diagnosis": stage1_json,
                        "stage2_messages": messages_s2,
                        "strategy_files_used": strategy_files,
                        "experience_loaded": [
                            e.model_dump() if hasattr(e, "model_dump") else dict(e)
                            for e in experience_entries
                        ],
                        "usage_total": _accumulate_usage(record.usage_total, reply_s1.usage),
                        "exception": {
                            "type": "network_error",
                            "stage": "stage2",
                            "message": str(exc),
                        },
                    }
                )
                self._pending_writer.save_partial(record, "network_error")
                on_event(OrchestratorEvent.Stage2Failed)
                return record
            raise

        if not s2_streamed_reasoning and reply_s2.reasoning_content:
            _emit_buffered_stream(reply_s2.reasoning_content, on_stage2_reasoning)
        if not s2_streamed_content and reply_s2.content:
            _emit_buffered_stream(reply_s2.content, on_stage2_content)

        # ── Step 16: Post-Stage-2-call cancel check ───────────────────────────
        if cancel_token.is_set():
            record = record.model_copy(
                update={
                    "stage1_messages": messages_s1,
                    "stage1_response": reply_s1.raw,
                    "stage1_diagnosis": stage1_json,
                    "stage2_messages": messages_s2,
                    "stage2_response": reply_s2.raw,
                    "strategy_files_used": strategy_files,
                    "experience_loaded": [
                        e.model_dump() if hasattr(e, "model_dump") else dict(e)
                        for e in experience_entries
                    ],
                    "usage_total": _accumulate_usage(
                        _accumulate_usage(record.usage_total, reply_s1.usage),
                        reply_s2.usage,
                    ),
                }
            )
            self._pending_writer.save_partial(record, "user_cancelled")
            on_event(OrchestratorEvent.Cancelled)
            return record

        # ── Step 17: Validate Stage 2 ─────────────────────────────────────────
        logger.debug("\n" + "="*80)
        logger.debug("【Stage 2 AI 完整响应】")
        logger.debug("="*80)
        logger.debug(reply_s2.content)
        if reply_s2.reasoning_content:
            logger.debug("\n--- [思考过程] ---\n%s", reply_s2.reasoning_content)
        logger.debug(
            "\n--- [Token 用量] prompt=%s completion=%s latency=%s ---",
            reply_s2.usage.prompt_tokens,
            reply_s2.usage.completion_tokens,
            _latency_ms_label(reply_s2.latency_ms),
        )
        logger.debug("="*80 + "\n")

        s2_usage_calls: list[Any] = [getattr(reply_s2, "usage", None)]

        def _call_s2_retry(msgs: list[dict]) -> Any:
            nonlocal s2_streamed_reasoning, s2_streamed_content
            on_event(OrchestratorEvent.Stage2Retry)
            s2_streamed_reasoning = False
            s2_streamed_content = False
            r = self._stream_chat_resilient(
                msgs,
                on_reasoning_token=_on_s2_reasoning,
                on_content_token=_on_s2_content,
                cancel_token=cancel_token,
                thinking=_thinking,
                reasoning_effort=_effort,
                stage_label="Stage 2",
            )
            if not s2_streamed_reasoning and r.reasoning_content:
                _emit_buffered_stream(r.reasoning_content, on_stage2_reasoning)
            if not s2_streamed_content and r.content:
                _emit_buffered_stream(r.content, on_stage2_content)
            s2_usage_calls.append(getattr(r, "usage", None))
            return r

        vr_s2 = validate_with_retry(
            stage="stage2",
            messages=messages_s2,
            reply=reply_s2,
            validator=self._validator,
            validation_settings=self._validation_settings(),
            validate_kwargs={
                "kline_frame": frame,
                "decision_stance": record.meta.decision_stance,
                "stage1_json": stage1_json,
                "skip_next_bar": not _enable_next_bar,
                "previous_record": previous_record,
                "structure_flip_cooldown_bars": _flip_cooldown,
            },
            call_api=_call_s2_retry,
            provider_settings=getattr(self._settings, "provider", None),
        )
        messages_s2 = vr_s2.messages
        reply_s2 = vr_s2.reply
        result_s2 = vr_s2.result
        if vr_s2.attempts > 1:
            logger.info("Stage 2 validation succeeded after %d attempt(s)", vr_s2.attempts)

        if isinstance(result_s2, ValidationError):
            err = result_s2
            err_message = _enrich_stage2_validation_message(err, reply_s2)
            logger.warning(
                "Stage 2 validation failed: category=%s message=%s",
                err.category,
                err_message,
            )
            record = record.model_copy(
                update={
                    "stage1_messages": messages_s1,
                    "stage1_response": reply_s1.raw,
                    "stage1_diagnosis": stage1_json,
                    "stage2_messages": messages_s2,
                    "stage2_response": reply_s2.raw,
                    "strategy_files_used": strategy_files,
                    "experience_loaded": [
                        e.model_dump() if hasattr(e, "model_dump") else dict(e)
                        for e in experience_entries
                    ],
                    "usage_total": _accumulate_usage_calls(
                        _accumulate_usage_calls(record.usage_total, s1_usage_calls),
                        s2_usage_calls,
                    ),
                    "exception": {
                        "type": "provider_error" if err.category == "e" else "validation_error",
                        "stage": "stage2",
                        "category": err.category,
                        "message": err_message,
                        "missing_fields": err.missing_fields,
                        "invalid_fields": err.invalid_fields,
                        "raw_text": err.raw_text,
                        "parse_position": err.parse_position,
                        "validation_attempts": vr_s2.attempts,
                    },
                }
            )
            self._pending_writer.save_partial(record, f"stage2_{err.category}")
            on_event(OrchestratorEvent.Stage2Failed)
            return record

        # Validation passed
        assert isinstance(result_s2, Ok)
        stage2_json: dict = result_s2.obj
        if not _enable_next_bar and isinstance(stage2_json, dict):
            stage2_json = copy.deepcopy(stage2_json)
            stage2_json.pop("next_bar_prediction", None)

        # ── Step 19: Stage 2 done ─────────────────────────────────────────────
        on_event(OrchestratorEvent.Stage2Done)

        # ── Step 19.5: Log next_bar_prediction (R9.3, NFR2.1) ───────────────────
        _pred = stage2_json if isinstance(stage2_json, dict) else {}
        _nb_pred = _pred.get("next_bar_prediction")
        if not _enable_next_bar:
            logger.info("next_bar_prediction omitted (feature disabled)")
        elif isinstance(_nb_pred, dict):
            if _nb_pred.get("unpredictable"):
                logger.info("next_bar_prediction direction=null probs=null/null/null unpredictable=true")
            else:
                _probs = _nb_pred.get("probabilities") or {}
                logger.info(
                    "next_bar_prediction direction=%s probs=%s/%s/%s unpredictable=false",
                    _nb_pred.get("direction"),
                    _probs.get("bullish"),
                    _probs.get("bearish"),
                    _probs.get("neutral"),
                )
        elif _enable_next_bar:
            logger.info("next_bar_prediction absent from stage2 response")

        _nc_pred = _pred.get("next_cycle_prediction")
        if isinstance(_nc_pred, dict):
            if _nc_pred.get("unpredictable"):
                logger.info("next_cycle_prediction cycle=null unpredictable=true")
            else:
                logger.info(
                    "next_cycle_prediction cycle=%s direction=%s unpredictable=false",
                    _nc_pred.get("cycle"),
                    _nc_pred.get("direction"),
                )
        else:
            logger.info("next_cycle_prediction absent from stage2 response")

        # ── Step 20: Build final record ───────────────────────────────────────
        usage_total = _accumulate_usage_calls(
            _accumulate_usage_calls(record.usage_total, s1_usage_calls),
            s2_usage_calls,
        )
        record = record.model_copy(
            update={
                "stage1_messages": messages_s1,
                "stage1_response": reply_s1.raw,
                "stage1_diagnosis": stage1_json,
                "stage2_messages": messages_s2,
                "stage2_response": reply_s2.raw,
                "stage2_decision": stage2_json,
                "strategy_files_used": strategy_files,
                "experience_loaded": [
                    e.model_dump() if hasattr(e, "model_dump") else dict(e)
                    for e in experience_entries
                ],
                "usage_total": usage_total,
                "exception": None,
            }
        )

        # ── Step 22: Persist full record ──────────────────────────────────────
        self._pending_writer.save_full(record)

        # ── Step 23: Record saved event ───────────────────────────────────────
        on_event(OrchestratorEvent.RecordSaved)

        # ── Step 24: Return ───────────────────────────────────────────────────
        return record

    # ── Internal helpers ──────────────────────────────────────────────────────

    def _thinking_params(self) -> tuple[bool, str]:
        """Return (thinking, reasoning_effort) from settings defaults."""
        provider = self._active_provider
        if provider is None and self._settings is not None:
            provider = self._settings.provider
        if provider is None:
            return True, "high"
        p = provider
        return p.thinking, p.reasoning_effort

    def _stream_chat_resilient(
        self,
        messages: list[dict[str, Any]],
        *,
        on_reasoning_token: Callable[[str], None] | None,
        on_content_token: Callable[[str], None] | None,
        cancel_token: CancelToken,
        thinking: bool,
        reasoning_effort: str,
        stage_label: str,
    ) -> Any:
        """Call stream_chat with the configured backup and legacy fallbacks."""
        original_model = (
            self._settings.provider.model if self._settings is not None else ""
        )
        tried_backup = False
        tried_qclaw = False
        tried_cursor = False
        tried_workbuddy = False
        active_thinking = thinking
        active_reasoning_effort = reasoning_effort
        using_backup = False

        if self._primary_route_disabled():
            tried_backup = True
            if not self._try_backup_fallback():
                error = RuntimeError("主线路已关闭，且备用线路未配置或不可用")
                self._notify_provider_failure(
                    stage_label=stage_label,
                    primary_error="主线路已关闭",
                    backup_error=str(error),
                )
                raise error
            using_backup = True
            self._notify_provider_switch(
                stage_label=stage_label,
                primary_error="主线路已被关闭（可能是此前 API 请求失败）",
            )
            active_thinking, active_reasoning_effort = self._thinking_params()
        else:
            self._primary_attempted = True

        while True:
            try:
                return self._client.stream_chat(
                    messages,
                    on_reasoning_token=on_reasoning_token,
                    on_content_token=on_content_token,
                    cancel_token=cancel_token,
                    thinking=active_thinking,
                    reasoning_effort=active_reasoning_effort,
                )
            except Exception as exc:
                if not self._is_provider_failure(exc):
                    raise
                if using_backup:
                    self._notify_provider_failure(
                        stage_label=stage_label,
                        primary_error="主线路此前已失败并被临时关闭",
                        backup_error=str(exc),
                    )
                    raise

                self._temporarily_disable_primary()
                if not tried_backup and self._try_backup_fallback():
                    tried_backup = True
                    using_backup = True
                    self._notify_provider_switch(
                        stage_label=stage_label,
                        primary_error=str(exc),
                    )
                    active_thinking, active_reasoning_effort = self._thinking_params()
                    logger.info(
                        "%s API error (%s); switched to configured backup provider",
                        stage_label,
                        exc,
                    )
                # Keep the built-in local routes as a fallback when no generic
                # backup is configured for their corresponding model alias.
                elif not tried_workbuddy and self._try_workbuddy_fallback(
                    original_model=original_model
                ):
                    tried_workbuddy = True
                    active_thinking, active_reasoning_effort = self._thinking_params()
                    logger.info(
                        "%s provider error (%s); applied WorkBuddy provider — retrying",
                        stage_label,
                        exc,
                    )
                elif not tried_cursor and self._try_cursor_fallback(
                    original_model=original_model
                ):
                    tried_cursor = True
                    active_thinking, active_reasoning_effort = self._thinking_params()
                    logger.info(
                        "%s provider error (%s); applied Cursor provider — retrying",
                        stage_label,
                        exc,
                    )
                elif not tried_qclaw and self._try_qclaw_fallback(
                    original_model=original_model
                ):
                    tried_qclaw = True
                    active_thinking, active_reasoning_effort = self._thinking_params()
                    logger.info(
                        "%s provider error (%s); applied QClaw provider — retrying",
                        stage_label,
                        exc,
                    )
                else:
                    self._notify_provider_failure(
                        stage_label=stage_label,
                        primary_error=str(exc),
                        backup_error="备用线路未配置或不可用",
                    )
                    raise

    def _primary_route_disabled(self) -> bool:
        settings = self._settings
        if settings is None:
            return False
        if not getattr(settings, "primary_provider_enabled", True):
            return True
        if not getattr(settings, "primary_provider_runtime_disabled", False):
            return False
        # A user-enabled retry starts the first request of a new analysis on
        # the primary again. Once that analysis has attempted it, later stages
        # stay on the route selected by the failover logic.
        return not (
            getattr(settings, "retry_primary_each_analysis", False)
            and not self._primary_attempted
        )

    def _temporarily_disable_primary(self) -> None:
        settings = self._settings
        if settings is not None:
            settings.primary_provider_runtime_disabled = True
            logger.warning("主线路 API 请求失败，本次运行临时关闭主线路")

    def _notify_provider_failure(
        self,
        *,
        stage_label: str,
        primary_error: str,
        backup_error: str,
    ) -> None:
        """Notify Feishu only after both configured routes are unavailable."""
        if self._failover_notification_sent:
            return
        self._failover_notification_sent = True
        settings = self._settings
        if settings is None:
            return
        primary_model = getattr(getattr(settings, "provider", None), "model", "")
        backup_model = getattr(getattr(settings, "backup_provider", None), "model", "")
        try:
            from pa_agent.notify.feishu_notifier import send_provider_failure_alert

            send_provider_failure_alert(
                primary_model=str(primary_model or "未配置"),
                backup_model=str(backup_model or "未配置"),
                stage=stage_label,
                primary_error=primary_error,
                backup_error=backup_error,
                settings=settings,
            )
        except Exception as notify_exc:  # noqa: BLE001
            logger.warning("备用线路故障通知发送失败: %s", notify_exc)

    def _notify_provider_switch(
        self,
        *,
        stage_label: str,
        primary_error: str,
    ) -> None:
        """Notify Feishu once when this analysis switches to its backup route."""
        settings = self._settings
        if settings is None:
            return
        if self._failover_switch_notification_sent:
            return
        self._failover_switch_notification_sent = True
        if not _claim_provider_switch_notification():
            return
        primary_model = getattr(getattr(settings, "provider", None), "model", "")
        backup_model = getattr(getattr(settings, "backup_provider", None), "model", "")
        try:
            from pa_agent.notify.feishu_notifier import send_provider_switch_alert

            send_provider_switch_alert(
                primary_model=str(primary_model or "未配置"),
                primary_error=primary_error,
                backup_model=str(backup_model or "未配置"),
                stage=stage_label,
                settings=settings,
            )
        except Exception as notify_exc:  # noqa: BLE001
            logger.warning("AI 线路切换通知发送失败: %s", notify_exc)

    def _try_backup_fallback(self) -> bool:
        """Switch this analysis to the configured backup provider once."""
        settings = self._settings
        if settings is None or not getattr(settings, "backup_provider_enabled", False):
            return False

        backup = getattr(settings, "backup_provider", None)
        if backup is None or not str(getattr(backup, "api_key", "") or "").strip():
            logger.warning("Backup provider is enabled but has no API key; skipping")
            return False

        try:
            from pa_agent.ai.client_factory import create_ai_client

            backup = backup.model_copy(deep=True)
            self._client = create_ai_client(backup, logger_=logger)
            self._active_provider = backup
            return True
        except Exception as exc:  # noqa: BLE001
            logger.warning("Unable to construct backup AI client: %s", exc)
            return False

    def _try_qclaw_fallback(self, *, original_model: str = "") -> bool:
        """Apply local QClaw provider (like settings Save with model=openclaw)."""
        from pa_agent.ai.qclaw_connector import (
            apply_qclaw_provider_to_settings,
            is_openclaw_model,
        )
        from pa_agent.config.paths import SETTINGS_JSON_PATH

        if not is_openclaw_model(original_model):
            return False
        if self._settings is None:
            return False

        from pa_agent.config.settings import save_settings
        from pa_agent.util.logging import update_api_key

        err = apply_qclaw_provider_to_settings(self._settings)
        if err:
            logger.warning("QClaw auto-fallback unavailable: %s", err)
            return False

        # The legacy fallback replaces the configured provider in-place, so
        # the runtime-disabled marker no longer refers to the active route.
        self._settings.primary_provider_runtime_disabled = False
        self._client.update_provider(self._settings.provider)
        self._active_provider = self._settings.provider
        try:
            save_settings(self._settings, SETTINGS_JSON_PATH)
            update_api_key(self._settings.provider.api_key)
        except Exception as save_exc:  # noqa: BLE001
            logger.warning("QClaw fallback applied but settings save failed: %s", save_exc)

        logger.info(
            "QClaw auto-fallback: model=%s base_url=%s",
            self._settings.provider.model,
            self._settings.provider.base_url,
        )
        return True

    def _try_cursor_fallback(self, *, original_model: str = "") -> bool:
        """Apply Cursor route via QClaw (like settings Save with model=openclaw_cs)."""
        from pa_agent.ai.cursor_connector import (
            apply_cursor_provider_to_settings,
            is_openclaw_cs_model,
        )
        from pa_agent.config.paths import SETTINGS_JSON_PATH

        if not is_openclaw_cs_model(original_model):
            return False
        if self._settings is None:
            return False

        from pa_agent.config.settings import save_settings
        from pa_agent.util.logging import update_api_key

        err = apply_cursor_provider_to_settings(
            self._settings,
            preferred_model=original_model,
        )
        if err:
            logger.warning("Cursor auto-fallback unavailable: %s", err)
            return False

        self._settings.primary_provider_runtime_disabled = False
        self._client.update_provider(self._settings.provider)
        self._active_provider = self._settings.provider
        try:
            save_settings(self._settings, SETTINGS_JSON_PATH)
            update_api_key(self._settings.provider.api_key)
        except Exception as save_exc:  # noqa: BLE001
            logger.warning("Cursor fallback applied but settings save failed: %s", save_exc)

        logger.info(
            "Cursor auto-fallback: model=%s base_url=%s",
            self._settings.provider.model,
            self._settings.provider.base_url,
        )
        return True

    def _try_workbuddy_fallback(self, *, original_model: str = "") -> bool:
        """Apply WorkBuddy provider (like settings Save with model=openclaw_wb)."""
        from pa_agent.ai.workbuddy_connector import (
            apply_workbuddy_provider_to_settings,
            is_openclaw_wb_model,
        )
        from pa_agent.config.paths import SETTINGS_JSON_PATH

        if not is_openclaw_wb_model(original_model):
            return False
        if self._settings is None:
            return False

        from pa_agent.config.settings import save_settings
        from pa_agent.util.logging import update_api_key

        err = apply_workbuddy_provider_to_settings(self._settings)
        if err:
            logger.warning("WorkBuddy auto-fallback unavailable: %s", err)
            return False

        self._settings.primary_provider_runtime_disabled = False
        self._client.update_provider(self._settings.provider)
        self._active_provider = self._settings.provider
        try:
            save_settings(self._settings, SETTINGS_JSON_PATH)
            update_api_key(self._settings.provider.api_key)
        except Exception as save_exc:  # noqa: BLE001
            logger.warning("WorkBuddy fallback applied but settings save failed: %s", save_exc)

        logger.info(
            "WorkBuddy auto-fallback: model=%s base_url=%s",
            self._settings.provider.model,
            self._settings.provider.base_url,
        )
        return True

    @staticmethod
    def _is_provider_failure(exc: Exception) -> bool:
        """Return True for route failures that are safe to retry elsewhere."""
        from pa_agent.ai.deepseek_client import CancelledError

        if isinstance(exc, CancelledError):
            return False
        # RuntimeError is used by the local SDK clients for unavailable
        # bridges/packages; API/network exceptions are covered below.
        return isinstance(exc, RuntimeError) or TwoStageOrchestrator._is_network_error(
            exc
        )

    @staticmethod
    def _is_network_error(exc: Exception) -> bool:
        """Return True if *exc* is a network/timeout error (SDK, httpx, or OS reset)."""
        from pa_agent.ai.deepseek_client import CancelledError

        if isinstance(exc, CancelledError):
            return False
        # Some OpenAI-compatible gateways return a truncated JSON body or SSE
        # frame. The SDK exposes that parser failure directly instead of
        # wrapping it in APIConnectionError, but it is still a provider-route
        # failure and should enter the same failover path.
        if isinstance(exc, json.JSONDecodeError):
            return True

        try:
            import openai  # type: ignore[import]

            if isinstance(
                exc,
                (
                    openai.APITimeoutError,
                    openai.APIConnectionError,
                    openai.APIStatusError,
                ),
            ):
                return True
        except ImportError:
            pass

        try:
            import httpx  # type: ignore[import]

            if isinstance(
                exc,
                (
                    httpx.ReadError,
                    httpx.ConnectError,
                    httpx.TimeoutException,
                    httpx.RemoteProtocolError,
                ),
            ):
                return True
        except ImportError:
            pass

        if isinstance(exc, (ConnectionResetError, ConnectionAbortedError, TimeoutError)):
            return True
        if isinstance(exc, OSError) and getattr(exc, "winerror", None) in (
            10054,  # WSAECONNRESET — remote host closed connection
            10053,  # WSAECONNABORTED
            10060,  # WSAETIMEDOUT
        ):
            return True

        cause = exc.__cause__
        if cause is not None and cause is not exc:
            return TwoStageOrchestrator._is_network_error(cause)
        return False
