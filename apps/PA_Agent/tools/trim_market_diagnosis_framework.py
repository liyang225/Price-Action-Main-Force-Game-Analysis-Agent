"""One-off: strip stage-2 trade rules from 市场诊断框架.txt (stage-1 diagnosis only)."""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TARGET = ROOT / "prompt_engineering" / "市场诊断框架.txt"

# Block headers that end a removable 「对应决策怎么做」 section
_STOP_MARKERS = (
    "【状态",
    "【通用转换",
    "【三层框架",
    "【框架嵌套",
    "【分析流程】",
    "【例外与陷阱】",
    "【输出约定】",
    "## ",
    "清单",
    "四、判断",
    "五、多时间",
)


def _is_stop(line: str) -> bool:
    s = line.strip()
    if not s:
        return False
    for m in _STOP_MARKERS:
        if s.startswith(m):
            return True
    if s.startswith("【") and "对应决策怎么做" not in s and not s.startswith("【尖峰→"):
        # e.g. 【状态2…】, 【微型通道→…】 transition blocks stay
        if "→" in s and "对应决策" not in s:
            return False
        if s.startswith("【状态"):
            return True
    return False


def strip_decision_blocks(text: str) -> str:
    lines = text.splitlines()
    out: list[str] = []
    skipping = False
    for line in lines:
        if "对应决策怎么做" in line and line.strip().startswith("【"):
            skipping = True
            continue
        if skipping:
            if _is_stop(line):
                skipping = False
                out.append(line)
            continue
        out.append(line)
    return "\n".join(out)


def remove_line_blocks(text: str, start_pat: str, end_pat: str | None = None) -> str:
    """Remove from line matching start_pat until line matching end_pat (exclusive of end)."""
    lines = text.splitlines()
    out: list[str] = []
    skip = False
    for line in lines:
        if not skip and re.search(start_pat, line):
            skip = True
            continue
        if skip and end_pat and re.search(end_pat, line):
            skip = False
            out.append(line)
            continue
        if not skip:
            out.append(line)
    return "\n".join(out)


def main() -> None:
    raw = TARGET.read_text(encoding="utf-8-sig")
    text = strip_decision_blocks(raw)

    # Layer-1: role / intro (diagnosis-only)
    text = text.replace(
        "> 来源文件：`市场周期识别方法及对应决策怎么做.txt`",
        "> 任务：周期频谱定位（识别标准与交易含义；具体下单规则在阶段二策略文件中）",
    )
    text = re.sub(
        r"你是一位精通Al Brooks价格行为体系的交易分析师，专注于市场周期识别与对应决策。你的核心职责是判断当前市场在周期频谱中的位置——这是所有后续分析和交易决策的起点。你不预测方向，只评估概率分布；你不依赖指标，只解读K线结构。识别出周期位置后，你必须给出具体、可执行的对应决策。",
        "你是一位精通Al Brooks价格行为体系的交易分析师，专注于市场周期识别。你的核心职责是判断当前市场在周期频谱中的位置——这是阶段二策略路由与闸门判断的起点。你不预测方向，只评估概率分布；你不依赖指标，只解读K线结构。阶段一不制定具体下单、止损、止盈方案。",
        text,
        count=1,
    )
    text = text.replace(
        "- 你用具体的、可观察的K线特征来定义每种周期状态，同时给出具体的入场、止损、止盈决策。",
        "- 你用具体的、可观察的K线特征来定义每种周期状态，并说明该状态的交易含义与风险要点（非具体价位）。",
    )
    text = text.replace(
        "- 决策思维：识别周期位置不是目的，给出对应决策才是目的",
        "- 诊断思维：识别周期位置是阶段一目的；具体交易决策在阶段二由策略文件与二元决策树完成",
    )
    text = text.replace(
        '市场周期频谱由强到弱分为8种状态。每种状态不仅有"识别标准"和"交易含义"，更有详细的"对应决策怎么做"——告诉你具体怎么入场、止损、止盈，以及绝对不能做什么。',
        '市场周期频谱由强到弱分为8种状态。每种状态有「识别标准」与「交易含义」（方向偏好、常见陷阱、概率要点）；具体入场止损止盈见阶段二路由的策略文件。',
    )

    # Remove 清单E
    text = re.sub(
        r"\n清单E：对应决策评估（新增）\n(?:□[^\n]+\n)+",
        "\n",
        text,
    )
    text = text.replace(
        "综合以上检查，得出当前市场在周期频谱中的位置，并记录这个判断的置信度（高/中/低）。然后根据对应决策规则，制定具体的交易计划。",
        "综合以上检查，得出当前市场在周期频谱中的位置，并记录 diagnosis_confidence（高/中/低）。在 JSON 的 strategy_files_needed 中列出阶段二需要的策略文件名。",
    )

    # Nested scenes: drop 「对应决策：」 lines
    text = re.sub(r"\n对应决策：[^\n]+\n", "\n", text)

    # Remove 「三、周期转换时的决策调整规则」 through before 「【通用转换规则】」
    text = remove_line_blocks(
        text,
        r"^三、周期转换时的决策调整规则",
        r"^【通用转换规则】",
    )
    # Trim 通用转换 rules 5-6 (position management)
    text = re.sub(
        r"\n5\. ⚠️ 持仓继承规则[\s\S]*?5\. 转换后的第一笔交易用比正常小的仓位（验证新策略是否有效）\n",
        "\n",
        text,
        count=1,
    )

    # Remove 步骤6 and trade-heavy 步骤5 tail
    text = re.sub(
        r"\n步骤5：寻找小框架（LTF）的入场信号\n[\s\S]*?步骤6：制定对应决策（新增）\n[\s\S]*?步骤7：持续更新",
        "\n步骤5：在小框架（LTF）上确认是否有值得在阶段二评估的结构信号（不输出具体下单）\n\n步骤6：持续更新",
        text,
        count=1,
    )
    text = text.replace(
        "- 周期位置变化时，立即调整对应决策（参考\"周期转换时的决策调整规则\"）",
        "- 周期位置变化时，更新 cycle_position、direction、strategy_files_needed（§2.5 惯性不足不单独设 gate_result=wait，见二元决策 §2.5）",
    )

    # Remove entire 【实战规则】
    text = remove_line_blocks(text, r"^【实战规则】", r"^【例外与陷阱】")

    # Layer-2 header
    text = text.replace(
        "> 来源文件：`市场背景识别方法以及对应决策怎么做.txt`",
        "> 任务：市场背景评估（五维背景与信号可靠性；具体交易在阶段二）",
    )

    # Remove 「七、对应决策怎么做」 … until next 「【分析流程】」 in layer 2
    text = remove_line_blocks(text, r"^七、对应决策怎么做", r"^【分析流程】")

    # Layer-3 header
    text = text.replace(
        "> 来源文件：`逐K分析方法及对应决策怎么做.txt`",
        "> 任务：逐K信号确认（结构/信号/跟随判断；执行细节在阶段二）",
    )

    # Remove decision matrix under section 十
    text = re.sub(
        r"\n十、逐棒分析的决策逻辑与对应决策\n\n逐棒分析的核心：[^\n]+\n\n决策流程：\n(?:\d+\.[^\n]+\n)+对应决策矩阵：\n\n(?:[^\n]+\n)+",
        "\n十、逐棒分析的逻辑（阶段一）\n\n逐棒分析的核心：每根K线收盘后重新评估市场状态，更新诊断字段（不输出下单价位）。\n\n评估流程：\n"
        "1. 每根K线收盘 → 评估类型（趋势K线/十字星）\n"
        "2. 评估与前序K线关系（结构/潜在信号）\n"
        "3. 判断当前处于趋势/区间/转换\n"
        "4. 将结论写入 key_signals、entry_setup、detected_patterns\n\n",
        text,
        count=1,
    )

    # Simplify transition blocks: 策略： → 诊断提示：
    text = re.sub(r"^策略：", "诊断提示：", text, flags=re.MULTILINE)

    # Collapse excessive blank lines
    text = re.sub(r"\n{4,}", "\n\n\n", text)

    TARGET.write_text(text + "\n", encoding="utf-8")
    print(f"Wrote {TARGET} ({len(text)} chars)")


if __name__ == "__main__":
    main()
