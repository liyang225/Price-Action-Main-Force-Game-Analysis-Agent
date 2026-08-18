# SecondOrderGame（二阶博弈系统）

基于博弈论与情绪周期的二阶推演分析模块，作为 PA_Agent 技术分析系统的姊妹模块，通过接口联动。
开发前，必须阅读这下面提到的文件夹和对应的文档。必读ARCHITECTURE.md

## Agent skills

### Issue tracker

Issues 与 PRD 以 markdown 文件形式存放在本仓库的 `.scratch/<feature-slug>/` 下（无远程仓库，本地跟踪）。See `docs/agents/issue-tracker.md`.

### Triage labels

使用五个标准 triage 角色的默认字符串（`needs-triage` / `needs-info` / `ready-for-agent` / `ready-for-human` / `wontfix`），记录在每个 issue 文件顶部的 `Status:` 行。See `docs/agents/triage-labels.md`.

### Domain docs

单上下文（single-context）：根目录一份 `CONTEXT.md`，架构决策记录在 `docs/adr/`。See `docs/agents/domain.md`.
