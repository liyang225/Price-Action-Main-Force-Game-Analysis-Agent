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

from PyQt6.QtWidgets import QLabel  # noqa: E402

from pa_agent.gui.second_order_cards import (  # noqa: E402
    PrototypeAnalysisPanel,
    _BehaviorBars,
    _BeliefBar,
    _RangeBar,
    _ScenarioCards,
    _SummaryBand,
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


# ---------------------------------------------------------------------------
# _SummaryBand
# ---------------------------------------------------------------------------

def test_summary_band_renders_value_label_status_note(qtbot) -> None:
    band = _SummaryBand(50.0, label="情绪指数", status_text="已计算", note="消息增量 +0.42")
    qtbot.addWidget(band)
    texts = _label_texts(band)
    assert "50.0" in texts
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
            "该情景明天开盘概率": "99.7%",
            "开盘首次下跌达止损概率": "暂无数据",
            "状态": "待确认",
            "应对": "保持观望，不因小波动行动",
        },
        {
            "情景": "超预期强",
            "该情景明天开盘概率": "0.1%",
            "开盘首次下跌达止损概率": "暂无数据",
            "状态": "待确认",
            "应对": "不追高，防范脉冲回落",
        },
        {
            "情景": "低于预期",
            "该情景明天开盘概率": "0.1%",
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
            [("LLM 周期观测", {"观测周期": "发酵", "置信度": "中"}, 2)],
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
    assert "50.0" in texts
    assert "已计算" in texts
    assert "0.40" in texts  # news_delta 0.4 -> 0.40
    assert "发酵" in texts  # LLM 观测
    assert "49.5%" in texts  # HMM 信念
    # band + formula + details + observation + belief + stretch
    assert panel._cards_layout.count() == 6


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
    assert panel._cards_layout.count() == 6


def test_game_page_maps_contract_fields(qtbot) -> None:
    panel = PrototypeAnalysisPanel("game")
    qtbot.addWidget(panel)
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
            "主导参与者行为推演": {
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
    assert panel._cards_layout.count() == 5  # 4 卡 + stretch


def test_game_page_empty_payload_renders_full_skeleton(qtbot) -> None:
    panel = PrototypeAnalysisPanel("game", "等待推演…")
    qtbot.addWidget(panel)
    texts = _label_texts(panel)
    assert "程序化博弈信号" in texts
    assert "参与者识别" in texts
    assert "HMM 行为先验（政策环境修正）" in texts
    assert "主导参与者行为推演" in texts
    assert "建仓" in texts and "FOMO追高" in texts  # 行为条骨架
    assert panel._cards_layout.count() == 5


def test_tree_page_maps_contract_fields(qtbot) -> None:
    panel = PrototypeAnalysisPanel("tree")
    qtbot.addWidget(panel)
    panel.set_payload(
        {
            "B/C三情景概率": [
                {
                    "情景": "符合预期",
                    "该情景明天开盘概率": "99.7%",
                    "开盘首次下跌达止损概率": "暂无数据",
                    "状态": "待确认",
                    "应对": "保持观望",
                },
                {
                    "情景": "超预期强",
                    "该情景明天开盘概率": "0.1%",
                    "开盘首次下跌达止损概率": "暂无数据",
                    "状态": "待确认",
                    "应对": "不追高",
                },
                {
                    "情景": "低于预期",
                    "该情景明天开盘概率": "0.1%",
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
                        "cycle_position_source": "llm_pending",
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
            [("HMM 行为先验（政策环境修正）", _PRIORS, 3)],
            [
                (
                    "新闻与事件材料",
                    {
                        "items": [
                            {
                                "title": "新闻A",
                                "sentiment_score": 0.5,
                                "snippet": "摘要文本",
                                "source": "财联社",
                            }
                        ]
                    },
                    1,
                )
            ],
        ],
        {"raw": True},
    )
    texts = _label_texts(panel)
    assert "半导体" in texts
    assert "SH.BK0001" in texts
    assert "政策暖风" in texts
    assert "36.1%" in texts
    assert "新闻A" in texts
    assert panel._cards_layout.count() == 5  # 4 卡 + stretch


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
