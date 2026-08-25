from __future__ import annotations

from datetime import date

import pytest

from research_harness import ConfigError, load_research_config


def test_yaml_config_loads_typed_research_inputs(tmp_path):
    config_path = tmp_path / "rules.yaml"
    config_path.write_text(
        """
version: 1
data:
  provider: futu
  provider_options:
    host: 127.0.0.1
    port: 11111
  start: 2024-01-02
  end: 2024-01-31
  period: 120m
  instruments:
    - code: SH.600000
      kind: stock
    - code: SH.BK0001
      kind: sector_index
rules:
  - label: 拉升
    when:
      all:
        - field: close
          op: gt
          value: 10
        - field: volume
          op: gte
          value: 1000
output:
  format: markdown
  include_matches: true
""".strip(),
        encoding="utf-8",
    )

    config = load_research_config(config_path)

    assert config.version == 1
    assert config.data.provider == "futu"
    assert config.data.start == date(2024, 1, 2)
    assert config.data.end == date(2024, 1, 31)
    assert config.data.period == "120m"
    assert [item.kind for item in config.data.instruments] == [
        "stock",
        "sector_index",
    ]
    assert config.rules[0].label == "拉升"
    assert config.output.format == "markdown"
    assert config.output.include_matches is True


@pytest.mark.parametrize(
    ("rule_yaml", "message"),
    [
        ("when: close > 10", "structured mapping"),
        (
            """when:
      field: close
      op: __import__
      value: 10""",
            "operator",
        ),
        (
            """when:
      field: close
      op: gt
      value: 10
      python: os.system""",
            "unknown key",
        ),
    ],
)
def test_config_rejects_unstructured_or_unsafe_rules(tmp_path, rule_yaml, message):
    config_path = tmp_path / "rules.yaml"
    config_path.write_text(
        f"""
version: 1
data:
  provider: memory
  start: 2024-01-02
  end: 2024-01-31
  period: day
  instruments:
    - code: SH.600000
      kind: stock
rules:
  - label: 拉升
    {rule_yaml}
""".strip(),
        encoding="utf-8",
    )

    with pytest.raises(ConfigError, match=message):
        load_research_config(config_path)


def test_config_rejects_duplicate_labels_and_instruments(tmp_path):
    config_path = tmp_path / "rules.yaml"
    config_path.write_text(
        """
version: 1
data:
  provider: memory
  start: 2024-01-02
  end: 2024-01-31
  period: day
  instruments:
    - code: SH.600000
      kind: stock
    - code: SH.600000
      kind: stock
rules:
  - label: 观望
    when: {field: close, op: gt, value: 10}
  - label: 观望
    when: {field: close, op: lte, value: 10}
""".strip(),
        encoding="utf-8",
    )

    with pytest.raises(ConfigError, match="duplicate instrument"):
        load_research_config(config_path)


@pytest.mark.parametrize(
    ("override", "message"),
    [
        ("version: true", "config.version"),
        ("output:\n  format: []", "output.format"),
        (
            """data:
  provider: futu
  provider_options: {port: not-a-port}
  start: 2024-01-02
  end: 2024-01-31
  period: day
  instruments: [{code: SH.600000, kind: stock}]""",
            "provider_options.port",
        ),
    ],
)
def test_config_rejects_wrong_scalar_types(tmp_path, override, message):
    base = """
version: 1
data:
  provider: futu
  start: 2024-01-02
  end: 2024-01-31
  period: day
  instruments:
    - code: SH.600000
      kind: stock
rules:
  - label: 观望
    when: {field: close, op: gt, value: 0}
""".strip()
    if override.startswith("version:"):
        text = base.replace("version: 1", override)
    elif override.startswith("output:"):
        text = base + "\n" + override
    else:
        start = base.index("data:")
        end = base.index("rules:")
        text = base[:start] + override + "\n" + base[end:]
    config_path = tmp_path / "invalid.yaml"
    config_path.write_text(text, encoding="utf-8")

    with pytest.raises(ConfigError, match=message):
        load_research_config(config_path)
