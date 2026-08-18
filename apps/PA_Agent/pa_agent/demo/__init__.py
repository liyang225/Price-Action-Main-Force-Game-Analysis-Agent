"""Demo mode: replay saved analysis records in the UI."""
from pa_agent.demo.record_loader import (
    frame_from_record_klines,
    is_demo_playable,
    list_pending_record_paths,
    load_analysis_record,
    pick_playable_demo_record,
    pick_random_record_path,
    try_load_analysis_record,
)
from pa_agent.demo.replayer import DemoReplayer

__all__ = [
    "DemoReplayer",
    "frame_from_record_klines",
    "is_demo_playable",
    "list_pending_record_paths",
    "load_analysis_record",
    "pick_playable_demo_record",
    "pick_random_record_path",
    "try_load_analysis_record",
]
