"""Property-based test: log files must never contain a plaintext API key.

**Validates: Requirements 18.1**
"""
from __future__ import annotations

import logging
import tempfile
from pathlib import Path
from unittest.mock import patch

from hypothesis import given, settings as h_settings
from hypothesis import strategies as st

from pa_agent.util.mask_secret import mask_secret
import pa_agent.util.logging as logging_module


# ── Strategy ──────────────────────────────────────────────────────────────────

# API keys are at least 12 characters long (realistic minimum)
api_key_strategy = st.text(
    alphabet=st.characters(
        whitelist_categories=("Lu", "Ll", "Nd"),  # letters + digits
        whitelist_characters="-_",
    ),
    min_size=12,
)


def _close_root_handlers() -> None:
    """Close and remove all handlers from the root logger (and tracked third-party loggers)."""
    root = logging.getLogger()
    for handler in list(root.handlers):
        handler.flush()
        handler.close()
        root.removeHandler(handler)
    for name in ("urllib3", "openai", "httpx"):
        lg = logging.getLogger(name)
        for handler in list(lg.handlers):
            handler.flush()
            handler.close()
            lg.removeHandler(handler)


# ── Property ──────────────────────────────────────────────────────────────────


@given(api_key=api_key_strategy)
@h_settings(max_examples=50)
def test_log_file_never_contains_plaintext_key(api_key: str) -> None:
    """For any API key of length >= 12, the log file must not contain the
    plaintext key after configure_logging() is called with that key.

    The masked form (mask_secret(key)) must appear at least once.
    """
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp_dir:
        log_file = Path(tmp_dir) / "test_pa_agent.log"

        try:
            # Patch LOG_FILE_PATH so we write to a temp file, not the real log
            with patch.object(logging_module, "LOG_FILE_PATH", log_file):
                logging_module._configured = False  # noqa: SLF001
                _close_root_handlers()
                logging_module.configure_logging(api_key=api_key)

            # Emit a log message that contains the plaintext key
            test_logger = logging.getLogger("test.masking")
            test_logger.info("API key in use: %s", api_key)

            # Flush all handlers
            root = logging.getLogger()
            for handler in root.handlers:
                handler.flush()

            # Read the log file
            assert log_file.exists(), "Log file was not created"
            content = log_file.read_text(encoding="utf-8")

            # The plaintext key must NOT appear in the file
            assert api_key not in content, (
                f"Plaintext API key found in log file.\n"
                f"Key: {api_key!r}\n"
                f"Masked: {mask_secret(api_key)!r}\n"
                f"Log content snippet: {content[:500]!r}"
            )

            # The masked form MUST appear at least once
            masked = mask_secret(api_key)
            assert content.count(masked) >= 1, (
                f"Masked key not found in log file.\n"
                f"Key: {api_key!r}\n"
                f"Masked: {masked!r}\n"
                f"Log content snippet: {content[:500]!r}"
            )
        finally:
            # Close all file handlers so Windows can delete the temp directory
            _close_root_handlers()
