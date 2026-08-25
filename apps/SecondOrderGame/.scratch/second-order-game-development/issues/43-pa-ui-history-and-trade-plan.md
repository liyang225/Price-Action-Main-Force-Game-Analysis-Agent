# 43 — 修正二阶页面布局、历史路径与 PA 交易计划交接

**What to build:** 调整二阶博弈嵌入页的信息卡布局，把历史记录迁入项目根目录下的独立文件夹，并确保 PA 已给出的入场、止盈、止损计划能进入 C 类首达概率与 T+1 闸门。

Status: ready-for-human
Delivery: complete (2026-08-14; automated verification complete, pending human acceptance)

- [x] “政策识别”与“板块结构”同排显示；大盘“状态”与“来源”同排显示。
- [x] 通用设置的默认 K 线周期选项包含 `30m`。
- [x] 历史库默认位于 `SecondOrderGame/analysis_history/`，设置页可直接打开该文件夹，旧库自动迁移。
- [x] PA 做多或做空交易计划只要入场、止盈、止损完整，就不再误报“缺少止盈价或止损价”。

## Comments

### 2026-08-14 交付记录

- PA 交接契约新增止盈价与订单方向，C 类首达概率按多向/空向价格几何判断。
- 真实历史记录 515220 已重放确认：入场 1.251、止盈 1.233、止损 1.269 均完整进入二阶契约。
- 旧库 14 条记录已复制到 `analysis_history/second_order_history.db`，旧库未删除。
- SecondOrderGame 全量测试 500 通过、1 跳过；PA 相关 GUI/设置测试 44 通过；界面机械检查无问题。
