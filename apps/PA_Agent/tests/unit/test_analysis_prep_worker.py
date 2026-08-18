from __future__ import annotations

from types import SimpleNamespace

from pa_agent.gui.analysis_prep_worker import AnalysisPrepWorker


def test_forced_incremental_does_not_fall_back_when_anchor_is_missing(
    monkeypatch,
) -> None:
    import pa_agent.data.snapshot as snapshot
    import pa_agent.records.analysis_history as history

    previous = object()
    monkeypatch.setattr(snapshot, "build_display_frame", lambda *_args, **_kwargs: object())
    monkeypatch.setattr(history, "find_latest_successful_record", lambda **_kwargs: previous)
    monkeypatch.setattr(history, "compute_incremental_bar_delta", lambda *_args: None)

    worker = AnalysisPrepWorker(
        bars_raw=[object()],
        symbol="159732",
        timeframe="15m",
        bar_count=1,
        now_ms=1,
        force_incremental=True,
        incremental_threshold=10,
    )
    results: list[object] = []
    worker.ready.connect(results.append)

    worker.run()

    assert len(results) == 1
    assert results[0].previous_record is None
    assert results[0].incremental_new_bar_count is None


def test_forced_incremental_bypasses_automatic_new_bar_threshold(
    monkeypatch,
) -> None:
    import pa_agent.data.snapshot as snapshot
    import pa_agent.records.analysis_history as history

    previous = object()
    delta = SimpleNamespace(
        new_count=25,
        anchor_ts_open=100.0,
        new_bar_ts_opens=tuple(float(value) for value in range(25, 0, -1)),
    )
    monkeypatch.setattr(snapshot, "build_display_frame", lambda *_args, **_kwargs: object())
    monkeypatch.setattr(history, "find_latest_successful_record", lambda **_kwargs: previous)
    monkeypatch.setattr(history, "compute_incremental_bar_delta", lambda *_args: delta)
    monkeypatch.setattr(history, "format_bar_ts", lambda value: str(value))

    worker = AnalysisPrepWorker(
        bars_raw=[object()],
        symbol="159732",
        timeframe="15m",
        bar_count=1,
        now_ms=1,
        force_incremental=True,
        incremental_threshold=10,
    )
    results: list[object] = []
    worker.ready.connect(results.append)

    worker.run()

    assert len(results) == 1
    assert results[0].previous_record is previous
    assert results[0].incremental_new_bar_count == 25
