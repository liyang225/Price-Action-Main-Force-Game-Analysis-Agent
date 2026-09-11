"""Unit tests for the prototype-styled second-order analysis cards.

Covers the data -> render mapping contract of ``second_order_cards.py``:

- each card component (``_SummaryBand`` / ``_BeliefBar`` / ``_RangeBar`` /
  ``_BehaviorBars`` / ``_ScenarioCards``) must render the documented payload
  shapes, including the ``None`` / empty values used by the pre-run skeleton;
- ``PrototypeAnalysisPanel`` must keep the field-level contract of
  ``_AnalysisResultPanel`` (``set_payload`` / ``set_grouped_payload`` /
  ``set_table_sections``) intact while routing each page to its layout.
"""
from __future__ import annotations

import os

import pytest

pytest.importorskip("PyQt6")

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import QEvent  # noqa: E402
from PyQt6.QtWidgets import QApplication, QLabel, QPlainTextEdit, QPushButton  # noqa: E402

from pa_agent.gui.second_order_cards import (  # noqa: E402
    PrototypeAnalysisPanel,
    _BehaviorBars,
    _BeliefBar,
    _FourColumnGrid,
    _RangeBar,
    _ScenarioCards,
    _SummaryBand,
    _scenario_convention,
)


def _label_texts(widget) -> list[str]:
    """All descendant QLabel texts (in widget-tree order)."""
    return [label.text() for label in widget.findChildren(QLabel)]


_PRIORS = {
    "主力": {
        "建仓": 0.361,
        "震仓": 0.119,
        "拉升": 0.31,
        "出货": 0.067,
        "观望": 0.124,
        "狩猎止损": 0.019,
    },
    "散户": {
        "FOMO追高": 0.083,
        "恐慌割肉": 0.18,
        "观望": 0.258,
        "理性跟随": 0.174,
        "底部建仓": 0.25,
        "高位减仓": 0.056,
    },
}

_EMPTY_PRIORS = {
    "主力": {
        "建仓": None,
        "震仓": None,
        "拉升": None,
        "出货": None,
        "观望": None,
        "狩猎止损": None,
    },
    "散户": {
        "FOMO追高": None,
        "恐慌割肉": None,
        "观望": None,
        "理性跟随": None,
        "底部建仓": None,
        "高位减仓": None,
    },
}

_POSTERIORS = {
    "主力": {
        "建仓": 0.45,
        "震仓": 0.11,
        "拉升": 0.28,
        "出货": 0.05,
        "观望": 0.09,
        "狩猎止损": 0.02,
    },
    "散户": {
        "FOMO追高": 0.14,
        "恐慌割肉": 0.11,
        "观望": 0.21,
        "理性跟随": 0.24,
        "底部建仓": 0.23,
        "高位减仓": 0.07,
    },
}


# ---------------------------------------------------------------------------
# _SummaryBand
# ---------------------------------------------------------------------------

def test_summary_band_renders_value_label_status_note(qtbot) -> None:
    band = _SummaryBand(50.0, label="情绪指数", status_text="已计算", note="消息增量 +0.42")
    qtbot.addWidget(band)
    texts = _label_texts(band)
    assert "50.00" in texts
    assert "情绪指数" in texts
    assert "已计算" in texts
    assert "消息增量 +0.42" in texts


def test_summary_band_missing_value_renders_dash(qtbot) -> None:
    band = _SummaryBand(None, label="情绪指数", status_text="等待推演")
    qtbot.addWidget(band)
    texts = _label_texts(band)
    assert "—" in texts
    assert "等待推演" in texts


def test_summary_band_keeps_score_column_and_copy_block(qtbot) -> None:
    band = _SummaryBand(50.0, label="情绪指数", status_text="已计算")
    qtbot.addWidget(band)
    # 根布局：固定大数字块 + 弹性说明块
    assert band.layout().count() == 2


# ---------------------------------------------------------------------------
# _BeliefBar
# ---------------------------------------------------------------------------

def test_belief_bar_renders_states_in_prototype_order(qtbot) -> None:
    belief = {
        "冰点": 0.2535,
        "发酵": 0.1763,
        "启动": 0.4952,
        "退潮": 0.0333,
        "高潮": 0.0417,
    }
    bar = _BeliefBar(belief)
    qtbot.addWidget(bar)
    texts = _label_texts(bar)
    for name in ("冰点", "发酵", "启动", "退潮", "高潮"):
        assert name in texts, f"missing {name!r} in {texts}"
    assert "49.5%" in texts  # 启动 0.4952
    assert "25.4%" in texts  # 冰点 0.2535


def test_belief_bar_orders_by_prototype_hint_even_for_shuffled_dict(qtbot) -> None:
    belief = {
        "高潮": 0.04,
        "冰点": 0.25,
        "启动": 0.49,
        "退潮": 0.03,
        "发酵": 0.18,
    }
    bar = _BeliefBar(belief)
    qtbot.addWidget(bar)
    order = [text for text in _label_texts(bar) if text in ("冰点", "发酵", "启动", "退潮", "高潮")]
    assert order == ["冰点", "发酵", "启动", "退潮", "高潮"]


def test_belief_bar_empty_renders_five_placeholders(qtbot) -> None:
    bar = _BeliefBar({})
    qtbot.addWidget(bar)
    texts = _label_texts(bar)
    for name in ("冰点", "发酵", "启动", "退潮", "高潮"):
        assert name in texts
    assert texts.count("—") == 5


def test_belief_bar_unknown_states_appended_after_hint(qtbot) -> None:
    belief = {"冰点": 0.5, "启动": 0.5, "额外档": 0.0}
    bar = _BeliefBar(belief)
    qtbot.addWidget(bar)
    assert "额外档" in _label_texts(bar)


# ---------------------------------------------------------------------------
# _RangeBar
# ---------------------------------------------------------------------------

def test_range_bar_renders_three_values_and_position(qtbot) -> None:
    bar = _RangeBar("1.365", "1.404", "1.444", position_text="价格位于均衡带内")
    qtbot.addWidget(bar)
    texts = _label_texts(bar)
    assert "1.365" in texts
    assert "1.404" in texts
    assert "1.444" in texts
    assert "价格位于均衡带内" in texts


def test_range_bar_accepts_numeric_values(qtbot) -> None:
    bar = _RangeBar(1.365, 1.404, 1.444)
    qtbot.addWidget(bar)
    texts = _label_texts(bar)
    assert "1.365" in texts and "1.404" in texts and "1.444" in texts


def test_range_bar_missing_values_renders_dashes(qtbot) -> None:
    bar = _RangeBar(None, None, None)
    qtbot.addWidget(bar)
    texts = _label_texts(bar)
    assert "—" in texts
    assert len([t for t in texts if t == "—"]) >= 3


# ---------------------------------------------------------------------------
# _BehaviorBars
# ---------------------------------------------------------------------------

def test_behavior_bars_renders_participants_and_probabilities(qtbot) -> None:
    bars = _BehaviorBars(_PRIORS)
    qtbot.addWidget(bars)
    texts = _label_texts(bars)
    assert "主力" in texts and "散户" in texts
    assert "36.1%" in texts  # 主力建仓
    assert "25.8%" in texts  # 散户观望
    assert "FOMO追高" in texts
    assert "狩猎止损" in texts


def test_behavior_bars_tolerates_none_probabilities(qtbot) -> None:
    bars = _BehaviorBars(_EMPTY_PRIORS)
    qtbot.addWidget(bars)
    texts = _label_texts(bars)
    assert "主力" in texts and "散户" in texts
    assert texts.count("—") >= 12  # 6 行为 × 2 参与者


def test_behavior_bars_empty_participant_map_renders_empty(qtbot) -> None:
    bars = _BehaviorBars({})
    qtbot.addWidget(bars)
    assert bars.layout().count() == 0


# ---------------------------------------------------------------------------
# _ScenarioCards
# ---------------------------------------------------------------------------

def test_scenario_cards_renders_main_and_alternatives(qtbot) -> None:
    branches = [
        {
            "情景": "符合预期",
            "下一完整时段概率": "99.7%",
            "开盘首次下跌达止损概率": "暂无数据",
            "状态": "待确认",
            "应对": "保持观望，不因小波动行动",
        },
        {
            "情景": "超预期强",
            "下一完整时段概率": "0.1%",
            "开盘首次下跌达止损概率": "暂无数据",
            "状态": "待确认",
            "应对": "不追高，防范脉冲回落",
        },
        {
            "情景": "低于预期",
            "下一完整时段概率": "0.1%",
            "开盘首次下跌达止损概率": "暂无数据",
            "状态": "待确认",
            "应对": "弱承接则回避，破位离场",
        },
    ]
    cards = _ScenarioCards(branches)
    qtbot.addWidget(cards)
    texts = _label_texts(cards)
    assert "99.7%" in texts
    assert "符合预期" in texts
    assert "超预期强" in texts and "低于预期" in texts
    assert "保持观望，不因小波动行动" in texts
    assert "历史样本频率" in texts
    # ADR-0030：情景名是契约标识，数字是「下一完整时段」分桶频率，必须标出口径，
    # 否则「超预期强 44%」会被读成次日大幅高开的预测。
    assert any("次日上午收盘较今日收盘" in text for text in texts)
    assert any("不是次日开盘跳空预测" in text for text in texts)


def test_scenario_convention_defines_every_bucket() -> None:
    assert "≥ +1%" in _scenario_convention("超预期强")
    assert "-1% ~ +1%" in _scenario_convention("符合预期")
    assert "≤ -1%" in _scenario_convention("低于预期")
    # 未知情景名仍给出口径行，不静默退化成没有口径的裸百分数。
    assert _scenario_convention(None) == "口径：下一完整时段收益分桶"
    assert _scenario_convention("未知情景") == "口径：下一完整时段收益分桶"


def test_scenario_cards_empty_renders_placeholder(qtbot) -> None:
    cards = _ScenarioCards([])
    qtbot.addWidget(cards)
    assert any("暂无情景分支" in text for text in _label_texts(cards))


# ---------------------------------------------------------------------------
# PrototypeAnalysisPanel — data -> render mapping contract
# ---------------------------------------------------------------------------

def test_cycle_page_maps_contract_fields(qtbot) -> None:
    panel = PrototypeAnalysisPanel("cycle")
    qtbot.addWidget(panel)
    panel.set_grouped_payload(
        [
            [
                ("情绪指数", 50.0, 1),
                ("情绪指数计算公式", "基准 50.0 + 当日净增量（限 ±15.0）", 2),
            ],
            [
                (
                    "情绪指数明细",
                    {
                        "status": "computed",
                        "previous_index": 49.8,
                        "news_delta": 0.4,
                        "price_action_delta": -0.2,
                        "daily_delta": 0.2,
                        "daily_return": 0.5,
                        "two_day_return": 1.2,
                    },
                    2,
                )
            ],
            [
                (
                    "LLM 周期观测",
                    {
                        "status": "ok",
                        "cycle_position": "发酵",
                        "cycle_event": "平台整理",
                        "confidence": "中",
                        "consensus_state": "分歧",
                        "consensus_direction": "转强",
                        "role": "hmm_noise_sensor",
                    },
                    2,
                )
            ],
            [
                (
                    "HMM 后验信念",
                    {"冰点": 0.2535, "发酵": 0.1763, "启动": 0.4952, "退潮": 0.0333, "高潮": 0.0417},
                    2,
                )
            ],
        ],
        {"raw": True},
    )
    texts = _label_texts(panel)
    assert "50.00" in texts
    assert "已计算" in texts
    # 情绪指数明细 已并入 summary band 的 note，不再是独立卡片。
    assert any("消息增量 +0.40" in text for text in texts)
    assert "AI 原始判断" in texts
    assert "发酵" in texts  # 大模型原始标签
    assert "49.5%" in texts  # HMM 信念
    # band + state/belief grid + formula + stretch
    assert panel._cards_layout.count() == 4


def test_cycle_page_pending_status_maps_to_waiting(qtbot) -> None:
    panel = PrototypeAnalysisPanel("cycle")
    qtbot.addWidget(panel)
    panel.set_grouped_payload(
        [
            [("情绪指数", None, 1), ("情绪指数计算公式", "等待推演", 2)],
            [("情绪指数明细", {"status": "pending"}, 2)],
            [("LLM 周期观测", "等待大模型给出周期观测", 2)],
            [("HMM 后验信念", {}, 2)],
        ],
        {"raw": True},
    )
    texts = _label_texts(panel)
    assert "等待推演" in texts
    assert "等待观测" in texts  # 观测载荷缺失时的占位
    assert "等待 HMM 更新" in texts
    assert panel._cards_layout.count() == 4


def test_cycle_page_separates_raw_llm_judgment_from_hmm_calibrated_cycle(qtbot) -> None:
    """The raw LLM label and the HMM posterior mode must not share a label.

    ``cycle_position`` is the noisy-sensor read-out and ``sector_belief``'s
    arg-max is the program's calibrated cycle, so the card names them
    ``AI 原始判断`` and ``HMM 校准判断`` instead of the old 观测/有效 wording.
    """
    panel = PrototypeAnalysisPanel("cycle")
    qtbot.addWidget(panel)
    panel.set_grouped_payload(
        [
            [("情绪指数", 50.0, 1), ("情绪指数计算公式", "基准 50.0", 2)],
            [("情绪指数明细", {"status": "computed"}, 2)],
            [
                (
                    "LLM 周期观测",
                    {
                        "status": "ok",
                        "cycle_position": "发酵",
                        "cycle_event": "平台整理",
                        "confidence": "中",
                        "consensus_state": "分歧",
                        "consensus_direction": "转强",
                        "role": "hmm_noise_sensor",
                    },
                    2,
                )
            ],
            [
                (
                    "HMM 后验信念",
                    {"冰点": 0.2535, "发酵": 0.1763, "启动": 0.4952, "退潮": 0.0333, "高潮": 0.0417},
                    2,
                )
            ],
        ],
        {"raw": True},
    )
    texts = _label_texts(panel)
    assert "AI 原始判断" in texts
    assert "HMM 校准判断（最终采用）" in texts
    assert "发酵" in texts  # raw LLM label
    assert "启动" in texts  # HMM posterior mode
    assert "观测周期" not in texts
    assert "有效周期" not in texts
    assert "降级" not in "".join(texts)


def test_cycle_page_flags_fallback_pa_as_non_observation(qtbot) -> None:
    """``fallback_pa`` reuses a program cycle position, so the card must say so."""
    panel = PrototypeAnalysisPanel("cycle")
    qtbot.addWidget(panel)
    panel.set_grouped_payload(
        [
            [("情绪指数", 50.0, 1), ("情绪指数计算公式", "基准 50.0", 2)],
            [("情绪指数明细", {"status": "computed"}, 2)],
            [
                (
                    "LLM 周期观测",
                    {
                        "status": "fallback_pa",
                        "cycle_position": "发酵",
                        "key_evidence": [],
                        "reason": "ModelRequest timed out after 30.0s",
                    },
                    2,
                )
            ],
            [("HMM 后验信念", {"冰点": 0.5, "启动": 0.5}, 2)],
        ],
        {"raw": True},
    )
    texts = _label_texts(panel)
    joined = "".join(texts)
    assert "发酵（降级，非大模型输出）" in texts
    assert "大模型周期判断本次不可用" in joined
    assert "ModelRequest timed out after 30.0s" in joined


def test_game_page_maps_contract_fields(qtbot) -> None:
    panel = PrototypeAnalysisPanel("game")
    qtbot.addWidget(panel)
    panel.resize(1200, 800)
    panel.show()
    panel.set_payload(
        {
            "程序化博弈信号": {
                "纳什均衡带": {"中心": "1.404", "上沿": "1.444", "下沿": "1.365", "价格位置": "带内"},
                "羊群行为": {"羊群买入": "否", "羊群卖出": "否", "RSI": "49.08", "异常放量": "否"},
                "聪明钱指数": {"净流入为正": "否"},
                "机构资金": {"吸筹": "否", "派发": "否"},
                "流动性陷阱": {"上方陷阱": "否", "下方陷阱": "否"},
                "反向/动量/回归信号": {
                    "逆势买入": "否",
                    "逆势卖出": "否",
                    "动量买入": "否",
                    "动量卖出": "否",
                    "回归买入": "否",
                    "回归卖出": "否",
                },
            },
            "参与者识别": {"participant": "散户", "key_evidence": ["成交量未放大，情绪指数中性"]},
            "参与者先验": _PRIORS,
            "参与者后验": _POSTERIORS,
            "当前行为与 A 类概率": {
                "散户": {"model_behavior": "观望", "probabilities": {"观望": 0.258, "底部建仓": 0.25}, "prior_weight": 1.0}
            },
        },
        {"raw": True},
    )
    texts = _label_texts(panel)
    assert "1.365" in texts and "1.404" in texts and "1.444" in texts
    assert "价格位于均衡带内" in texts
    assert "未触发" in texts  # 否 -> 未触发
    assert "参与者识别" in texts
    assert "主导参与者" in texts
    assert "36.1%" in texts
    assert "25.8%" in texts
    assert "当下参与者行为推断（HMM行为先验）" in texts
    assert "当下参与者行为推断（HMM 行为后验）" in texts
    assert "当前散户行为" in texts
    assert "45.0%" in texts
    assert "24.0%" in texts
    forecast_grid = panel.findChild(_FourColumnGrid)
    assert forecast_grid is not None
    assert forecast_grid.layout_.count() == 4
    qtbot.wait(20)
    cards = [forecast_grid.layout_.itemAt(index).widget() for index in range(4)]
    assert len({card.y() for card in cards}) == 1
    assert all(card.width() > 0 for card in cards)
    assert {forecast_grid.layout_.itemAt(index).widget().layout().itemAt(0).widget().text() for index in range(4)} == {
        "参与者",
        "当前散户行为",
        "A 类概率（程序计算）",
        "先验权重",
    }


def test_game_page_empty_payload_renders_full_skeleton(qtbot) -> None:
    panel = PrototypeAnalysisPanel("game", "等待推演…")
    qtbot.addWidget(panel)
    texts = _label_texts(panel)
    assert "纳什均衡带" in texts
    assert "参与者识别" in texts
    assert "当下参与者行为推断（HMM行为先验）" in texts
    assert "当下参与者行为推断（HMM 行为后验）" in texts
    assert "当前行为与 A 类概率" in texts
    assert "建仓" in texts and "FOMO追高" in texts  # 行为条骨架
    assert panel._cards_layout.count() >= 2


def test_tree_page_maps_contract_fields(qtbot) -> None:
    panel = PrototypeAnalysisPanel("tree")
    qtbot.addWidget(panel)
    panel.set_payload(
        {
            "B/C三情景概率": [
                {
                    "情景": "符合预期",
                    "下一完整时段概率": "99.7%",
                    "开盘首次下跌达止损概率": "暂无数据",
                    "状态": "待确认",
                    "应对": "保持观望",
                },
                {
                    "情景": "超预期强",
                    "下一完整时段概率": "0.1%",
                    "开盘首次下跌达止损概率": "暂无数据",
                    "状态": "待确认",
                    "应对": "不追高",
                },
                {
                    "情景": "低于预期",
                    "下一完整时段概率": "0.1%",
                    "开盘首次下跌达止损概率": "暂无数据",
                    "状态": "待确认",
                    "应对": "回避",
                },
            ]
        },
        {"raw": True},
    )
    texts = _label_texts(panel)
    assert "99.7%" in texts
    assert "符合预期" in texts
    assert "超预期强" in texts and "低于预期" in texts


def test_tree_page_empty_renders_three_scenario_placeholders(qtbot) -> None:
    panel = PrototypeAnalysisPanel("tree", "等待推演…")
    qtbot.addWidget(panel)
    texts = _label_texts(panel)
    for name in ("符合预期", "超预期强", "低于预期"):
        assert name in texts
    assert texts.count("—") >= 3  # 三张卡概率占位


def test_tree_page_shows_the_behavior_tendency_of_each_scenario(qtbot) -> None:
    """§10.2：每个情景都要给出该情景下参与者最可能的行为。"""
    panel = PrototypeAnalysisPanel("tree")
    qtbot.addWidget(panel)
    panel.set_payload(
        {
            "B/C三情景概率": [
                {
                    "情景": "符合预期",
                    "下一完整时段概率": "60.0%",
                    "状态": "待确认",
                    "行为倾向": "继续派发为主；观望为次（触发：高位横盘）",
                    "风险": "主力继续派发，随时转折",
                    "应对": "高位不接盘",
                },
                {
                    "情景": "超预期强",
                    "下一完整时段概率": "25.0%",
                    "状态": "待确认",
                    "行为倾向": "继续借利好派发（触发：新增重磅利好）",
                    "风险": "最后一波拉升后崩盘",
                    "应对": "不追高",
                },
                {
                    "情景": "低于预期",
                    "下一完整时段概率": "15.0%",
                    "状态": "待确认",
                    # 没有行为倾向与风险时不占位
                    "应对": "快速离场",
                },
            ]
        },
        {"raw": True},
    )
    texts = _label_texts(panel)
    assert "行为倾向：继续派发为主；观望为次（触发：高位横盘）" in texts
    assert "行为倾向：继续借利好派发（触发：新增重磅利好）" in texts
    assert sum(1 for text in texts if text.startswith("行为倾向：")) == 2
    assert "风险：主力继续派发，随时转折" in texts
    assert "风险：最后一波拉升后崩盘" in texts
    assert sum(1 for text in texts if text.startswith("风险：")) == 2


def test_tree_page_trade_rules_toggle_edit_and_save(qtbot) -> None:
    saved: list[str] = []
    panel = PrototypeAnalysisPanel("tree")
    qtbot.addWidget(panel)
    panel.set_trade_rules("初始规则", lambda text: saved.append(text) or True)

    editor = panel.findChildren(QPlainTextEdit, "secondOrderTradeRulesInput")[-1]
    action = panel.findChildren(QPushButton, "secondOrderTradeRulesAction")[-1]
    assert editor.isReadOnly()
    assert action.text() == "修改"
    assert "我的交易规则" in _label_texts(panel)

    action.click()
    assert not editor.isReadOnly()
    assert action.text() == "保存"
    editor.setPlainText("单笔亏损不超过总资金的 1%。")
    action.click()

    assert saved == ["单笔亏损不超过总资金的 1%。"]
    assert editor.isReadOnly()
    assert action.text() == "修改"


def test_sector_page_maps_contract_fields(qtbot) -> None:
    panel = PrototypeAnalysisPanel("sector")
    qtbot.addWidget(panel)
    panel.set_grouped_payload(
        [
            [
                (
                    "板块结构",
                    {
                        "sector_name": "半导体",
                        "sector_code": "SH.BK0001",
                        "sentiment_index": 50.0,
                        "cycle_position": "启动",
                        "cycle_position_source": "hmm_posterior",
                        "llm_observation": "发酵",
                    },
                    3,
                )
            ],
            [
                ("政策环境", "政策暖风", 1),
                (
                    "政策检测",
                    {
                        "状态": "detected",
                        "检测环境": "政策暖风",
                        "证据链": [{"渠道": "软信号", "摘要": "命中词：降准"}],
                    },
                    2,
                ),
            ],
        ],
        {"raw": True},
    )
    texts = _label_texts(panel)
    assert "半导体" in texts
    assert "SH.BK0001" in texts
    assert "政策暖风" in texts
    assert "周期位置" in texts
    assert "启动" in texts  # sector_analysis.cycle_position（HMM 校准判断）
    assert "AI 原始判断" in texts
    assert "发酵" in texts  # sector_analysis.llm_observation（大模型原始标签）
    assert "软信号：命中词：降准" in texts
    # 板块结构 + 政策环境 两张卡：HMM 行为先验与新闻材料都不在本页契约内
    assert panel._cards_layout.count() == 2  # grid + stretch


def test_sector_data_cards_precede_policy_environment(qtbot) -> None:
    panel = PrototypeAnalysisPanel("sector")
    qtbot.addWidget(panel)
    panel.set_grouped_payload(
        [
            [("板块结构", {"sector_name": "半导体"}, 3)],
            [("政策环境", "政策暖风", 1)],
        ],
        {},
    )
    panel.set_table_sections(
        [
            {"title": "资金流向", "headers": [], "rows": []},
            {"title": "连板信息", "headers": [], "rows": []},
            {"title": "龙虎榜", "headers": [], "rows": []},
        ]
    )
    QApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)

    assert panel._cards_layout.indexOf(panel._sector_grid) >= 0
    layout = panel._sector_grid.layout_
    assert "资金流向" in _label_texts(layout.itemAtPosition(0, 0).widget())
    assert "连板信息" in _label_texts(layout.itemAtPosition(1, 0).widget())
    assert "龙虎榜" in _label_texts(layout.itemAtPosition(1, 1).widget())
    assert "板块状态" in _label_texts(layout.itemAtPosition(2, 0).widget())
    assert "结构结论" in _label_texts(layout.itemAtPosition(3, 0).widget())
    assert "政策环境" in _label_texts(layout.itemAtPosition(4, 0).widget())


def test_market_page_maps_contract_fields(qtbot) -> None:
    panel = PrototypeAnalysisPanel("market")
    qtbot.addWidget(panel)
    panel.set_grouped_payload(
        [
            [("状态", "ok", 1), ("来源", "DSA", 1)],
            [("DSA 数据日期", "2026-08-15", 1), ("本次决策日期", "2026-08-16", 1)],
            [
                (
                    "模块化大盘分析说明",
                    {
                        "模块化大盘分析": [{"title": "市场结论", "content": "偏暖震荡"}],
                        "说明": "同日数据可用",
                    },
                    4,
                )
            ],
        ],
        {"raw": True},
    )
    texts = _label_texts(panel)
    assert "ok" in texts
    assert "DSA" in texts
    assert "2026-08-15" in texts
    assert "市场结论" in texts
    assert "偏暖震荡" in texts
    assert panel._cards_layout.count() == 3  # fact-grid + section card + stretch


def test_market_page_empty_renders_waiting_card(qtbot) -> None:
    panel = PrototypeAnalysisPanel("market", "等待推演…")
    qtbot.addWidget(panel)
    texts = _label_texts(panel)
    assert any("等待读取 DSA 大盘分析缓存" in text for text in texts)
    assert panel._cards_layout.count() == 3  # fact-grid + waiting card + stretch


@pytest.mark.parametrize("page", ["cycle", "game", "tree", "sector", "market"])
def test_initial_state_renders_full_skeleton(qtbot, page) -> None:
    panel = PrototypeAnalysisPanel(page, "等待推演…")
    qtbot.addWidget(panel)
    # 骨架已渲染：至少内容块 + stretch
    assert panel._cards_layout.count() >= 2
    # 推演数据填充前不渲染"等待"占位卡（而是完整布局）
    assert all(
        "等待推演…" not in label.text()
        for label in panel.findChildren(QLabel)
    )
