"""Tests for the OHLCV-only sector-rule validation pack."""

from __future__ import annotations

import importlib.util
from pathlib import Path
import sys

import numpy as np
import pandas as pd
import pytest
import yaml

from src.data.fake_client import FakeMarketDataSource
from src.data.models import Bar
from src.data.protocol import DataSourceError


SCRIPT = Path(__file__).parents[1] / ".scratch" / "sector-labeler-validation" / "validate_sector_rules.py"
SPEC = importlib.util.spec_from_file_location("validate_sector_rules", SCRIPT)
assert SPEC and SPEC.loader
validation = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = validation
SPEC.loader.exec_module(validation)


def _bars(days: int = 800, *, start: str = "2024-01-01") -> list[Bar]:
    dates = pd.bdate_range(start, periods=days)
    close = 100 + np.sin(np.arange(days) / 7) * 8 + np.arange(days) * 0.02
    volume = 1_000_000 + (np.sin(np.arange(days) / 5) * 400_000).astype(int)
    return [
        Bar(str(day.date()), float(price - 0.5), float(price + 1), float(price - 1), float(price), int(vol), float(vol * price))
        for day, price, vol in zip(dates, close, volume, strict=True)
    ]


def test_live_validation_excludes_bad_and_short_codes() -> None:
    good = [validation.SectorCandidate(f"SH.GOOD{i}", f"行业{i}") for i in range(5)]
    short = validation.SectorCandidate("SH.SHORT", "短历史")
    bad = validation.SectorCandidate("SH.BAD", "错误代码")
    source = FakeMarketDataSource(
        kline_data={
            **{(item.code, "K_DAY", "2024-01-01", "2026-12-31"): _bars() for item in good},
            (short.code, "K_DAY", "2024-01-01", "2026-12-31"): _bars(100),
        },
        failures={("get_kline", (bad.code, "K_DAY", "2024-01-01", "2026-12-31")): DataSourceError("unknown code")},
    )

    histories, records = validation.fetch_validated_histories(
        source, [*good, short, bad], start="2024-01-01", end="2026-12-31"
    )

    assert set(histories["sector_code"]) == {item.code for item in good}
    assert len(records.query("validation_status == 'validated'")) == 5
    assert records.set_index("code").loc[short.code, "validation_status"] == "insufficient_history"
    assert records.set_index("code").loc[bad.code, "validation_status"] == "invalid"


def test_two_distant_bars_do_not_count_as_two_years_of_daily_history() -> None:
    candidate = validation.SectorCandidate("SH.SPARSE", "稀疏历史")
    sparse = [_bars(1, start="2024-01-01")[0], _bars(1, start="2026-12-31")[0]]
    source = FakeMarketDataSource(
        kline_data={(candidate.code, "K_DAY", "2024-01-01", "2026-12-31"): sparse}
    )

    histories, records = validation.fetch_validated_histories(
        source, [candidate], start="2024-01-01", end="2026-12-31"
    )

    assert histories.empty
    assert records.iloc[0]["validation_status"] == "insufficient_history"


def test_feature_engineering_rejects_sentiment_inputs() -> None:
    frame = pd.DataFrame([bar.__dict__ if hasattr(bar, "__dict__") else {
        "time_key": bar.time_key, "open": bar.open, "high": bar.high, "low": bar.low,
        "close": bar.close, "volume": bar.volume, "turnover": bar.turnover,
    } for bar in _bars(50)])
    frame["sentiment_index"] = 50
    config = validation.load_rule_config()

    with pytest.raises(ValueError, match="sentiment inputs are forbidden"):
        validation.engineer_sector_features(frame, config)


def test_recent_trend_uses_the_five_days_before_the_target_bar() -> None:
    bars = _bars(50)
    features = validation.engineer_sector_features(bars, validation.load_rule_config())

    closes = np.array([bar.close for bar in bars])
    assert features.loc[10, "recent_trend_5d"] == pytest.approx(closes[9] / closes[4] - 1.0)


def test_unmatched_days_remain_unlabeled() -> None:
    result = validation.apply_sector_rules(_bars())

    assert set(result["machine_status"].dropna()) <= {"labeled", "unlabeled", "data_insufficient"}
    assert result.loc[result["machine_status"].eq("unlabeled"), "machine_label"].isna().all()


def test_startup_rejects_an_already_confirmed_prior_trend(monkeypatch) -> None:
    features = pd.DataFrame(
        {
            "date": [pd.Timestamp("2026-01-05")],
            "open": [100.0],
            "high": [102.0],
            "low": [99.0],
            "close": [101.0],
            "volume": [1_000_000.0],
            "return_1d": [0.01],
            "forward_return": [0.04],
            "volume_ratio_20": [1.0],
            "volatility_20": [0.01],
            "recent_trend_5d": [0.04],
            "consecutive_down_days": [0],
            "consecutive_shrink_days": [0],
            "price_position_20": [0.50],
            "zero_range": [False],
            "forward_window_complete": [True],
            "required_ohlcv_complete": [True],
        }
    )
    monkeypatch.setattr(
        validation,
        "engineer_sector_features",
        lambda bars, config: features.copy(deep=True),
    )

    result = validation.apply_sector_rules(pd.DataFrame())

    assert result.loc[0, "machine_status"] == "unlabeled"
    assert pd.isna(result.loc[0, "machine_label"])


def test_fermentation_discloses_price_proxy_evidence(monkeypatch) -> None:
    features = pd.DataFrame(
        {
            "date": [pd.Timestamp("2026-01-05")],
            "open": [100.0],
            "high": [102.0],
            "low": [99.0],
            "close": [101.0],
            "volume": [1_000_000.0],
            "return_1d": [0.01],
            "forward_return": [0.06],
            "volume_ratio_20": [1.0],
            "volatility_20": [0.01],
            "recent_trend_5d": [0.04],
            "consecutive_down_days": [0],
            "consecutive_shrink_days": [0],
            "price_position_20": [0.60],
            "zero_range": [False],
            "forward_window_complete": [True],
            "required_ohlcv_complete": [True],
        }
    )
    monkeypatch.setattr(
        validation,
        "engineer_sector_features",
        lambda bars, config: features.copy(deep=True),
    )

    result = validation.apply_sector_rules(pd.DataFrame())

    assert result.loc[0, "machine_label"] == "发酵"
    assert result.loc[0, "evidence_mode"] == "price_trend_proxy"
    assert result.loc[0, "expansion_verified"] == np.bool_(False)


def test_incomplete_lookback_is_data_insufficient() -> None:
    result = validation.apply_sector_rules(_bars(30))

    assert result.loc[0, "machine_status"] == "data_insufficient"
    assert pd.isna(result.loc[0, "machine_label"])


def test_missing_required_ohlcv_column_is_data_insufficient() -> None:
    frame = pd.DataFrame(
        {
            "date": pd.bdate_range("2025-01-01", periods=40),
            "open": 100.0,
            "high": 101.0,
            "low": 99.0,
            "close": 100.0,
        }
    )

    result = validation.apply_sector_rules(frame)

    assert result["machine_status"].eq("data_insufficient").all()
    assert result["machine_label"].isna().all()
    assert result["evidence_mode"].isna().all()
    assert result["expansion_verified"].isna().all()


def test_balanced_annotation_selection_uses_all_five_strata(monkeypatch) -> None:
    histories = pd.DataFrame(
        {
            "sector_code": ["SH.A"] * 100,
            "sector_name": ["A行业"] * 100,
            "date": pd.bdate_range("2024-01-01", periods=100),
            "open": 1.0,
            "high": 2.0,
            "low": 0.5,
            "close": 1.0,
            "volume": 100,
        }
    )

    def fake_rules(group, config_path):
        output = group.reset_index(drop=True).copy()
        output["machine_label"] = [validation.STATES[index % 5] for index in range(len(output))]
        output["machine_status"] = "labeled"
        return output

    monkeypatch.setattr(validation, "apply_sector_rules", fake_rules)
    sheet = validation.select_annotation_dates(histories, total=75)

    assert len(sheet) == 75
    assert "suggested_stratum" not in sheet
    assert sheet.attrs["stratum_counts"] == {state: 15 for state in validation.STATES}
    assert sheet["manual_label"].eq("").all()


def test_annotation_selection_is_distributed_across_years(monkeypatch) -> None:
    histories = pd.DataFrame(
        {
            "sector_code": ["SH.A"] * 1_500,
            "sector_name": ["A行业"] * 1_500,
            "date": pd.bdate_range("2020-01-01", periods=1_500),
            "open": 1.0,
            "high": 2.0,
            "low": 0.5,
            "close": 1.0,
            "volume": 100,
        }
    )

    def fake_rules(group, config_path):
        output = group.reset_index(drop=True).copy()
        output["machine_label"] = [validation.STATES[index % 5] for index in range(len(output))]
        output["machine_status"] = "labeled"
        return output

    monkeypatch.setattr(validation, "apply_sector_rules", fake_rules)
    sheet = validation.select_annotation_dates(histories, total=75)

    assert pd.to_datetime(sheet["date"]).dt.year.nunique() >= 5


def test_manual_comparison_reports_distribution_confusion_and_bias(monkeypatch) -> None:
    histories = pd.DataFrame(
        {
            "sector_code": ["SH.A"] * 10,
            "sector_name": ["A行业"] * 10,
            "date": pd.bdate_range("2024-01-01", periods=10),
            "open": 1.0,
            "high": 2.0,
            "low": 0.5,
            "close": 1.0,
            "volume": 100,
        }
    )

    def fake_rules(group, config_path):
        output = group.reset_index(drop=True).copy()
        output["machine_label"] = "发酵"
        output["machine_status"] = "labeled"
        return output

    monkeypatch.setattr(validation, "apply_sector_rules", fake_rules)
    annotations = pd.DataFrame(
        {
            "sector_code": ["SH.A"] * 10,
            "date": pd.bdate_range("2024-01-01", periods=10),
            "manual_label": list(validation.STATES) * 2,
        }
    )
    comparison = validation.compare_manual_labels(histories, annotations)

    assert comparison["machine_distribution"]["发酵"] == 10
    assert comparison["confusion_matrix"]["冰点"]["发酵"] == 2
    assert {item["state"] for item in comparison["systematic_biases"]} >= {"发酵"}
    assert comparison["agreement"] == pytest.approx(0.2)


def test_confusion_matrix_preserves_machine_unlabeled_by_manual_state(monkeypatch) -> None:
    histories = pd.DataFrame(
        {
            "sector_code": ["SH.A"] * 2,
            "sector_name": ["A行业"] * 2,
            "date": pd.bdate_range("2024-01-01", periods=2),
            "open": 1.0,
            "high": 2.0,
            "low": 0.5,
            "close": 1.0,
            "volume": 100,
        }
    )

    def fake_rules(group, config_path):
        output = group.reset_index(drop=True).copy()
        output["machine_label"] = pd.Series([pd.NA, pd.NA], dtype="string")
        output["machine_status"] = ["unlabeled", "data_insufficient"]
        return output

    monkeypatch.setattr(validation, "apply_sector_rules", fake_rules)
    annotations = pd.DataFrame(
        {
            "sector_code": ["SH.A", "SH.A"],
            "date": pd.bdate_range("2024-01-01", periods=2),
            "manual_label": ["启动", "退潮"],
        }
    )

    comparison = validation.compare_manual_labels(histories, annotations)

    assert comparison["confusion_matrix"]["启动"]["unlabeled"] == 1
    assert comparison["confusion_matrix"]["退潮"]["data_insufficient"] == 1


def test_compare_command_writes_report_from_blind_sheet(tmp_path, monkeypatch) -> None:
    bars = pd.DataFrame(
        {
            "sector_code": ["SH.A"] * 50,
            "sector_name": ["A行业"] * 50,
            "date": pd.bdate_range("2024-01-01", periods=50),
            "open": 1.0,
            "high": 2.0,
            "low": 0.5,
            "close": 1.0,
            "volume": 100,
        }
    )
    bars.to_csv(tmp_path / "sector_ohlcv.csv.gz", index=False, compression="gzip")
    pd.DataFrame(
        [
            {
                "code": "SH.A",
                "name": "A行业",
                "validation_status": "validated",
                "row_count": 50,
                "first_date": "2024-01-01",
                "last_date": "2024-03-08",
                "missing_rate": 0.0,
                "error": "",
            }
        ]
    ).to_csv(tmp_path / "sector_codes.csv", index=False)
    pd.DataFrame(
        {
            "sector_code": ["SH.A"],
            "sector_name": ["A行业"],
            "date": ["2024-02-01"],
            "manual_label": ["发酵"],
            "annotator": ["tester"],
            "notes": [""],
        }
    ).to_csv(tmp_path / "annotation_sheet_v1.csv", index=False)
    monkeypatch.setattr(validation, "PACK_DIR", tmp_path)

    assert validation.main(["compare", "--json"]) == 0
    report = tmp_path / "reports" / "validation-report.md"
    assert report.exists()
    report_text = report.read_text(encoding="utf-8")
    assert "盲标表不包含逐行机器分层" in report_text
    assert "机器数据不足" in report_text
    assert "机器规则未命中" in report_text


def test_legacy_frozen_check_flag_accepts_version_one_with_hash(tmp_path) -> None:
    config = validation.load_rule_config()
    config["version"] = 1
    config["rule_hash"]["frozen_hash"] = validation.canonical_rule_hash(config)
    path = tmp_path / "sector_labeler.yaml"
    path.write_text(yaml.safe_dump(config, allow_unicode=True, sort_keys=False), encoding="utf-8")

    assert validation.main(["--check-frozen-config", "--config", str(path)]) == 0


def test_frozen_check_rejects_a_well_formed_but_stale_hash(tmp_path) -> None:
    config = validation.load_rule_config()
    config["version"] = 1
    config["rule_hash"]["frozen_hash"] = "0" * 64
    path = tmp_path / "sector_labeler.yaml"
    path.write_text(yaml.safe_dump(config, allow_unicode=True, sort_keys=False), encoding="utf-8")

    assert validation.main(["--check-frozen-config", "--config", str(path)]) == 2
