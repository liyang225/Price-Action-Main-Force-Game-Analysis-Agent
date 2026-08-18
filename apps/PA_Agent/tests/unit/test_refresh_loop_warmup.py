"""RefreshLoop must fetch enough bars for indicator warmup."""
from __future__ import annotations

import ast
from pathlib import Path


def test_refresh_loop_snapshot_count_includes_indicator_warmup() -> None:
    src = Path(__file__).resolve().parents[2] / "pa_agent" / "data" / "refresh_loop.py"
    tree = ast.parse(src.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if not (
            isinstance(func, ast.Attribute)
            and func.attr == "latest_snapshot"
            and isinstance(func.value, ast.Attribute)
            and func.value.attr == "_source"
        ):
            continue
        assert len(node.args) == 1
        arg = node.args[0]
        assert isinstance(arg, ast.BinOp)
        assert isinstance(arg.op, ast.Add)
        break
    else:
        raise AssertionError("latest_snapshot call not found")

    # n_bars + INDICATOR_WARMUP_BARS + 5
    assert isinstance(arg.left, ast.BinOp)
    assert isinstance(arg.left.op, ast.Add)
    assert isinstance(arg.left.left, ast.Attribute)
    assert arg.left.left.attr == "_n_bars"
