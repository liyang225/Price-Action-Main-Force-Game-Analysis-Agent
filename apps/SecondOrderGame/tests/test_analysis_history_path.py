from __future__ import annotations

import sqlite3

import src.integration.analysis_history as history_module
from src.integration.analysis_history import AnalysisHistoryStore


def test_default_history_store_migrates_legacy_database_to_dedicated_folder(
    tmp_path, monkeypatch
) -> None:
    legacy = tmp_path / "runtime" / "second_order_history.db"
    target = tmp_path / "analysis_history" / "second_order_history.db"
    legacy.parent.mkdir(parents=True)
    connection = sqlite3.connect(legacy)
    connection.execute("CREATE TABLE marker (value TEXT)")
    connection.execute("INSERT INTO marker VALUES ('legacy')")
    connection.commit()
    connection.close()
    monkeypatch.setattr(history_module, "DEFAULT_HISTORY_DB", target)
    monkeypatch.setattr(history_module, "LEGACY_HISTORY_DB", legacy)

    store = AnalysisHistoryStore()
    store.close()

    assert target.is_file()
    connection = sqlite3.connect(target)
    assert connection.execute("SELECT value FROM marker").fetchone()[0] == "legacy"
    connection.close()
