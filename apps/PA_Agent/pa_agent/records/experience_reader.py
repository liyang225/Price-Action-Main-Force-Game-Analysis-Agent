"""ExperienceReader — read-only access to the experience library.

Scans ``success_cases/`` and ``failure_cases/`` subdirectories under
``EXPERIENCE_DIR / cycle_position /``, sorts files by the timestamp
embedded in their filenames (descending, newest first), and returns
the top 5 entries across both directories combined.

File naming convention (timestamp portion):
    YYYY-MM-DD_HH-mm-ss   (minutes use '-', not ':')

Example filename:
    2026-05-18_14-30-45_XAUUSD_1h.json

This module is strictly read-only — it never writes or deletes files.
"""
from __future__ import annotations

import json
import logging
import re
from datetime import datetime
from pathlib import Path
from typing import Optional

from pa_agent.records.schema import ExperienceEntry

# Regex to extract the timestamp portion from a filename.
_TS_PATTERN = re.compile(r"(\d{4}-\d{2}-\d{2}_\d{2}-\d{2}-\d{2})")
_TS_FORMAT = "%Y-%m-%d_%H-%M-%S"


def _default_logger() -> logging.Logger:
    return logging.getLogger(__name__)


def _parse_timestamp_ms(filename: str) -> Optional[int]:
    """Extract and parse the timestamp from a filename.

    Returns the timestamp in milliseconds, or ``None`` if the filename
    does not contain a parseable timestamp.
    """
    match = _TS_PATTERN.search(filename)
    if not match:
        return None
    ts_str = match.group(1)
    try:
        dt = datetime.strptime(ts_str, _TS_FORMAT)
        return int(dt.timestamp() * 1000)
    except ValueError:
        return None


class ExperienceReader:
    """Read experience entries from the experience library (read-only).

    Parameters
    ----------
    experience_dir:
        Root directory of the experience library.  Defaults to
        ``pa_agent.config.paths.EXPERIENCE_DIR`` when ``None``.
    logger:
        Optional logger instance.  A module-level logger is used when
        ``None``.
    """

    def __init__(
        self,
        experience_dir: Optional[Path] = None,
        logger: Optional[logging.Logger] = None,
    ) -> None:
        if experience_dir is None:
            from pa_agent.config.paths import EXPERIENCE_DIR
            experience_dir = EXPERIENCE_DIR

        self._experience_dir = experience_dir
        self._logger = logger or _default_logger()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def read_top5(self, cycle_position: str) -> list[ExperienceEntry]:
        """Return the 5 most-recent experience entries for *cycle_position*.

        Scans both ``success_cases/`` and ``failure_cases/`` subdirectories
        under ``EXPERIENCE_DIR / cycle_position /``.  Files are sorted by
        the timestamp embedded in their filenames (descending).  The top 5
        entries across both directories combined are returned.

        Parameters
        ----------
        cycle_position:
            The cycle position label (e.g. ``"micro_channel"``).

        Returns
        -------
        list[ExperienceEntry]
            Up to 5 entries, newest first.  Returns an empty list when no
            readable entries are found.
        """
        base_dir = self._experience_dir / cycle_position
        candidates: list[tuple[int, str, Path]] = []  # (timestamp_ms, case_type, path)

        for case_type, subdir_name in (("success", "success_cases"), ("failure", "failure_cases")):
            subdir = base_dir / subdir_name
            if not subdir.exists():
                self._logger.debug(
                    "ExperienceReader: directory does not exist, skipping: %s", subdir
                )
                continue

            for file_path in subdir.iterdir():
                if not file_path.is_file():
                    continue
                # Skip non-JSON files and hidden files like .gitkeep
                if file_path.suffix.lower() != ".json":
                    continue

                ts_ms = _parse_timestamp_ms(file_path.name)
                if ts_ms is None:
                    self._logger.warning(
                        "ExperienceReader: cannot parse timestamp from filename, skipping: %s",
                        file_path.name,
                    )
                    continue

                candidates.append((ts_ms, case_type, file_path))

        # Sort by timestamp descending (newest first), then take top 5.
        candidates.sort(key=lambda t: t[0], reverse=True)
        top5 = candidates[:5]

        entries: list[ExperienceEntry] = []
        for ts_ms, case_type, file_path in top5:
            content = self._read_json(file_path)
            if content is None:
                continue
            entry = ExperienceEntry(
                filename=file_path.name,
                case_type=case_type,
                cycle_position=cycle_position,
                timestamp_ms=ts_ms,
                content=content,
            )
            entries.append(entry)

        return entries

    def read_for_stage2(
        self,
        cycle_position: str,
        *,
        direction: str = "",
        patterns: list[str] | None = None,
        max_entries: int = 3,
        max_chars_per_entry: int = 400,
    ) -> list[ExperienceEntry]:
        """Return recent experience entries filtered for Stage 2 relevance."""
        entries = self.read_top5(cycle_position)
        if not entries:
            return []

        dir_norm = str(direction or "").strip().lower()
        pattern_set = {
            str(p).strip().lower() for p in (patterns or []) if str(p).strip()
        }

        def _score(entry: ExperienceEntry) -> int:
            content = entry.content if isinstance(entry.content, dict) else {}
            score = 0
            ent_dir = str(content.get("direction", "") or "").strip().lower()
            if dir_norm and ent_dir == dir_norm:
                score += 2
            ent_patterns = content.get("detected_patterns") or []
            if pattern_set and isinstance(ent_patterns, list):
                overlap = pattern_set.intersection(
                    {str(p).strip().lower() for p in ent_patterns}
                )
                score += len(overlap)
            return score

        ranked = sorted(entries, key=lambda e: (_score(e), e.timestamp_ms), reverse=True)
        cap = max(0, min(max_entries, 10))
        return ranked[:cap]

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _read_json(self, path: Path) -> Optional[dict]:
        """Read and parse a JSON file.

        Returns the parsed dict, or ``None`` on any error (with a warning
        logged).
        """
        try:
            text = path.read_text(encoding="utf-8")
            return json.loads(text)
        except OSError as exc:
            self._logger.warning(
                "ExperienceReader: cannot read file %s: %s", path, exc
            )
            return None
        except json.JSONDecodeError as exc:
            self._logger.warning(
                "ExperienceReader: invalid JSON in file %s: %s", path, exc
            )
            return None
