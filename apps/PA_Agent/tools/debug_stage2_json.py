"""Debug stage2 JSON parse error."""
import json
import re
import sys

RAW = r''' {
  "decision": {
    "order_direction": null,
    "order_type": "不下单",
    "entry_price": null,
    "take_profit_price": null,
    "stop_loss_price": null,
    "reasoning": "阶段一确认 cycle_position=trending_tr，direction=bearish偏弱，HTF为多头回调阶段。进入阶段二后，结构分支走§6区间逻辑：当前K6-K1价格在4528-4540窄幅横盘，K线高度重叠，EMA20平坦并与价格纠缠，符合Barbwire特征（区间宽约12点，紧凑度极高）。按§6.4规则，Barbwire中不交易，需等待尖峰级别突破。\\n\\n入场信号检查（§9）：K1为最近K线，收盘位置在4528-4540区间内部，无明确方向性信号棒，既不是区间下边界的看涨反转棒，也不是上边界的看跌信号棒。9.2方向一致性不满足。\\n\\n即使勉强识别信号，§10.1止损位置不明确（区间内部无法锁定信号棒极点为结构化止损），且§14禁止行为中\"在区间中部入场\"和\"在极端交易区间中交易\"均被触发。\\n\\n激进档位在此场景下：gate_result=proceed，趋势方向偏空，但Barbwire触发§14硬性禁止项（在极端密集区间中交易），即使激进档也必须不下单。等待价格以尖峰形式突破4518支撑（空头确立）或站稳4540/EMA附近（多头反弹），届时再评估下单。",
    "diagnosis_confidence": 62,
'''

# User pasted full JSON in query - read from stdin file if provided
if len(sys.argv) > 1:
    RAW = open(sys.argv[1], encoding="utf-8").read()

from pa_agent.ai.json_validator import _strip_fences

text = RAW if len(sys.argv) > 1 else open(
    r"D:\cl\PA_Agent\tools\stage2_raw_sample.txt", encoding="utf-8"
).read()

stripped = _strip_fences(text)
print("stripped len", len(stripped))
lines = stripped.splitlines()
for i, line in enumerate(lines[:15], 1):
    print(f"{i:3d} len={len(line)} | {line[:120]}...")

try:
    json.loads(stripped)
    print("OK")
except json.JSONDecodeError as e:
    print("ERR", e.lineno, e.colno, e.msg)
    if 1 <= e.lineno <= len(lines):
        bad = lines[e.lineno - 1]
        print("line:", bad[max(0, e.colno - 40) : e.colno + 40])
