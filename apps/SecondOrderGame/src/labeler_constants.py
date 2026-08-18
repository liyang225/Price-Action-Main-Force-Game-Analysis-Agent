"""
SecondOrderGame — 共享常量定义

供标注器、校验器、HMM 引擎共同引用，避免重复定义导致的枚举分叉。
"""

CYCLE_STATES = ("冰点", "启动", "发酵", "高潮", "退潮")

MAIN_FORCE_BEHAVIORS = ("建仓", "震仓", "拉升", "出货", "观望", "狩猎止损")

RETAIL_BEHAVIORS = ("FOMO追高", "恐慌割肉", "观望", "理性跟随", "底部建仓", "高位减仓")

# The stock labeler describes main-force price behavior, so its historical
# public vocabulary stays stable while HMM consumers use participant-specific
# vocabularies.
BEHAVIORS = list(MAIN_FORCE_BEHAVIORS)

PARTICIPANTS = ["主力", "散户"]

BEHAVIORS_BY_PARTICIPANT = {
    "主力": MAIN_FORCE_BEHAVIORS,
    "散户": RETAIL_BEHAVIORS,
}


def behaviors_for(participant: str) -> tuple[str, ...]:
    try:
        return BEHAVIORS_BY_PARTICIPANT[participant]
    except KeyError as exc:
        raise ValueError(f"未知参与者：{participant!r}") from exc
