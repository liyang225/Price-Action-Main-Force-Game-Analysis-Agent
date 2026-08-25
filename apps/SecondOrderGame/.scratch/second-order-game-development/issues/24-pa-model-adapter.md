# 24 — 通过薄适配层复用 PA 模型客户端

**What to build:** 在二阶博弈与 PA 现有模型客户端之间建立可注入的薄适配层，使二阶博弈复用 PA 的模型能力但不依赖其内部调用细节。

**Blocked by:** None — can start immediately.

**Status:** ready-for-human

- [x] 生产适配器复用 PA 现有模型客户端，不新增独立供应商客户端或凭据配置。
- [x] 二阶博弈只依赖稳定的请求/响应边界，PA 客户端可由离线 fake 替换。
- [x] 响应必须通过结构化 schema 和固定枚举校验，模型返回的概率数字不会进入业务计算。
- [x] 超时、格式错误和非法枚举均成为明确失败，不被静默吞掉。

## Comments

### 2026-08-11 交付记录

- 在架构规定的 `src/integration/` 新增 `StructuredModelClient`、`ModelRequest`、`ModelResponse` 稳定边界；业务调用方不依赖 PA 内部路由、供应商 SDK 或凭据配置。
- 新增 `PAModelAdapter`，仅接收已经配置好的 PA `LLMToolAdapter` 兼容对象并调用其 `call_text`；离线 fake 可直接替换整个稳定边界。
- 所有输出 schema 必须继承 `StrictModelOutput`，从而拒绝未声明字段；固定枚举通过 Pydantic schema 校验，非法枚举、非法 JSON、schema 错误、供应商错误和超时均使用独立异常类型显式失败。
- 适配器在 schema 校验前后拦截所有 JSON 数值，并拒绝文本中的显式概率数字；该约束不可由调用方关闭，防止模型数字进入后续业务计算。
- 初次定向回归：`37 passed, 1 skipped`；初次全套 `tests/`：`347 passed, 1 skipped`。代码审查修正后的最终结果见后续记录。

### 2026-08-11 代码审查修正

- 规范审查指出 PA 联动桥应位于 `src/integration/`，已从 `src/reasoning/` 迁移并调整公开导出。
- 规范与工单审查均指出按字段名拦截概率存在绕过路径，已改为不可关闭的全局边界：拒绝任意 JSON 数值，并拒绝文本中与概率、胜率、置信度、赔率、先验或后验并列的数字；schema 校验后再次检查，防止类型转换产生数值。
- 修正后定向回归：`41 passed, 1 skipped`；全套 `tests/`：`353 passed, 1 skipped`。
- 复审补充覆盖概率字段中的字符串数字（如 `estimatedWinRate: "73"`、`prob: "0.73"`），最终适配器单测 `18 passed`；全套 `tests/`：`358 passed, 1 skipped`。
