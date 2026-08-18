from datetime import datetime
import json

import pytest

from src.integration.pa_link import (
    PAStage2Bridge,
    PAStage2Input,
    PAStage2Link,
    PAStage2Status,
    PAWorkspaceState,
    PADecisionPoint,
    PALinkMode,
    sanitize_pa_payload,
)


def _payload(**overrides):
    value = {
        "symbol": "000001.SZ",
        "decision_point": "midday",
        "should_trade": True,
        "entry_price": 10.2,
        "stop_loss_price": 9.8,
        "estimated_win_rate": 63,
        "technical_reason": "kept for PA display",
    }
    value.update(overrides)
    return value


def test_stage2_payload_adapts_trade_and_no_trade_nulls_without_mutating_pa_fields():
    result = PAStage2Input.from_pa_payload(
        _payload(should_trade=False, entry_price=None, stop_loss_price=None, estimated_win_rate=None)
    )
    assert result.should_trade is False
    assert result.entry_price is None
    assert result.estimated_win_rate is None
    assert result.payload["technical_reason"] == "kept for PA display"


def test_stage2_trade_plan_keeps_take_profit_and_direction_in_public_contract():
    result = PAStage2Input.from_pa_payload(
        _payload(
            order_direction="做空",
            take_profit_price=9.4,
        )
    )

    assert result.take_profit_price == 9.4
    assert result.order_direction == "做空"
    assert result.to_dict()["take_profit_price"] == 9.4


def test_bridge_removes_pa_prompt_and_conversation_internals_from_model_materials():
    marker = "PA-TECHNICAL-SYSTEM-PROMPT-MUST-NOT-CROSS"
    result = PAStage2Bridge().adapt(
        PAStage2Input.from_pa_payload(
            _payload(
                stage1_messages=[{"role": "system", "content": marker}],
                system_prompt=marker,
                analysis_record={"conversation_history": [marker]},
                stage1_diagnosis={"cycle_position": "启动", "summary": "结构化结论"},
            )
        )
    )

    serialized = json.dumps(result.to_dict(), ensure_ascii=False)
    assert marker not in serialized
    assert "结构化结论" in serialized


def test_pa_payload_sanitizer_removes_nested_credentials() -> None:
    payload = sanitize_pa_payload(
        {
            "symbol": "000001.SZ",
            "api_key": "secret-key",
            "nested": {
                "authorization": "Bearer secret",
                "cookie": "session=secret",
                "access_token": "secret-token",
                "price_history": [10.0, 10.5],
            },
        }
    )

    assert payload == {
        "symbol": "000001.SZ",
        "nested": {"price_history": [10.0, 10.5]},
    }


def test_t1_callback_runs_only_after_successful_stage2_and_preserves_timestamp():
    calls = []
    completed_at = datetime(2026, 8, 12, 11, 30)
    link = PAStage2Link(
        PALinkMode.T1,
        on_stage2_complete=lambda event: calls.append(event),
        clock=lambda: completed_at,
    )

    assert link.complete_stage2(_payload(), success=False) is None
    assert calls == []
    link.complete_stage2(_payload())
    assert len(calls) == 1
    assert calls[0].input.decision_point is PADecisionPoint.MIDDAY
    assert calls[0].completed_at == completed_at


def test_t0_mode_is_a_noop_even_when_callback_is_configured():
    calls = []
    link = PAStage2Link(PALinkMode.T0, on_stage2_complete=calls.append)
    assert link.complete_stage2(_payload()) is None
    assert calls == []


def test_t1_rejects_a_stage2_payload_from_the_wrong_decision_point():
    link = PAStage2Link(
        PALinkMode.T1,
        on_stage2_complete=lambda event: None,
        clock=lambda: datetime(2026, 8, 12, 15, 0),
    )
    with pytest.raises(ValueError, match="does not match scheduler"):
        link.complete_stage2(_payload(decision_point="midday"))


@pytest.mark.parametrize(
    ("moment", "expected"),
    [
        (datetime(2026, 8, 12, 11, 30), PADecisionPoint.MIDDAY),
        (datetime(2026, 8, 12, 14, 59), PADecisionPoint.MIDDAY),
        (datetime(2026, 8, 12, 15, 0), PADecisionPoint.CLOSE),
    ],
)
def test_decision_schedule_distinguishes_midday_and_close(moment, expected):
    assert PAStage2Link(clock=lambda: moment).decision_point() is expected


def test_decision_schedule_rejects_before_midday():
    with pytest.raises(ValueError, match="before the midday"):
        PAStage2Link(clock=lambda: datetime(2026, 8, 12, 10, 0)).decision_point()


def test_workspace_state_keeps_technical_tab_and_exposes_loading_result_error_states():
    state = PAWorkspaceState("000001.SZ")
    assert state.active_tab == "technical"
    assert state.select("second_order").active_tab == "second_order"
    assert state.loading().stage2_status is PAStage2Status.LOADING
    assert state.ready({"gate": False}).stage2_status is PAStage2Status.READY
    assert state.ready({"gate": False}).active_tab == "second_order"
    assert state.failed("provider timeout").stage2_status is PAStage2Status.ERROR
