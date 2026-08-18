"""Regression checks for the sector-cycle terminology used by prompts."""

import json
from pathlib import Path
import re


ROOT = Path(__file__).resolve().parents[1]
PROMPT_ROOT = ROOT / "prompt_engine"
PROMPT = ROOT / "prompt_engine" / "通用" / "情绪周期判断.txt"
CONTEXT = ROOT / "CONTEXT.md"
ARCHITECTURE = ROOT / "ARCHITECTURE.md"


def test_cycle_prompt_keeps_platform_as_an_event_not_a_sixth_state() -> None:
    content = PROMPT.read_text(encoding="utf-8")

    assert '"cycle_event": "无"' in content
    assert "无|平台整理|二次启动|加速|高位兑现|破位转弱" in content
    assert "高潮→发酵" in content
    assert "不新增第六个周期位置" in content
    assert "不跳过中间状态" not in content


def test_domain_context_defines_platform_transition_boundary() -> None:
    content = CONTEXT.read_text(encoding="utf-8")

    assert "**情绪周期事件**" in content
    assert "平台整理" in content
    assert "二次启动" in content
    assert "不是 HMM 隐状态" in content


def test_prompts_do_not_request_model_generated_probability_fields() -> None:
    for path in PROMPT_ROOT.rglob("*.txt"):
        content = path.read_text(encoding="utf-8")
        assert '"probability_change"' not in content, path
        assert "概率变化:" not in content, path
        assert "标注各路径的概率" not in content, path


def test_prompts_do_not_recompute_program_owned_thresholds() -> None:
    forbidden = ("RSI>", "RSI<", "占比>60%", "占比>70%", "换手率>5%")
    for path in PROMPT_ROOT.rglob("*.txt"):
        content = path.read_text(encoding="utf-8")
        for fragment in forbidden:
            assert fragment not in content, (path, fragment)


def test_all_json_examples_are_strictly_parseable() -> None:
    for path in PROMPT_ROOT.rglob("*"):
        if path.suffix not in {".txt", ".md"}:
            continue
        content = path.read_text(encoding="utf-8")
        for block in re.findall(r"```json\s*\n(.*?)\n```", content, re.DOTALL):
            json.loads(block)


def test_architecture_records_cycle_event_as_orthogonal_output() -> None:
    content = ARCHITECTURE.read_text(encoding="utf-8")
    assert "结构事件" in content
    assert "平台整理" in content
    assert "不是 HMM 隐状态" in content


def test_participant_prompt_sets_cover_core_behaviors() -> None:
    main_force = {path.stem for path in (PROMPT_ROOT / "主力").glob("*.txt")}
    retail = {path.stem for path in (PROMPT_ROOT / "散户").glob("*.txt")}

    assert main_force == {"建仓", "震仓", "拉升", "出货", "观望", "狩猎止损"}
    assert retail == {
        "FOMO追高",
        "恐慌割肉",
        "观望",
        "理性跟随",
        "底部建仓",
        "高位减仓",
    }
