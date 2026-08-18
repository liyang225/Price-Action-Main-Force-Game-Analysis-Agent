# SecondOrderGame

SecondOrderGame 是基于博弈论、情绪周期和情景应对树的二阶量化分析模块，与 PA Agent 联动使用。

## 安装

```powershell
python -m pip install -e ".[dev]"
```

## 常用命令

```powershell
python -m src.gui.workbench
python -m pytest -q
python -m build
```

DSA 数据库路径由 PA Agent 设置项 `dsa_database_path` 或环境变量 `SECOND_ORDER_DSA_DATABASE` 提供。

本项目仅用于研究和分析辅助，不构成投资建议。
