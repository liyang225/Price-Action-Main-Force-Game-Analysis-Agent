# 使用手册

## 生产提示词登记

生产环境只允许加载 `config/prompt_routing.yaml` 登记的文件。当前清单：

- `prompt_engine/通用/参与者识别.txt`
- `prompt_engine/通用/人设与思维方式.txt`
- `prompt_engine/通用/主体目的分析.txt`
- `prompt_engine/通用/情绪周期判断.txt`
- `prompt_engine/通用/新闻情绪评分.txt`
- `prompt_engine/通用/情景应对.txt`
- `prompt_engine/通用/用户经验.txt`
- `prompt_engine/主力/建仓.txt`
- `prompt_engine/主力/震仓.txt`
- `prompt_engine/主力/拉升.txt`
- `prompt_engine/主力/出货.txt`
- `prompt_engine/主力/观望.txt`
- `prompt_engine/主力/狩猎止损.txt`
- `prompt_engine/散户/FOMO追高.txt`
- `prompt_engine/散户/恐慌割肉.txt`
- `prompt_engine/散户/观望.txt`
- `prompt_engine/散户/理性跟随.txt`
- `prompt_engine/散户/底部建仓.txt`
- `prompt_engine/散户/高位减仓.txt`

修改提示词时须同时递增路由配置版本，并保持登记表、路由表与本清单一致。

## 历史分析记录

二阶博弈历史记录保存在项目根目录的 `analysis_history/second_order_history.db`。
嵌入 PA 的二阶博弈“设置”页提供“打开历史记录文件夹”按钮。旧版本位于
`runtime/second_order_history.db` 的记录会在首次读取时复制迁移，旧文件保留。

## PA 板块设置

PA 的每个自选品种必须保存非空的富途板块代码 `sector_code`。系统不限制市场、
前缀或代码格式，也不会改变大小写；代码会原样交给富途 OpenD 查询。富途无法
返回板块成分股时，材料准备会返回具体查询错误。`sector_name` 仅用于显示和新闻搜索。
二阶博弈不会再用板块名称或股票代码代替 `sector_code`；缺少该设置时，
材料准备会明确报错并停止写入情绪台账。

PA 使用 `PAMarketDataAdapter` 时，还须给它配置可读取板块 `K_DAY` 与交易日历的
`sector_market_source`。个股实时订阅只承担 K_120M 个股数据，不能作为板块行情来源。
