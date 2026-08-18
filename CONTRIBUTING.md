# 参与贡献

## 开发环境

使用 Windows 10/11 和 Python 3.11+。两个应用的开发依赖需分别安装。

## 分支与提交

- 从 `main` 创建短期功能或修复分支。
- 一次提交聚焦一类行为变更。
- 改动 schema、配置、提示词或路由时，同步更新对应测试。

## 提交前

```powershell
python -m pytest -q .\apps\PA_Agent\tests
python -m pytest -q .\apps\SecondOrderGame\tests
```

不得提交本机配置、API 密钥、日志、数据库、交易记录、浏览器 profile 或未确认授权的第三方素材。
