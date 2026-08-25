# 19 — 实现安全的提示词路由器

**What to build:** 按情绪周期位置和参与者类型纯函数查找已登记提示词，保证相同输入始终返回相同合法结果。

**Blocked by:** 04 — 启动时强制校验配置.

**Status:** ready-for-agent

- [ ] 路由只接受五档情绪周期位置和主力/散户两种参与者。
- [ ] 不存在、越界或表外路径在加载阶段被拒绝。
- [ ] 路由调用无文件写入、网络访问或其他副作用，大模型不能指定路径。

## Required context

- `AGENTS.md`、`CONTEXT.md`、`ARCHITECTURE.md`、`docs/ROADMAP.md`
- `docs/adr/0005-routing-pure-function-config-table.md`
- `docs/adr/0011-prompt-file-directory-structure.md`
- `docs/adr/0018-w-matrix-collapse-to-2participant.md`
- `docs/adr/0021-sector-cycle-and-consensus-model.md`
- `docs/adr/0020-test-seams-and-fixtures.md`
- 可自行探索项目中的配置加载模式；生产提示词尚未齐全时使用测试夹具验证路由，不用空文件假装完成。

## Files in scope

- 创建：`src/reasoning/prompt_router.py`、`tests/test_prompt_router.py`
- 创建：`tests/fixtures/prompt_routing_valid.yaml`、`tests/fixtures/prompts/` 下最小合法测试文件
- 修改：`src/config_validator.py`，增加路由 schema 与合法根目录校验函数，但暂不强制生产配置完整
- 参考：`src/labeler_constants.py`、`config/hmm_prior.yaml`

## Constraints and non-goals

- 路由输入只包含情绪周期位置和主力/散户参与者，映射结果来自配置表。
- 周期位置枚举固定为冰点、启动、发酵、高潮、退潮；分歧及转强/转弱是正交共识字段，不参与本路由维度。
- 路由是纯函数；不得写文件、联网、调用模型或接受模型给出的路径。
- 防止绝对路径、父目录穿越、符号链接越界和表外文件。
- 本票不编写业务提示词内容，不启用生产路由配置。

## Verification

在 `SecondOrderGame/` 根目录运行：

```powershell
C:\Users\bai\AppData\Local\Programs\Python\Python314\python.exe -m pytest tests/test_prompt_router.py -v
C:\Users\bai\AppData\Local\Programs\Python\Python314\python.exe -m pytest tests/ -v
```

测试必须包含相同输入相同输出、缺失文件、绝对路径、`..` 越界和表外枚举。
