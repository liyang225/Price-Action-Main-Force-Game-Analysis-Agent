# 价格行为博弈量化智能体

本仓库是一个 Windows 桌面量化分析工具集，包含两个联动应用：

- `apps/PA_Agent`：价格行为分析、图表与两阶段模型编排。
- `apps/SecondOrderGame`：基于情绪周期、参与者行为和 T+1 闸门的二阶博弈推演。

b站视频介绍：【耗费12亿token！开源券商付费功能 一键部署主力行为分析智能体】https://www.bilibili.com/video/BV1pM8g6kEx6?vd_source=364d0985edff8e1fe09d8f5635b32eae

![核心界面]（https://github.com/liyang225/Price-Action-Main-Force-Game-Analysis-Agent/blob/4162d19ecdbd936728ec0dbaa57d6acfaf4a82f6/.github/PHOTO/%E5%B1%8F%E5%B9%95%E6%88%AA%E5%9B%BE%202026-08-17%20180823.png）

二阶博弈系统当前的功能模块可以概括为：
一、数据层：富途行情、AkShare 龙虎榜、新闻预取、每日分析缓存、情绪台账、资金流台账、限流与数据源抽象。
二、板块/个股标注：板块情绪周期识别（含 v2 影子运行与切换）、个股主力行为事后标注。
三、HMM 信念引擎：根据板块情绪周期，持续更新对隐状态的信念，并推导主力/散户行为先验。
四、博弈信号：纳什均衡带、羊群行为、聪明钱、流动性陷阱、动量/均值回归等程序化观测信号，以及龙虎榜信号。
五、概率与风控：开盘区间分布、T+1 首次触及概率、T+1 交易闸门、概率免责声明与先验权重展示。
六、推演引擎：情绪周期判断、参与者识别、政策环境识别、提示词路由、信念更新、行为预测，以及三情景应对树生成。
七、校准追踪：记录预测与真实结果，后续计算 Brier Score 等校准质量指标。
八、PA 联动层：读取 PA 的市场/个股分析结果，在 PA 阶段二完成后触发二阶博弈分析，并输出独立的 T+1 闸门。
九、参数工作台 GUI：HMM 参数编辑、预览、版本历史和配置会话管理。
十、配置与启动校验：HMM 先验、信号、情绪、板块、标注、概率、T+1、龙虎榜、GUI 等配置均由校验器约束。

升级优化：
增加自选股和分析池
增加交互界面的快捷输入提示词功能
创新增加浏览器标签让程序支持同窗口多线程并行分析
历史记录列表（可自动计算下单机会盈亏）
增加副图表
优化ai大模型设置
全局UI界面重构-优化信息层级
改版分析进程状态管道减少视觉噪声和解放更丰富空间布局
缩短高频功能路径提高操作效率
增加用户导入经验和高权重信源
20万数据参数优化还有其他的维护
提升全局流畅度

## 环境

- Windows 10/11
- Python 3.11 或更高版本
- 可选：Futu OpenD、DSA 大盘分析数据库、大模型 API

## 安装

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -e ".\apps\PA_Agent[dev]"
python -m pip install -e ".\apps\SecondOrderGame[dev]"
Copy-Item .\apps\PA_Agent\config\settings.example.json .\apps\PA_Agent\config\settings.json
```

API 密钥只应写入本机 `apps/PA_Agent/config/settings.json` 或环境变量，该文件已被 Git 忽略。

## 配置

1. 先去富途牛牛官网下载安装富途open d.
2.github上（需使用网络代理工具）下载Daily stock analysis，二阶博弈设置中接入DSA的大盘分析记录存放路径
3.按下面方式连接本机 DSA。无需重新把私人绝对路径写回源码，建议只在本机环境变量或本机启动脚本中配置。
确认 DSA 本机安装目录
例如：
D:\Tools\DailyStockAnalysis
该目录应包含：
D:\Tools\DailyStockAnalysis\.env
D:\Tools\DailyStockAnalysis\data\stock_analysis.db
D:\Tools\DailyStockAnalysis\resources\backend\stock_analysis\stock_analysis.exe
实际目录不同，以本机 DSA 的文件位置为准。
配置三个路径
DSA_INSTALL_ROOT
填写 DSA 安装根目录，例如：
D:\Tools\DailyStockAnalysis
DSA_EXECUTABLE
填写 DSA 后端程序的完整路径，例如：
D:\Tools\DailyStockAnalysis\resources\backend\stock_analysis\stock_analysis.exe
SECOND_ORDER_DSA_DATABASE
可以填写 DSA 的 data 文件夹：
D:\Tools\DailyStockAnalysis\data
也可以直接填写数据库文件：
D:\Tools\DailyStockAnalysis\data\stock_analysis.db
Windows 永久配置方式
在 PowerShell 中执行：
[Environment]::SetEnvironmentVariable(
    "DSA_INSTALL_ROOT",
    "D:\Tools\DailyStockAnalysis",
    "User"
)

[Environment]::SetEnvironmentVariable(
    "DSA_EXECUTABLE",
    "D:\Tools\DailyStockAnalysis\resources\backend\stock_analysis\stock_analysis.exe",
    "User"
)

[Environment]::SetEnvironmentVariable(
    "SECOND_ORDER_DSA_DATABASE",
    "D:\Tools\DailyStockAnalysis\data",
    "User"
)
配置后关闭并重新打开程序。环境变量是在程序启动时读取的。
仅在本机启动脚本中硬编码
也可以在顶层 run.bat 的 setlocal 后添加：
set "DSA_INSTALL_ROOT=D:\Tools\DailyStockAnalysis"
set "DSA_EXECUTABLE=%DSA_INSTALL_ROOT%\resources\backend\stock_analysis\stock_analysis.exe"
set "SECOND_ORDER_DSA_DATABASE=%DSA_INSTALL_ROOT%\data"
这种方式只对本次启动有效。包含个人路径的 run.bat 不应提交到 GitHub。
程序内配置
PA Agent 设置界面的“DSA data 文件夹”可以填写：
D:\Tools\DailyStockAnalysis\data
或者：
D:\Tools\DailyStockAnalysis\data\stock_analysis.db
界面配置的数据库路径优先于 SECOND_ORDER_DSA_DATABASE。不过界面只能指定数据库；需要由 PA Agent 主动启动 DSA 大盘复盘时，仍应配置 DSA_INSTALL_ROOT 和 DSA_EXECUTABLE。
数据连接要求
数据库必须是 DSA 实际使用的 SQLite 数据库，并满足：
文件名通常为 stock_analysis.db
存在 analysis_history 表
DSA 已生成 code = 'MARKET'
对应记录的 report_type = 'market_review'
连接关系为：
PA Agent
  -> SecondOrderGame
  -> 读取 DSA 的 stock_analysis.db
  -> 缓存不存在或需要刷新时启动 stock_analysis.exe
  -> DSA 写回数据库
  -> SecondOrderGame 重新读取最新大盘复盘结果
若只读取 DSA 已有结果，只配置数据库路径即可；若要在 PA Agent 内点击刷新并自动调用 DSA，则三个路径都必须正确。


## 启动

安装完依赖后，可直接双击顶层 `run.bat`，或在 PowerShell 中执行：

```powershell
.\run.bat
```

PA Agent 默认从同一仓库的 `apps/SecondOrderGame` 加载二阶模块。如果使用其他位置，可设置 `SECOND_ORDER_GAME_ROOT`。DSA 数据库路径通过 GUI 设置或 `SECOND_ORDER_DSA_DATABASE` 配置。

## 验证

```powershell
python -m pytest -q .\apps\PA_Agent\tests
python -m pytest -q .\apps\SecondOrderGame\tests
python -m build .\apps\SecondOrderGame
```

## 项目状态

`v0.1.0-alpha` 阶段，用于代码审阅、离线测试和早期反馈。A 类行为概率可能仍包含专家先验，不等同于统计估计。本项目不连接券商自动下单，不构成投资建议。

## 许可证

本仓库按 `AGPL-3.0-or-later` 发布，第三方组件适用其各自的许可证。详见 `LICENSE` 和 `NOTICE`。
