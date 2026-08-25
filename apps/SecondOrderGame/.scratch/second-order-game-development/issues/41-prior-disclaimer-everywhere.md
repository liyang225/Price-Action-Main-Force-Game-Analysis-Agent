# 41 — 统一展示先验声明

**What to build:** 在所有概率输出和用户结果页面持续展示“专家先验推演，非统计估计”，直到相应先验权重满足既定退出条件。

**Blocked by:** 12 — 统一概率结果契约; 32 — 打通 PA 端到端联动页面; 36 — 建立参数 GUI 基座与测试策略.

Status: ready-for-human
Delivery: complete (2026-08-12; automated verification complete, pending human acceptance)

- [x] PA 联动页、情景应对树和参数 GUI 的概率结果均展示统一声明。
- [x] 先验权重高于或等于 20% 时声明不可关闭或被滚动区域遮蔽。
- [x] 不同概率行按自身先验权重决定标注状态，不用全局猜测替代结果字段。

## Comments

### 2026-08-12 交付记录

- 新增统一 `disclaimer_for_prior_weight` / `annotate_probability_row` 策略，阈值固定为 20%。
- A 类行为预测与三情景树按各自概率行的先验权重生成声明；参数工作台预览返回逐参与者/行为的权重与声明映射，页面保留醒目声明。
- 先验权重达到 20% 时声明持续展示，低于阈值时仅该行可隐藏，不以全局状态覆盖单行结果。
