"""Public behavior tests for the unsaved HMM configuration session."""

from __future__ import annotations

from copy import deepcopy
from pathlib import Path

import pytest
import yaml

from src.gui.session import ConfigSession, RowRef


CONFIG_PATH = Path(__file__).parents[1] / "config" / "hmm_prior.yaml"


def test_opening_and_discarding_a_session_never_changes_the_config_file(parameter_config_path: Path) -> None:
    config_path = parameter_config_path
    original = config_path.read_bytes()
    session = ConfigSession.from_file(config_path)

    session.set_beginner_percentage(
        RowRef("transition_matrix", "冰点"), "启动", 60
    )
    assert session.is_dirty

    session.discard()

    assert not session.is_dirty
    assert config_path.read_bytes() == original
    assert session.config["transition_matrix"]["冰点"]["启动"] == pytest.approx(0.35)


def test_beginner_edit_rebalances_the_row_and_preserves_zero_or_positive_values() -> None:
    session = ConfigSession.from_file(CONFIG_PATH)
    row_ref = RowRef("transition_matrix", "冰点")

    session.set_beginner_percentage(row_ref, "启动", 100)
    row = session.config["transition_matrix"]["冰点"]

    assert row["启动"] == pytest.approx(1.0)
    assert sum(value for key, value in row.items() if key != "alpha") == pytest.approx(1.0)
    assert all(value >= 0 for key, value in row.items() if key != "alpha")
    assert session.is_valid


def test_confidence_choices_map_to_hidden_legal_alpha_values() -> None:
    session = ConfigSession.from_file(CONFIG_PATH)
    row_ref = RowRef("behavior_mapping", "启动", "主力")

    values = [session.set_confidence(row_ref, label) for label in ("弱", "中", "强")]

    assert values == [3.0, 8.0, 24.0]
    assert session.config["behavior_mapping"]["启动"]["主力"]["alpha"] == 24.0
    assert session.is_valid


def test_invalid_expert_edit_keeps_a_separate_last_valid_configuration() -> None:
    session = ConfigSession.from_file(CONFIG_PATH)
    valid_before = deepcopy(session.last_valid_config)

    session.set_expert_value(
        RowRef("transition_matrix", "冰点"), "启动", -0.1
    )

    assert not session.is_valid
    assert "负数" in (session.validation_error or "")
    assert session.config["transition_matrix"]["冰点"]["启动"] == -0.1
    assert session.last_valid_config == valid_before


def test_mode_switching_is_view_state_only_and_never_loses_unsaved_changes() -> None:
    session = ConfigSession.from_file(CONFIG_PATH)
    row_ref = RowRef("confusion_matrix", "true_启动")
    session.set_beginner_percentage(row_ref, "llm_启动", 70)

    beginner_view = session.config
    expert_view = session.config

    assert expert_view == beginner_view
    assert expert_view["confusion_matrix"]["true_启动"]["llm_启动"] == pytest.approx(0.70)


def test_unknown_rows_and_non_finite_values_are_rejected_without_mutation() -> None:
    session = ConfigSession.from_file(CONFIG_PATH)
    before = session.config

    with pytest.raises(KeyError):
        session.set_expert_value(RowRef("transition_matrix", "不存在"), "启动", 0.5)
    with pytest.raises(ValueError, match="有限数字"):
        session.set_expert_value(RowRef("transition_matrix", "冰点"), "启动", float("nan"))

    assert session.config == before


def test_session_can_be_created_from_a_mapping_without_sharing_mutable_state() -> None:
    source = yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8"))
    session = ConfigSession(source)

    session.set_beginner_percentage(RowRef("transition_matrix", "冰点"), "启动", 60)

    assert source["transition_matrix"]["冰点"]["启动"] == pytest.approx(0.35)
