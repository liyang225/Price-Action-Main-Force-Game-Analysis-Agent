from __future__ import annotations

from datetime import datetime

import pytest

from src.integration.progress import ProgressEvent, ProgressSink


def _event(message: str = "测试", kind: str = "info") -> ProgressEvent:
    return ProgressEvent(ts=datetime(2026, 8, 15, 16, 0, 0), message=message, kind=kind)


def test_progress_event_roundtrips_through_dict() -> None:
    event = ProgressEvent(
        ts=datetime(2026, 8, 15, 16, 0, 0),
        symbol="600519.SH",
        kind="stage",
        stage="model",
        message="主导参与者：主力",
        source="participant_classifier",
    )

    restored = ProgressEvent.from_dict(event.to_dict())

    assert restored == event
    assert restored.kind == "stage"
    assert restored.message == "主导参与者：主力"


def test_progress_event_rejects_unknown_kind() -> None:
    with pytest.raises(ValueError, match="kind"):
        ProgressEvent(ts=datetime.now(), kind="bogus")


def test_progress_event_rejects_non_datetime_ts() -> None:
    with pytest.raises(TypeError, match="ts"):
        ProgressEvent(ts="2026-08-15")  # type: ignore[arg-type]


def test_sink_emits_to_subscribers_and_records() -> None:
    sink = ProgressSink()
    received: list[ProgressEvent] = []
    sink.subscribe(received.append)
    event = _event("阶段 A", kind="stage")

    sink.emit(event)

    assert received == [event]
    assert sink.events() == [event]
    assert len(sink) == 1


def test_sink_reset_clears_recording_but_keeps_subscribers() -> None:
    sink = ProgressSink()
    received: list[ProgressEvent] = []
    sink.subscribe(received.append)
    sink.emit(_event("第一", kind="info"))

    sink.reset()

    assert sink.events() == []
    sink.emit(_event("第二", kind="info"))
    assert len(received) == 2


def test_sink_rejects_non_progress_event() -> None:
    with pytest.raises(TypeError, match="ProgressEvent"):
        ProgressSink().emit("not an event")  # type: ignore[arg-type]


def test_sink_rejects_non_callable_subscriber() -> None:
    with pytest.raises(TypeError, match="callable"):
        ProgressSink().subscribe("not callable")  # type: ignore[arg-type]


def test_sink_caps_recorded_events() -> None:
    sink = ProgressSink(max_events=3)
    for index in range(5):
        sink.emit(_event(f"事件 {index}"))

    assert len(sink.events()) == 3
    assert [event.message for event in sink.events()] == ["事件 2", "事件 3", "事件 4"]
