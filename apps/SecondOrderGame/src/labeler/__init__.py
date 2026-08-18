"""Independent post-hoc labelers for sector and stock layers."""

from .behavior_counts import BehaviorCountStore
from .confusion_counts import ConfusionCountStore, build_llm_observation_sink
from .ledger import LabelLedger, StoredLabel
from .sector_labeler import SectorLabeler, SectorLabelingResult
from .stock_labeler import StockLabeler, StockLabelingResult, classify_participant

__all__ = [
    "BehaviorCountStore",
    "ConfusionCountStore",
    "LabelLedger",
    "SectorLabeler",
    "SectorLabelingResult",
    "StockLabeler",
    "StockLabelingResult",
    "StoredLabel",
    "build_llm_observation_sink",
    "classify_participant",
]


def __getattr__(name: str):
    """Lazily expose heavier modules so ``python -m`` stays clean."""
    if name in {
        "CATCHUP_LABEL_WINDOW",
        "LabelerCatchUpReport",
        "NightlyLabelingReport",
        "SectorRunReport",
        "compute_labeler_gap",
        "run_labeler_catchup",
        "run_nightly",
    }:
        from . import nightly

        return getattr(nightly, name)
    if name in {
        "load_calibrated_hmm_config",
        "load_calibrated_hmm_config_from_files",
    }:
        from . import calibration

        return getattr(calibration, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
