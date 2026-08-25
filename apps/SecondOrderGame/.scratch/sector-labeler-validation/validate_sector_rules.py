"""Build and compare the sector-cycle rule validation pack.

The module deliberately accepts only sector-index OHLCV.  It neither imports
nor reads the explanatory sector sentiment index, protecting the separation
required by ADR-0019.
"""

from __future__ import annotations

import argparse
from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import asdict, dataclass
from datetime import date, timedelta
import hashlib
import json
from pathlib import Path
import sys
from typing import Any

import numpy as np
import pandas as pd
import yaml

# Direct execution puts this scratch directory on ``sys.path``; add the
# repository root so the production data seam remains the only adapter import.
ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.data.futu_client import FutuMarketDataSource
from src.data.models import Bar
from src.data.protocol import DataSourceError, MarketDataSource
from src.labeler_constants import CYCLE_STATES


PACK_DIR = Path(__file__).resolve().parent
DEFAULT_CONFIG = ROOT / "config" / "sector_labeler.yaml"
STATES = CYCLE_STATES
MACHINE_OUTCOMES = (*STATES, "unlabeled", "data_insufficient")
OHLCV_COLUMNS = ("open", "high", "low", "close", "volume")
ANNOTATION_COLUMNS = (
    "sector_code",
    "sector_name",
    "date",
    "manual_label",
    "annotator",
    "notes",
)
GENERATOR_VERSION = 3


@dataclass(frozen=True, slots=True)
class SectorCandidate:
    code: str
    name: str


@dataclass(frozen=True, slots=True)
class CollectionRecord:
    code: str
    name: str
    validation_status: str
    row_count: int
    first_date: str
    last_date: str
    missing_rate: float
    error: str = ""


def load_rule_config(path: str | Path = DEFAULT_CONFIG) -> dict[str, Any]:
    """Load a v1 rule definition while enforcing its independence guard.

    Version zero is the editable draft and version one is the frozen form.
    Both use the same validation-pack execution path; the frozen-config check
    separately enforces the version/hash gate before production use.
    """

    source = Path(path)
    config = yaml.safe_load(source.read_text(encoding="utf-8"))
    independence = config.get("independence", {})
    if independence.get("may_read_sector_sentiment_index") is not False:
        raise ValueError("sector rules must explicitly forbid the sector sentiment index")
    if config.get("version") not in {0, 1}:
        raise ValueError("this validation pack expects version 0 draft or version 1 frozen rules")
    return config


def canonical_rule_hash(config: Mapping[str, Any]) -> str:
    """Hash the complete rule document with its self-referential hash cleared."""

    canonical = json.loads(json.dumps(config, ensure_ascii=False))
    canonical["rule_hash"]["frozen_hash"] = None
    payload = yaml.safe_dump(canonical, allow_unicode=True, sort_keys=True).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def file_sha256(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def normalize_ohlcv(
    data: pd.DataFrame | Sequence[Bar],
    *,
    allow_incomplete: bool = False,
) -> pd.DataFrame:
    """Return deterministic sector OHLCV, optionally retaining incomplete rows."""

    if isinstance(data, pd.DataFrame):
        result = data.copy(deep=True)
    else:
        result = pd.DataFrame(asdict(bar) for bar in data)
    if "date" not in result and "time_key" in result:
        result = result.rename(columns={"time_key": "date"})
    if "date" not in result:
        raise ValueError("sector OHLCV is missing columns: date")
    missing = sorted(set(OHLCV_COLUMNS).difference(result.columns))
    if missing and not allow_incomplete:
        raise ValueError(f"sector OHLCV is missing columns: {', '.join(missing)}")
    for column in missing:
        result[column] = np.nan
    result["date"] = pd.to_datetime(result["date"], errors="raise").dt.tz_localize(None).dt.normalize()
    if result["date"].duplicated().any():
        raise ValueError("sector OHLCV contains duplicate dates")
    for column in OHLCV_COLUMNS:
        result[column] = pd.to_numeric(result[column], errors="coerce")
    result["required_ohlcv_complete"] = result[list(OHLCV_COLUMNS)].notna().all(axis=1)
    invalid = ~result["required_ohlcv_complete"]
    if invalid.any() and not allow_incomplete:
        raise ValueError(f"sector OHLCV contains {int(invalid.sum())} non-numeric rows")
    prices = result[["open", "high", "low", "close"]].stack()
    if (prices <= 0).any():
        raise ValueError("sector OHLC prices must be positive")
    if (result["volume"].dropna() < 0).any():
        raise ValueError("sector volume must be non-negative")
    return result.sort_values("date", kind="mergesort").reset_index(drop=True)


def engineer_sector_features(
    bars: pd.DataFrame | Sequence[Bar],
    config: Mapping[str, Any],
) -> pd.DataFrame:
    """Engineer the draft features from sector OHLCV and nothing else."""

    frame = normalize_ohlcv(bars, allow_incomplete=True)
    forbidden = [column for column in frame if "sentiment" in column.lower() or "情绪" in column]
    if forbidden:
        raise ValueError(f"sector sentiment inputs are forbidden: {', '.join(forbidden)}")

    lookback = config["lookback"]
    range_window = int(lookback["range_bars"])
    volume_window = int(lookback["volume_median_bars"])
    volatility_window = int(lookback["volatility_bars"])
    forward_window = int(config["forward_return"]["window_bars"])

    result = frame.copy(deep=True)
    result["return_1d"] = result["close"].pct_change(fill_method=None)
    result["forward_return"] = result["close"].shift(-forward_window) / result["close"] - 1.0
    prior_volume_median = result["volume"].shift(1).rolling(
        volume_window, min_periods=volume_window
    ).median()
    result["volume_ratio_20"] = result["volume"] / prior_volume_median.replace(0, np.nan)
    result["volatility_20"] = result["return_1d"].rolling(
        volatility_window, min_periods=volatility_window
    ).std(ddof=1)
    # The config defines the trend before entering the target state: t-6 -> t-1.
    result["recent_trend_5d"] = result["close"].shift(1) / result["close"].shift(6) - 1.0
    rolling_low = result["low"].rolling(range_window, min_periods=range_window).min()
    rolling_high = result["high"].rolling(range_window, min_periods=range_window).max()
    width = (rolling_high - rolling_low).replace(0, np.nan)
    result["price_position_20"] = ((result["close"] - rolling_low) / width).clip(0.0, 1.0)
    result["consecutive_down_days"] = _run_length(result["return_1d"] < 0)
    result["consecutive_shrink_days"] = _run_length(result["volume"] < prior_volume_median)
    result["zero_range"] = result["high"].eq(result["low"])
    result["forward_window_complete"] = result["forward_return"].notna()
    return result


def _run_length(condition: pd.Series) -> pd.Series:
    counts: list[int] = []
    current = 0
    for value in condition.fillna(False).astype(bool):
        current = current + 1 if value else 0
        counts.append(current)
    return pd.Series(counts, index=condition.index, dtype="int64")


def apply_sector_rules(
    bars: pd.DataFrame | Sequence[Bar],
    config_path: str | Path = DEFAULT_CONFIG,
) -> pd.DataFrame:
    """Apply the v1 draft, separating unmatched from insufficient days."""

    config = load_rule_config(config_path)
    features = engineer_sector_features(bars, config)
    thresholds = config["thresholds"]
    masks = pd.DataFrame(
        {
            "冰点": _all(
                features,
                thresholds["冰点"],
                ("price_position_20", "lte", "price_position_20_max"),
                ("consecutive_shrink_days", "gte", "consecutive_shrink_days_min"),
                ("recent_trend_5d", "lte", "recent_trend_5d_max"),
                ("forward_return", "gte", "forward_min"),
                ("volume_ratio_20", "lte", "volume_ratio_max"),
            ),
            "启动": _all(
                features,
                thresholds["启动"],
                ("return_1d", "gte", "return_1d_min"),
                ("forward_return", "gte", "forward_min"),
                ("volume_ratio_20", "gte", "volume_ratio_min"),
                ("volume_ratio_20", "lte", "volume_ratio_max"),
                ("price_position_20", "gte", "price_position_20_min"),
                ("consecutive_down_days", "lte", "consecutive_down_days_max"),
                ("recent_trend_5d", "lte", "recent_trend_5d_max"),
            ),
            "发酵": _all(
                features,
                thresholds["发酵"],
                ("return_1d", "gte", "return_1d_min"),
                ("forward_return", "gte", "forward_min"),
                ("volume_ratio_20", "gte", "volume_ratio_min"),
                ("volume_ratio_20", "lte", "volume_ratio_max"),
                ("price_position_20", "gte", "price_position_20_min"),
                ("consecutive_down_days", "lte", "consecutive_down_days_max"),
                ("recent_trend_5d", "gte", "recent_trend_5d_min"),
            ),
            "高潮": _all(
                features,
                thresholds["高潮"],
                ("return_1d", "gte", "return_1d_min"),
                ("volume_ratio_20", "gte", "volume_ratio_min"),
                ("price_position_20", "gte", "price_position_20_min"),
                ("forward_return", "lte", "forward_max"),
                ("recent_trend_5d", "gte", "recent_trend_5d_min"),
            ),
            "退潮": _all(
                features,
                thresholds["退潮"],
                ("return_1d", "lte", "return_1d_max"),
                ("forward_return", "lte", "forward_max"),
                ("volume_ratio_20", "gte", "volume_ratio_min"),
                ("price_position_20", "lte", "price_position_20_max"),
                ("consecutive_down_days", "gte", "consecutive_down_days_min"),
            ),
        },
        index=features.index,
    )
    required_rule_features = (
        "return_1d", "forward_return", "volume_ratio_20", "volatility_20",
        "recent_trend_5d", "price_position_20",
    )
    eligible = (
        features["required_ohlcv_complete"]
        & features["forward_window_complete"]
        & features[list(required_rule_features)].notna().all(axis=1)
    )
    if config["zero_range_bar"].get("skip_labeling"):
        eligible &= ~features["zero_range"]
    masks = masks.where(eligible, False).astype(bool)
    labels = pd.Series(pd.NA, index=features.index, dtype="string")
    for state in config["priority"]:
        labels.loc[labels.isna() & masks[state]] = state
    status = pd.Series("data_insufficient", index=features.index, dtype="string")
    status.loc[eligible] = "unlabeled"
    status.loc[labels.notna()] = "labeled"
    output = features.copy(deep=True)
    output["machine_label"] = labels
    output["machine_status"] = status
    output["candidate_count"] = masks.sum(axis=1)
    evidence_mode = pd.Series(pd.NA, index=features.index, dtype="string")
    expansion_verified = pd.Series(pd.NA, index=features.index, dtype="boolean")
    for state, metadata in config["state_metadata"].items():
        selected = labels.eq(state)
        evidence_mode.loc[selected] = metadata["evidence_mode"]
        expansion_verified.loc[selected] = metadata["expansion_verified"]
    output["evidence_mode"] = evidence_mode
    output["expansion_verified"] = expansion_verified
    return output


def _all(
    features: pd.DataFrame,
    thresholds: Mapping[str, float],
    *conditions: tuple[str, str, str],
) -> pd.Series:
    result = pd.Series(True, index=features.index, dtype=bool)
    for feature, operator, threshold_name in conditions:
        values = pd.to_numeric(features[feature], errors="coerce")
        threshold = thresholds[threshold_name]
        if operator == "gte":
            current = values >= threshold
        elif operator == "lte":
            current = values <= threshold
        elif operator == "abs_lte":
            current = values.abs() <= threshold
        else:  # pragma: no cover - all operators are enumerated above.
            raise ValueError(f"unsupported rule operator: {operator}")
        result &= current.fillna(False)
    return result


def fetch_validated_histories(
    source: MarketDataSource,
    candidates: Sequence[SectorCandidate],
    *,
    start: str,
    end: str,
    minimum_calendar_days: int = 730,
    minimum_trading_days: int = 400,
    maximum_missing_rate: float = 0.25,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Validate candidate codes by real calls; only adequate histories survive."""

    collected: list[pd.DataFrame] = []
    records: list[CollectionRecord] = []
    requested_start = pd.Timestamp(start)
    requested_end = pd.Timestamp(end)
    expected_business_days = max(1, len(pd.bdate_range(requested_start, requested_end)))
    for candidate in candidates:
        try:
            bars = normalize_ohlcv(source.get_kline(candidate.code, "K_DAY", start, end))
            first = bars["date"].min() if not bars.empty else pd.NaT
            last = bars["date"].max() if not bars.empty else pd.NaT
            span = (last - first).days if pd.notna(first) and pd.notna(last) else 0
            missing_rate = max(0.0, 1.0 - len(bars) / expected_business_days)
            adequate = (
                len(bars) >= minimum_trading_days
                and span >= minimum_calendar_days
                and missing_rate <= maximum_missing_rate
            )
            status = "validated" if adequate else "insufficient_history"
            error = "" if adequate else (
                f"history has {len(bars)} trading days across {span} calendar days "
                f"with {missing_rate:.2%} weekday gaps"
            )
            if status == "validated":
                bars.insert(0, "sector_name", candidate.name)
                bars.insert(0, "sector_code", candidate.code)
                collected.append(bars)
            records.append(
                CollectionRecord(
                    candidate.code,
                    candidate.name,
                    status,
                    len(bars),
                    first.date().isoformat() if pd.notna(first) else "",
                    last.date().isoformat() if pd.notna(last) else "",
                    missing_rate,
                    error,
                )
            )
        except (DataSourceError, ValueError, TypeError) as exc:
            records.append(
                CollectionRecord(candidate.code, candidate.name, "invalid", 0, "", "", 1.0, str(exc))
            )
    histories = pd.concat(collected, ignore_index=True) if collected else pd.DataFrame(
        columns=("sector_code", "sector_name", "date", *OHLCV_COLUMNS)
    )
    return histories, pd.DataFrame(asdict(record) for record in records)


def select_annotation_dates(
    histories: pd.DataFrame,
    *,
    total: int = 75,
    seed: int = 20260810,
    config_path: str | Path = DEFAULT_CONFIG,
) -> pd.DataFrame:
    """Build a deterministic 50-100 row sheet balanced across five strata.

    Draft labels balance the selection internally but are not written beside
    the human task.  The returned frame carries only aggregate counts in
    ``DataFrame.attrs`` so the annotator remains blind to machine predictions.
    """

    if not 50 <= total <= 100:
        raise ValueError("annotation total must be between 50 and 100")
    if total % len(STATES):
        raise ValueError("annotation total must be divisible by five")
    required = {"sector_code", "sector_name", "date", *OHLCV_COLUMNS}
    missing = sorted(required.difference(histories.columns))
    if missing:
        raise ValueError(f"history is missing columns: {', '.join(missing)}")

    candidates: list[pd.DataFrame] = []
    for (code, name), group in histories.groupby(["sector_code", "sector_name"], sort=True):
        labeled = apply_sector_rules(group, config_path)
        labeled["sector_code"] = code
        labeled["sector_name"] = name
        candidates.append(labeled)
    pool = pd.concat(candidates, ignore_index=True)
    pool = pool.loc[pool["machine_status"].eq("labeled")].copy()
    per_state = total // len(STATES)
    rows: list[pd.DataFrame] = []
    for offset, state in enumerate(STATES):
        state_pool = pool.loc[pool["machine_label"].eq(state)]
        if len(state_pool) < per_state:
            raise ValueError(
                f"not enough OHLCV-derived {state} candidates: need {per_state}, found {len(state_pool)}"
            )
        # Round-robin sector-year buckets, retaining random order inside each
        # bucket. This prevents early years or one industry dominating a state.
        shuffled = state_pool.sample(frac=1.0, random_state=seed + offset).reset_index(drop=True)
        shuffled["_year"] = pd.to_datetime(shuffled["date"]).dt.year
        shuffled["_bucket"] = shuffled["sector_code"].astype(str) + ":" + shuffled["_year"].astype(str)
        shuffled["_random_order"] = range(len(shuffled))
        shuffled["_bucket_rank"] = shuffled.groupby("_bucket", sort=True).cumcount()
        chosen = shuffled.sort_values(
            ["_bucket_rank", "_random_order"], kind="mergesort"
        ).head(per_state)
        rows.append(chosen)
    selected = pd.concat(rows, ignore_index=True)
    sheet = pd.DataFrame(
        {
            "sector_code": selected["sector_code"],
            "sector_name": selected["sector_name"],
            "date": pd.to_datetime(selected["date"]).dt.date.astype(str),
            "manual_label": "",
            "annotator": "",
            "notes": "",
        }
    )
    sheet = sheet.loc[:, ANNOTATION_COLUMNS].sort_values(
        ["sector_code", "date"], kind="mergesort"
    ).reset_index(drop=True)
    sheet.attrs["stratum_counts"] = {state: per_state for state in STATES}
    sheet.attrs["seed"] = seed
    return sheet


def compare_manual_labels(
    histories: pd.DataFrame,
    annotations: pd.DataFrame,
    *,
    config_path: str | Path = DEFAULT_CONFIG,
) -> dict[str, Any]:
    """Compare completed manual labels to the draft machine rules."""

    required = {"sector_code", "date", "manual_label"}
    missing = sorted(required.difference(annotations.columns))
    if missing:
        raise ValueError(f"annotation sheet is missing columns: {', '.join(missing)}")
    completed = annotations.copy(deep=True)
    completed["manual_label"] = completed["manual_label"].fillna("").astype(str).str.strip()
    unknown = sorted(set(completed["manual_label"]) - {"", *STATES})
    if unknown:
        raise ValueError(f"annotation sheet contains unknown labels: {', '.join(unknown)}")
    completed = completed.loc[completed["manual_label"].ne("")].copy()

    machine_frames: list[pd.DataFrame] = []
    for code, group in histories.groupby("sector_code", sort=True):
        machine = apply_sector_rules(group, config_path)
        machine["sector_code"] = code
        machine_frames.append(machine[["sector_code", "date", "machine_label", "machine_status"]])
    machine = pd.concat(machine_frames, ignore_index=True) if machine_frames else pd.DataFrame()
    completed["date"] = pd.to_datetime(completed["date"], errors="raise").dt.normalize()
    compared = completed.merge(machine, on=["sector_code", "date"], how="left", validate="one_to_one")
    compared["machine_outcome"] = compared["machine_label"].astype("string")
    missing_machine_label = compared["machine_outcome"].isna()
    compared.loc[missing_machine_label, "machine_outcome"] = compared.loc[
        missing_machine_label, "machine_status"
    ].fillna("data_insufficient")

    manual_distribution = _distribution(compared["manual_label"])
    machine_distribution = _distribution(compared["machine_label"])
    matrix = pd.crosstab(
        pd.Categorical(compared["manual_label"], categories=STATES),
        pd.Categorical(compared["machine_outcome"], categories=MACHINE_OUTCOMES),
        dropna=False,
    ).reindex(index=STATES, columns=MACHINE_OUTCOMES, fill_value=0)
    comparable = compared["machine_label"].notna()
    agreement = (
        float((compared.loc[comparable, "manual_label"] == compared.loc[comparable, "machine_label"]).mean())
        if comparable.any()
        else None
    )
    biases: list[dict[str, Any]] = []
    total = len(compared)
    for state in STATES:
        manual_share = manual_distribution[state] / total if total else 0.0
        machine_share = machine_distribution[state] / total if total else 0.0
        delta = machine_share - manual_share
        if abs(delta) >= 0.10:
            biases.append(
                {
                    "state": state,
                    "direction": "over" if delta > 0 else "under",
                    "share_delta": delta,
                }
            )
    pair_counts = Counter(
        (str(row.manual_label), str(row.machine_label))
        for row in compared.loc[comparable].itertuples(index=False)
        if row.manual_label != row.machine_label
    )
    top_confusions = [
        {"manual": manual, "machine": machine_label, "count": count}
        for (manual, machine_label), count in pair_counts.most_common(5)
    ]
    return {
        "completed_annotation_count": total,
        "comparable_count": int(comparable.sum()),
        "machine_unlabeled_count": int((compared["machine_status"] == "unlabeled").sum()),
        "machine_data_insufficient_count": int((compared["machine_status"] == "data_insufficient").sum()),
        "agreement": agreement,
        "manual_distribution": manual_distribution,
        "machine_distribution": machine_distribution,
        "confusion_matrix": {
            state: {column: int(matrix.loc[state, column]) for column in MACHINE_OUTCOMES}
            for state in STATES
        },
        "systematic_biases": biases,
        "top_confusions": top_confusions,
    }


def _distribution(values: pd.Series) -> dict[str, int]:
    counts = values.value_counts()
    return {state: int(counts.get(state, 0)) for state in STATES}


def write_report(
    histories: pd.DataFrame,
    codes: pd.DataFrame,
    annotations: pd.DataFrame,
    comparison: Mapping[str, Any] | None,
    destination: str | Path,
    *,
    sampling_manifest: Mapping[str, Any] | None = None,
    rule_config: Mapping[str, Any] | None = None,
) -> Path:
    """Write the reproducible Markdown validation summary."""

    output = Path(destination)
    output.parent.mkdir(parents=True, exist_ok=True)
    stratum_counts = annotations.attrs.get("stratum_counts")
    if stratum_counts is None and sampling_manifest is not None:
        stratum_counts = sampling_manifest.get("stratum_counts")
    stratum_summary = (
        json.dumps(stratum_counts, ensure_ascii=False)
        if stratum_counts
        else "未提供（盲标表不包含逐行机器分层）"
    )
    config = dict(rule_config or load_rule_config())
    version = config["version"]
    frozen_hash = config["rule_hash"]["frozen_hash"]
    rule_summary = (
        f"version {version}，规则哈希 `{frozen_hash}`"
        if frozen_hash is not None
        else f"version {version} 草案（尚无冻结哈希）"
    )
    lines = [
        "# 板块规则实证验证报告",
        "",
        f"> 当前规则是 {rule_summary}；本报告不把机器标签当作人工真值。",
        "",
        "## 已核验板块与数据质量",
        "",
        "| 板块 | 代码 | 状态 | 日期范围 | 行数 | 工作日缺失率 |",
        "|---|---|---|---|---:|---:|",
    ]
    for row in codes.itertuples(index=False):
        date_range = f"{row.first_date} 至 {row.last_date}" if row.first_date else "—"
        lines.append(
            f"| {row.name} | `{row.code}` | {row.validation_status} | {date_range} | {row.row_count} | {row.missing_rate:.2%} |"
        )
    lines.extend(
        [
            "",
            "## 待人工标注样本",
            "",
            f"共 {len(annotations)} 个日期；抽样分层：{stratum_summary}。",
            "人工标注只应查看对应板块指数 OHLCV，不得查看板块情绪指数或机器标签。",
            "",
            "## 机器规则对比",
            "",
        ]
    )
    if not comparison or comparison["completed_annotation_count"] == 0:
        lines.append("尚无已填写的人工标签；运行 `compare` 后将在此写入分布、混淆矩阵和系统性偏差。")
    else:
        lines.extend(
            [
                f"已完成 {comparison['completed_annotation_count']} 条，机器可比 {comparison['comparable_count']} 条。",
                f"机器规则未命中：{comparison['machine_unlabeled_count']} 条；机器数据不足：{comparison['machine_data_insufficient_count']} 条。",
                f"一致率：{comparison['agreement']:.2%}" if comparison["agreement"] is not None else "一致率：数据不足",
                "",
                f"- 人工分布：`{json.dumps(comparison['manual_distribution'], ensure_ascii=False)}`",
                f"- 机器分布：`{json.dumps(comparison['machine_distribution'], ensure_ascii=False)}`",
                f"- 系统性偏差：`{json.dumps(comparison['systematic_biases'], ensure_ascii=False)}`",
                f"- 主要混淆：`{json.dumps(comparison['top_confusions'], ensure_ascii=False)}`",
                "",
                "### 混淆矩阵（行=人工，列=机器）",
                "",
                "| 人工\\机器 | " + " | ".join(MACHINE_OUTCOMES) + " |",
                "|---|" + "---:|" * len(MACHINE_OUTCOMES),
            ]
        )
        for state in STATES:
            row = comparison["confusion_matrix"][state]
            lines.append(
                f"| {state} | "
                + " | ".join(str(row[column]) for column in MACHINE_OUTCOMES)
                + " |"
            )
    output.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return output


def _read_candidates(path: Path) -> list[SectorCandidate]:
    frame = pd.read_csv(path, dtype=str).fillna("")
    required = {"code", "name"}
    if not required.issubset(frame):
        raise ValueError("sector code file must contain code and name")
    return [SectorCandidate(row.code, row.name) for row in frame.itertuples(index=False)]


def _write_collection_artifacts(histories: pd.DataFrame, records: pd.DataFrame) -> None:
    histories.to_csv(
        PACK_DIR / "sector_ohlcv.csv.gz",
        index=False,
        encoding="utf-8",
        compression={"method": "gzip", "mtime": 0},
    )
    records.to_csv(PACK_DIR / "sector_codes.csv", index=False, encoding="utf-8-sig")
    digest = hashlib.sha256((PACK_DIR / "sector_ohlcv.csv.gz").read_bytes()).hexdigest()
    (PACK_DIR / "collection-manifest.json").write_text(
        json.dumps(
            {
                "schema_version": "1",
                "source": "FutuMarketDataSource.get_kline(K_DAY)",
                "sha256": digest,
                "validated_sector_count": int(records["validation_status"].eq("validated").sum()),
                "row_count": len(histories),
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )


def _command_collect(args: argparse.Namespace) -> int:
    candidates = _read_candidates(Path(args.candidates))
    source = FutuMarketDataSource(host=args.host, port=args.port, retries=args.retries)
    try:
        histories, records = fetch_validated_histories(
            source, candidates, start=args.start, end=args.end
        )
    finally:
        source.close()
    if records["validation_status"].eq("validated").sum() < 5:
        print("fewer than five candidate sectors passed live validation", file=sys.stderr)
        return 2
    _write_collection_artifacts(histories, records)
    return 0


def _command_sample(args: argparse.Namespace) -> int:
    history_path = PACK_DIR / "sector_ohlcv.csv.gz"
    histories = pd.read_csv(history_path, compression="gzip")
    sheet = select_annotation_dates(histories, total=args.total, seed=args.seed)
    destination = Path(args.output)
    if not destination.is_absolute():
        destination = PACK_DIR / destination
    if destination.exists():
        existing = pd.read_csv(destination, keep_default_na=False, encoding="utf-8-sig")
        if "manual_label" in existing and existing["manual_label"].astype(str).str.strip().ne("").any():
            raise ValueError(f"refusing to overwrite completed manual labels: {destination}")
    sheet.to_csv(destination, index=False, encoding="utf-8-sig")
    manifest_path = destination.with_name(f"{destination.stem}-manifest.json")
    manifest_path.write_text(
        json.dumps(
            {
                "schema_version": "3",
                "generator_version": GENERATOR_VERSION,
                "state_space": list(STATES),
                "rule_config_version": load_rule_config()["version"],
                "rule_config_hash": canonical_rule_hash(load_rule_config()),
                "input_data_sha256": file_sha256(history_path),
                "seed": sheet.attrs["seed"],
                "total": len(sheet),
                "stratum_counts": sheet.attrs["stratum_counts"],
                "blind_sheet": True,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    return 0


def _command_compare(args: argparse.Namespace) -> int:
    histories = pd.read_csv(PACK_DIR / "sector_ohlcv.csv.gz", compression="gzip")
    codes = pd.read_csv(PACK_DIR / "sector_codes.csv")
    source = Path(args.input)
    if not source.is_absolute():
        source = PACK_DIR / source
    annotations = pd.read_csv(source, keep_default_na=False, encoding="utf-8-sig")
    comparison = compare_manual_labels(histories, annotations)
    manifest_path = source.with_name(f"{source.stem}-manifest.json")
    sampling_manifest = (
        json.loads(manifest_path.read_text(encoding="utf-8"))
        if manifest_path.exists()
        else None
    )
    write_report(
        histories,
        codes,
        annotations,
        comparison,
        PACK_DIR / "reports" / "validation-report.md",
        sampling_manifest=sampling_manifest,
        rule_config=load_rule_config(args.config),
    )
    if args.json:
        print(json.dumps(comparison, ensure_ascii=False, indent=2))
    return 0


def _command_check_frozen_config(args: argparse.Namespace) -> int:
    """Check that the current rules are a frozen v1 configuration."""

    from src.config_validator import ConfigError, validate_sector_labeler_file

    try:
        validate_sector_labeler_file(args.config)
        config = load_rule_config(args.config)
    except (ConfigError, ValueError) as exc:
        print(str(exc), file=sys.stderr)
        return 2
    frozen_hash = config["rule_hash"]["frozen_hash"]
    if config["version"] != 1 or frozen_hash is None:
        print("sector rules are not frozen at version 1 with a rule hash", file=sys.stderr)
        return 2
    if frozen_hash != canonical_rule_hash(config):
        print("sector frozen rule hash does not match the canonical configuration", file=sys.stderr)
        return 2
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Collect OHLCV-only sector evidence and compare draft cycle rules."
    )
    subparsers = parser.add_subparsers(dest="command")
    collect = subparsers.add_parser("collect", help="validate candidates through live Futu OpenD")
    collect.add_argument("--candidates", default=str(PACK_DIR / "sector_candidates.csv"))
    collect.add_argument("--start", default=(date.today() - timedelta(days=3 * 365)).isoformat())
    collect.add_argument("--end", default=date.today().isoformat())
    collect.add_argument("--host", default="127.0.0.1")
    collect.add_argument("--port", type=int, default=11111)
    collect.add_argument("--retries", type=int, default=1)
    collect.set_defaults(handler=_command_collect)

    sample = subparsers.add_parser("sample", help="create a balanced manual annotation sheet")
    sample.add_argument("--total", type=int, default=75)
    sample.add_argument("--seed", type=int, default=20260810)
    sample.add_argument("--output", default="annotation_sheet_v1.csv")
    sample.set_defaults(handler=_command_sample)

    compare = subparsers.add_parser("compare", help="compare completed manual labels to draft rules")
    compare.add_argument("--json", action="store_true", help="also print the comparison as JSON")
    compare.add_argument("--config", default=str(DEFAULT_CONFIG))
    compare.add_argument(
        "--input",
        default="annotation_sheet_v1.csv",
        help="annotation CSV name or path (default: the current five-state v1 sheet)",
    )
    compare.set_defaults(handler=_command_compare)

    frozen = subparsers.add_parser("check-frozen-config", help="verify a frozen v1 rule configuration")
    frozen.add_argument("--config", default=str(DEFAULT_CONFIG))
    frozen.set_defaults(handler=_command_check_frozen_config)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    # Keep the issue verification command stable even though the tool also
    # exposes subcommands for collection, sampling, and comparison.
    raw_args = list(sys.argv[1:] if argv is None else argv)
    if raw_args and raw_args[0] == "--check-frozen-config":
        parser = argparse.ArgumentParser(add_help=False)
        parser.add_argument("--check-frozen-config", action="store_true")
        parser.add_argument("--config", default=str(DEFAULT_CONFIG))
        args = parser.parse_args(raw_args)
        return _command_check_frozen_config(args)
    parser = build_parser()
    args = parser.parse_args(raw_args)
    if not getattr(args, "command", None):
        parser.print_help()
        return 0
    return args.handler(args)


if __name__ == "__main__":
    raise SystemExit(main())
