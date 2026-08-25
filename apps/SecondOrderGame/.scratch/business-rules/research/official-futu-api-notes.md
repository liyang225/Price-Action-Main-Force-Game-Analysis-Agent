# 富途 OpenAPI 历史 K 线约束核查

核查日期：2026-08-10
官方文档版本：Futu API v10.9

## 结论

富途官方接口同时支持 `K_DAY` 与 `K_120M`，历史数据保留上限分别属于“日 K 最近 20 年”和“分 K 最近 8 年”。但这是品类级保留上限，不代表每个股票、指数或板块代码都一定有同样深度的数据；研究工具必须逐标的记录实际首条时间。

A 股板块代码可以通过 `get_plate_list` 获得。官方文档没有在 `request_history_kline` 页面明确写出“板块代码可作为 code 参数”，但本机 OpenD v10.9 实测 `SH.LIST0002`（半导体）在 `K_DAY` 和 `K_120M` 均成功返回 OHLCV。因此项目可以使用富途板块行情做相对基准，但仍应保留能力探测与降级，不应把所有板块都假定为拥有完整 8/20 年历史。

## 登录与行情权限

- OpenD 登录已经取消开户门槛：可使用富途牛牛号，或注册手机号/邮箱登录；首次登录成功后仍须完成问卷评估和协议确认。
- 官方行情品类表列明 A 股市场支持股票、ETF、指数和板块。
- A 股行情权限的官方口径为：境内认证客户免费获得 LV1；国际客户暂不支持。境内/国际身份按 OpenD 登录 IP 地址区分。
- 因此，“无需开户即可登录 OpenD”不等于“任何账号都能读取 A 股行情”。研究底座应把登录状态、合规确认和 A 股行情权限分别探测并报告。

来源：

- [权限与额度：登录、行情权限](https://openapi.futunn.com/futu-api-doc/intro/authority.html)
- [介绍：支持的行情市场与品类](https://openapi.futunn.com/futu-api-doc/intro/intro.html)

## 历史 K 线额度

历史 K 线额度按最近 7 天内请求过的唯一标的计数：

| 用户条件 | 普通标的历史 K 线额度 |
|---|---:|
| 总资产小于 1 万 HKD，含未开户 | 100 |
| 总资产达到 1 万 HKD | 300 |
| 总资产达到 50 万 HKD，或月交易笔数 > 200，或月交易额 > 200 万 HKD | 1000 |
| 总资产达到 500 万 HKD，或月交易笔数 > 2000，或月交易额 > 2000 万 HKD | 2000 |

计数规则：

- 最近 7 天内首次请求一个标的，占用 1 个额度。
- 7 天内重复请求同一标的，不重复累计。
- 同一标的请求不同 K 线周期，也只占 1 个额度。
- 已占用额度在对应请求发生 7 天后释放。
- 可调用 `get_history_kl_quota(get_detail=True)` 查询已用、剩余额度及标的明细。

项目含义：日线和 `K_120M` 应在同一标的拉取任务中一起完成，不会增加唯一标的额度；真正受限的是股票加板块代码的去重数量，而不是页数或周期数。

来源：

- [订阅额度与历史 K 线额度](https://openapi.futunn.com/futu-api-doc/intro/authority.html#1314)
- [获取历史 K 线额度使用明细](https://openapi.futunn.com/futu-api-doc/quote/get-history-kl-quota.html)

## 周期、历史深度与返回字段

- `KLType.K_DAY`：日 K。
- `KLType.K_120M`：120 分钟 K，即 2 小时。
- 历史 K 线接口返回 `open`、`close`、`high`、`low`、`volume`、`turnover` 等字段。
- 官方保留范围：分 K 提供最近 8 年，日 K 提供最近 20 年，周 K、月 K 等日 K 以上周期不限制。
- `turnover_rate` 仅对日 K 及以上周期提供，不能把它作为 `K_120M` 规则的必需输入。
- `start` 或 `end` 缺省时，接口自动使用 365 天窗口；要研究更长历史，必须显式传入两端日期。

来源：

- [获取历史 K 线](https://openapi.futunn.com/futu-api-doc/quote/request-history-kline.html)
- [K 线类型 `KLType`](https://openapi.futunn.com/futu-api-doc/quote/quote.html#4119)

## 分页与接口限频

- `max_count` 默认是 1000，表示本次请求最多返回的 K 线根数。
- `max_count=None` 可要求返回 `start` 到 `end` 的全部数据，但官方提示超过 1000 根时应分页，避免 OpenD 收齐全部数据后一次下发造成超时。
- 当结果多于 `max_count` 时，首页传 `page_req_key=None`；后续页必须传上次返回的 `page_req_key`，直到返回 `None`。
- 历史 K 线接口限频为每 30 秒最多 60 次。
- 分页时，该限频只计算每个标的的首页；该标的的后续页请求不受这条 60 次/30 秒规则限制。
- 官方没有承诺“拉取一年数据”的固定耗时。工单 01 所需耗时必须在目标机器、目标权限和真实网络条件下实测，不能从文档推算。

来源：[获取历史 K 线：参数、分页与接口限制](https://openapi.futunn.com/futu-api-doc/quote/request-history-kline.html)

## 板块指数历史数据：文档与实测边界

官方可以确认的事实：

- A 股行情品类包含“指数”和“板块”。
- `get_plate_list(market, plate_class)` 返回 `code`、`plate_name`、`plate_id`；板块代码应来自该接口，而不是自行拼接。
- `request_history_kline` 的公开参数说明只写“code：股票代码”，没有明确列出股票、指数、板块各自的支持矩阵。

本机 OpenD v10.9 的只读实测：

- `get_plate_list(Market.SH, Plate.INDUSTRY)` 成功返回 131 个行业板块；示例代码为 `SH.LIST0002`，名称为“半导体”。
- 对 `SH.LIST0002` 请求 `K_DAY` 成功，返回板块 OHLCV、成交额等字段。
- 对同一代码请求 `K_120M` 成功，每个交易日返回 `11:30:00` 和 `15:00:00` 两根 K 线，符合项目两个决策点。
- 以 `start=2000-01-01`、`end=2026-08-10`、`max_count=1` 探测，该板块首条日 K 为 `2020-01-02 00:00:00`，首条 120 分钟 K 为 `2021-11-24 11:30:00`。
- `get_history_kl_quota(get_detail=True)` 的额度明细中出现 `SH.LIST0002` / “半导体”，确认板块代码会像股票代码一样占用一个唯一标的历史 K 线额度；同一板块的日线和 `K_120M` 没有生成两条额度记录。

上述首条日期只证明当前服务器上“半导体”这一板块代码的实际深度，不能外推到全部板块。研究底座应对每个板块、每个周期保存 `first_available_time`，并区分：

1. 官方品类上限：日 K 20 年、分 K 8 年。
2. 具体代码实际深度：可能因板块创建、口径变更或服务端数据覆盖而更短。
3. 本次请求区间：不要把缺省 365 天误判成服务端只有一年数据。

来源：

- [获取板块列表](https://openapi.futunn.com/futu-api-doc/quote/get-plate-list.html)
- [获取历史 K 线](https://openapi.futunn.com/futu-api-doc/quote/request-history-kline.html)
- [介绍：A 股指数与板块行情支持](https://openapi.futunn.com/futu-api-doc/intro/intro.html)

## 对工单 01 的直接约束

- 拉取器必须显式指定长区间，并循环 `page_req_key`，否则会把默认一年窗口或首页 1000 根误报成历史极限。
- 历史深度报告应至少包含 `requested_start`、`first_available_time`、`last_available_time`、`row_count`、`page_count`、`code`、`ktype` 和接口错误原文。
- 股票与板块代码都要分别实测日线和 `K_120M`，不能只引用 20 年/8 年的官方上限。
- 批量调度应按唯一标的预算额度；一个标的的日线与 `K_120M` 合并执行。
- 首页请求应限制在 60 次/30 秒以内。后续页虽不计入该官方限频，仍应串行或有限并发，避免本地 OpenD 超时。
- 板块历史能力应启动时探测；失败时明确返回“板块历史不可用/权限不足”，不能静默改用个股平均值，因为那会改变工单 02 的基准定义。

## 仍需实测

- 板块列表发生增删或代码变更时，历史序列是否连续。
