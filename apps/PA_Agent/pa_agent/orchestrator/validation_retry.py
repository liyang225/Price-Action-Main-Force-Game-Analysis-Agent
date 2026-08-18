"""Validate model output with optional continuation retry."""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Callable, Literal

from pa_agent.ai.json_validator import Ok, ValidationError, coalesce_model_json_text
from pa_agent.ai.retry_feedback import build_retry_feedback, parse_previous_for_cheat
from pa_agent.ai.retry_policy import detect_cheat, extract_feedback_targets, should_retry

logger = logging.getLogger(__name__)

StageName = Literal["stage1", "stage2"]


@dataclass
class ValidationRetryResult:
    result: Ok | ValidationError
    messages: list[dict[str, Any]]
    reply: Any
    attempts: int
    cheat_detected: bool = False


def append_assistant_turn(
    messages: list[dict[str, Any]],
    reply: Any,
    *,
    provider_settings: Any | None = None,
) -> list[dict[str, Any]]:
    """Append the successful assistant reply to *messages* for audit completeness."""
    content = getattr(reply, "content", None) or ""
    if not content.strip():
        return messages
    if messages and messages[-1].get("role") == "assistant":
        if (messages[-1].get("content") or "").strip() == content.strip():
            return messages
    preserve_mimo = False
    if provider_settings is not None:
        from pa_agent.ai.mimo_compat import (
            build_assistant_api_message,
            is_mimo_provider,
        )

        preserve_mimo = is_mimo_provider(
            getattr(provider_settings, "base_url", ""),
            getattr(provider_settings, "model", ""),
        )
    if preserve_mimo:
        reasoning = getattr(reply, "reasoning_content", None) or ""
        assistant_msg = build_assistant_api_message(
            content,
            reasoning_content=reasoning,
        )
    else:
        assistant_msg = {"role": "assistant", "content": content}
    return messages + [assistant_msg]


def validate_with_retry(
    *,
    stage: StageName,
    messages: list[dict[str, Any]],
    reply: Any,
    validator: Any,
    validation_settings: Any,
    validate_kwargs: dict[str, Any],
    call_api: Callable[[list[dict[str, Any]]], Any],
    provider_settings: Any | None = None,
) -> ValidationRetryResult:
    """Validate *reply*; on retryable failure append feedback and re-call API."""
    max_attempts = int(getattr(validation_settings, "retry_max", 3) or 0)
    if not getattr(validation_settings, "retry_enabled", True):
        max_attempts = 0
    if stage == "stage2" and not getattr(validation_settings, "retry_stage2", True):
        max_attempts = 0

    current_messages = list(messages)
    current_reply = reply
    attempt = 0
    previous_raw: str | None = None
    previous_obj: dict[str, Any] | None = None
    previous_feedback_targets: set[str] = set()

    while True:
        content = coalesce_model_json_text(
            getattr(current_reply, "content", None) or "",
            getattr(current_reply, "reasoning_content", None) or "",
        )
        result = validator.validate(stage, content, **validate_kwargs)

        if isinstance(result, Ok):
            if attempt > 0 and previous_obj is not None:
                before_norm = validator.normalize_parsed(
                    stage,
                    previous_obj,
                    **validate_kwargs,
                )
                cheats = detect_cheat(
                    stage,
                    before_norm,
                    result.obj,
                    before_raw=previous_obj,
                    after_raw=parse_previous_for_cheat(content),
                    feedback_mentioned=previous_feedback_targets,
                )
                if cheats:
                    logger.warning(
                        "%s retry cheat detected after attempt %d: %s",
                        stage,
                        attempt,
                        "; ".join(cheats),
                    )
                    return ValidationRetryResult(
                        result=ValidationError(
                            category="c",
                            stage=stage,
                            raw_text=content,
                            message="重试后篡改了不可变字段: " + "; ".join(cheats),
                            invalid_fields=[f"cheat:{c}" for c in cheats],
                        ),
                        messages=current_messages,
                        reply=current_reply,
                        attempts=attempt + 1,
                        cheat_detected=True,
                    )
            return ValidationRetryResult(
                result=result,
                messages=append_assistant_turn(
                    current_messages,
                    current_reply,
                    provider_settings=provider_settings,
                ),
                reply=current_reply,
                attempts=attempt + 1,
            )

        err = result
        if not should_retry(
            err.category,
            err.invalid_fields,
            err.missing_fields,
            attempt=attempt,
            settings=validation_settings,
        ):
            return ValidationRetryResult(
                result=err,
                messages=current_messages,
                reply=current_reply,
                attempts=attempt + 1,
            )

        attempt += 1
        logger.info(
            "%s validation failed (category=%s), retry %d/%d",
            stage,
            err.category,
            attempt,
            max_attempts,
        )

        previous_raw = content
        previous_obj = parse_previous_for_cheat(previous_raw)
        previous_feedback_targets = extract_feedback_targets(
            err.invalid_fields,
            err.missing_fields,
        )

        feedback = build_retry_feedback(
            err,
            stage=stage,
            attempt=attempt,
            max_attempts=max_attempts,
            frame=validate_kwargs.get("kline_frame"),
            previous_raw=previous_raw,
        )
        preserve_mimo = False
        if provider_settings is not None:
            from pa_agent.ai.mimo_compat import (
                build_assistant_api_message,
                is_mimo_provider,
            )

            preserve_mimo = is_mimo_provider(
                getattr(provider_settings, "base_url", ""),
                getattr(provider_settings, "model", ""),
            )
        if preserve_mimo:
            reasoning = getattr(current_reply, "reasoning_content", None) or ""
            assistant_msg = build_assistant_api_message(
                content,
                reasoning_content=reasoning,
            )
        else:
            assistant_msg = {"role": "assistant", "content": content}
        current_messages = current_messages + [
            assistant_msg,
            {"role": "user", "content": feedback},
        ]
        current_reply = call_api(current_messages)
