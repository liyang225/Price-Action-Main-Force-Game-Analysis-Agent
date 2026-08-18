"""Public behavior tests for preview calculation and version history."""

from __future__ import annotations

from copy import deepcopy
from pathlib import Path

import pytest
import yaml

from src.config_validator import ConfigError
from src.gui.history import ConfigHistory
from src.gui.preview import build_preview
from src.gui.session import ConfigSession, RowRef
from src.labeler_constants import BEHAVIORS_BY_PARTICIPANT, CYCLE_STATES, PARTICIPANTS


CONFIG_PATH = Path(__file__).parents[1] / "config" / "hmm_prior.yaml"


@pytest.fixture
def config() -> dict:
    return yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8"))


def test_preview_reuses_the_production_filter_for_belief_and_both_participants(config) -> None:
    belief = {state: float(state == "冰点") for state in CYCLE_STATES}

    preview = build_preview(config, belief=belief, observation="高潮")

    assert set(preview.posterior_belief) == set(CYCLE_STATES)
    assert sum(preview.posterior_belief.values()) == pytest.approx(1.0)
    assert set(preview.behavior_distributions) == set(PARTICIPANTS)
    for participant, distribution in preview.behavior_distributions.items():
        assert set(distribution) == set(BEHAVIORS_BY_PARTICIPANT[participant])
        assert sum(distribution.values()) == pytest.approx(1.0)
    assert set(preview.heatmaps) == {"A", "C", "W · 主力", "W · 散户"}


def test_unsaved_A_and_W_edits_change_the_live_preview(config) -> None:
    belief = {state: float(state == "冰点") for state in CYCLE_STATES}
    baseline = build_preview(config, belief=belief, observation="高潮")
    edited = deepcopy(config)
    edited["transition_matrix"]["冰点"].update(
        {"冰点": 0.0, "启动": 0.0, "发酵": 0.0, "高潮": 1.0, "退潮": 0.0}
    )
    edited["behavior_mapping"]["高潮"]["主力"].update(
        {"建仓": 1.0, "震仓": 0.0, "拉升": 0.0, "出货": 0.0, "观望": 0.0, "狩猎止损": 0.0}
    )

    changed = build_preview(edited, belief=belief, observation="高潮")

    assert changed.posterior_belief != baseline.posterior_belief
    assert changed.behavior_distributions["主力"] != baseline.behavior_distributions["主力"]
    assert changed.behavior_distributions["主力"]["建仓"] == pytest.approx(1.0)


def test_invalid_draft_never_produces_a_preview(config) -> None:
    config["transition_matrix"]["冰点"]["启动"] = -0.1

    with pytest.raises(ConfigError, match="负数"):
        build_preview(config)


def test_valid_save_increments_version_and_records_comparable_snapshots(parameter_config_path: Path) -> None:
    config_path = parameter_config_path
    session = ConfigSession.from_file(config_path)
    session.set_beginner_percentage(RowRef("transition_matrix", "冰点"), "启动", 40)
    history = ConfigHistory(config_path)

    event = history.save(session)

    on_disk = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    snapshots = history.list_snapshots()
    assert event.version == 6
    assert on_disk["version"] == 6
    assert session.base_version == 6
    assert not session.is_dirty
    assert [snapshot.version for snapshot in snapshots] == [6, 5]
    assert "transition_matrix" in history.compare(snapshots[-1])


def test_invalid_save_leaves_the_disk_file_byte_for_byte_unchanged(parameter_config_path: Path) -> None:
    config_path = parameter_config_path
    original = config_path.read_bytes()
    session = ConfigSession.from_file(config_path)
    session.set_expert_value(RowRef("transition_matrix", "冰点"), "启动", -1)

    with pytest.raises(ConfigError):
        ConfigHistory(config_path).save(session)

    assert config_path.read_bytes() == original
    assert not (config_path.parent / "history" / "hmm_prior").exists()


def test_restore_is_validated_and_written_as_a_new_version_event(parameter_config_path: Path) -> None:
    config_path = parameter_config_path
    history = ConfigHistory(config_path)
    session = ConfigSession.from_file(config_path)
    original_startup = session.config["transition_matrix"]["冰点"]["启动"]
    session.set_beginner_percentage(RowRef("transition_matrix", "冰点"), "启动", 40)
    history.save(session)
    version_five = next(item for item in history.list_snapshots() if item.version == 5)

    event = history.restore(version_five, session)

    restored = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    assert event.version == 7
    assert event.action == "restore-v5"
    assert restored["version"] == 7
    assert restored["transition_matrix"]["冰点"]["启动"] == pytest.approx(original_startup)
    assert session.base_version == 7


def test_only_the_ten_most_recent_snapshots_are_retained(parameter_config_path: Path) -> None:
    config_path = parameter_config_path
    history = ConfigHistory(config_path, keep=10)
    session = ConfigSession.from_file(config_path)

    for percentage in range(36, 48):
        session.set_beginner_percentage(
            RowRef("transition_matrix", "冰点"), "启动", percentage
        )
        history.save(session)

    snapshots = history.list_snapshots()
    assert len(snapshots) == 10
    assert snapshots[0].version == 17
    assert snapshots[-1].version == 8
