"""Tests for shared live-input prompt snippets."""
from __future__ import annotations

from pa_agent.records.prompt_library import PromptLibraryStore


def test_prompt_library_persists_edit_and_delete(tmp_path):
    path = tmp_path / "prompt_library.json"
    store = PromptLibraryStore(path)
    created = store.add(name="复盘", text="请复盘当前走势。")

    changed = store.update(created.id, name="详细复盘", text="请详细复盘当前走势。")
    assert changed is not None
    assert PromptLibraryStore(path).items == (changed,)
    assert store.remove(created.id) == changed
    assert PromptLibraryStore(path).items == ()
