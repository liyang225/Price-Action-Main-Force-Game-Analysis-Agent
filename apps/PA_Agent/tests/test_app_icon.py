"""Tests for pa_agent.util.app_icon and the built icon assets."""
from __future__ import annotations

import os

import pytest

from pa_agent.util import app_icon

_EXPECTED_SIZES = {16, 24, 32, 48, 64, 128, 256}


def test_icon_assets_exist():
    ico = os.path.join(app_icon._ASSETS_DIR, "pa-agent.ico")
    svg = os.path.join(app_icon._ASSETS_DIR, "pa-agent-icon.svg")
    small = os.path.join(app_icon._ASSETS_DIR, "pa-agent-icon-small.svg")
    assert os.path.isfile(ico), "run tools/build_icon.py to generate pa-agent.ico"
    assert os.path.isfile(svg)
    assert os.path.isfile(small)


def test_ico_contains_all_sizes():
    from PIL import Image

    ico = os.path.join(app_icon._ASSETS_DIR, "pa-agent.ico")
    if not os.path.isfile(ico):
        pytest.skip("pa-agent.ico not built")
    with Image.open(ico) as img:
        sizes = {s[0] for s in img.info.get("sizes", set())}
    assert _EXPECTED_SIZES <= sizes


def test_install_app_icon_sets_window_icon(qapp):
    icon = app_icon.install_app_icon(qapp)
    assert not icon.isNull()
    assert not qapp.windowIcon().isNull()
    available = {s.width() for s in qapp.windowIcon().availableSizes()}
    assert _EXPECTED_SIZES <= available
