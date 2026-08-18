# PA Agent

PA Agent 是面向主观交易者的价格行为 AI 分析桌面工具。它从 MT5、TradingView、AkShare 或其他已配置数据源读取 K 线，将结构化数据和预计算特征交给两阶段模型编排流程。本工具不连接券商执行下单。

## 主要功能

- 多数据源 K 线与结构化市场特征
- 市场诊断与交易决策两阶段分析
- 增量分析、历史记录和自由追问
- 决策树可视化与可配置校验
- 与 `apps/SecondOrderGame` 的二阶博弈分析联动
- API Key 本地存储与日志脱敏

## 环境要求

- Windows 10/11
- Python 3.11+
- 至少配置一种数据源
- 如需模型分析，需配置兼容的 AI API

## 安装与启动

```powershell
python -m pip install -e ".[dev]"
Copy-Item .\config\settings.example.json .\config\settings.json
python -m pa_agent.main
```

首次启动后在设置中填写 Base URL、模型名与 API Key。`config/settings.json` 是本机私有文件，不得提交到 Git。

完整操作说明见 [`PA_Agent使用文档.md`](PA_Agent使用文档.md)，配置字段说明见 [`config/README.md`](config/README.md)。

## 声明

本工具仅供学习、研究与分析辅助，不构成投资建议。交易有风险，决策后果由使用者自行承担。

本项目按 [AGPL-3.0-or-later](LICENSE) 发布。
