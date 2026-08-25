from __future__ import annotations

import json

from research_harness.cli import main


class FixedProvider:
    def fetch_history(self, request):
        return [
            {
                "code": request.code,
                "trading_date": "2024-01-02",
                "close": 10.5,
                "volume": 1000,
            }
        ]


def _write_config(path):
    path.write_text(
        """
version: 1
data:
  provider: memory
  start: 2024-01-02
  end: 2024-01-02
  period: day
  instruments:
    - code: SH.600000
      kind: stock
rules:
  - label: 拉升
    when:
      field: close
      op: gte
      value: 10
""".strip(),
        encoding="utf-8",
    )


def test_cli_replay_writes_reproducible_json(tmp_path):
    config_path = tmp_path / "rules.yaml"
    output_path = tmp_path / "report.json"
    _write_config(config_path)

    exit_code = main(
        [
            "replay",
            "--config",
            str(config_path),
            "--format",
            "json",
            "--output",
            str(output_path),
            "--include-matches",
        ],
        provider_factory=lambda config: FixedProvider(),
    )

    assert exit_code == 0
    payload = json.loads(output_path.read_text(encoding="utf-8"))
    assert payload["label_stats"] == [
        {"count": 1, "label": "拉升", "share": 1.0}
    ]
    assert payload["matches"] == [
        {
            "code": "SH.600000",
            "labels": ["拉升"],
            "trading_date": "2024-01-02",
        }
    ]


def test_cli_replay_prints_markdown_when_no_output_path(tmp_path, capsys):
    config_path = tmp_path / "rules.yaml"
    _write_config(config_path)

    exit_code = main(
        ["replay", "--config", str(config_path), "--format", "markdown"],
        provider_factory=lambda config: FixedProvider(),
    )

    assert exit_code == 0
    assert "# 标注规则回放报告" in capsys.readouterr().out


def test_cli_depth_measures_both_periods_by_default(tmp_path, capsys):
    config_path = tmp_path / "rules.yaml"
    _write_config(config_path)

    exit_code = main(
        ["depth", "--config", str(config_path), "--format", "json"],
        provider_factory=lambda config: FixedProvider(),
    )

    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    assert [entry["period"] for entry in payload["entries"]] == ["day", "120m"]
