# 02 — 建立统一市场数据接缝

**What to build:** 让业务模块通过同一个可注入边界读取 K 线、资金流、消息和龙虎榜；生产环境适配真实来源，测试环境使用离线 fake 和假时钟。

**Blocked by:** None — can start immediately.

**Status:** ready-for-agent

- [ ] 四类外部数据都能通过同一业务接口获取，调用方不依赖供应商 SDK。
- [ ] fake 可以注入成功、空数据与失败结果，且测试不连接 OpenD、Tavily 或 AkShare。
- [ ] 限频与重试使用可注入时间源，30 秒窗口边界可在离线测试中验证。

## Required context

- `AGENTS.md`、`CONTEXT.md`、`ARCHITECTURE.md`、`docs/ROADMAP.md`
- `docs/adr/0004-use-k120m-not-k240m.md`
- `docs/adr/0006-news-background-prefetch.md`
- `docs/adr/0020-test-seams-and-fixtures.md`
- 开工前可以在 `SecondOrderGame/` 中自行探索相邻的数据调用方、测试和配置；以 `rg` 找到的现状为准，但不要顺手扩展到本票之外的业务模块。

## Files in scope

- 创建：`src/data/__init__.py`、`src/data/protocol.py`、`src/data/models.py`、`src/data/futu_client.py`、`src/data/fake_client.py`、`src/data/rate_limiter.py`
- 创建：`tests/test_market_data_source.py`、`tests/test_rate_limiter.py`
- 修改：`tests/conftest.py`，把协议/fake/假时钟夹具改为复用生产定义，避免测试与生产各维护一套类型
- 参考：`config/sectors.yaml`、`pyproject.toml`
- 外部参考：`../富途API接口/Futu-API-Doc-zh-Python.md`、`../富途API接口/FTAPI4Python_10.9.6908/`

## Constraints and non-goals

- 只建立一个业务数据接缝，不为富途、Tavily、AkShare各建一套平行协议。
- 测试不得依赖真实 OpenD、网络、挂钟等待或供应商凭据。
- 本票不实现台账、缓存、采集调度、概率计算或龙虎榜业务规则。
- 不改变 K_120M 口径，不用异常时返回空列表来掩盖连接失败。

## Verification

在 `SecondOrderGame/` 根目录运行：

```powershell
C:\Users\bai\AppData\Local\Programs\Python\Python314\python.exe -m pytest tests/test_market_data_source.py tests/test_rate_limiter.py -v
C:\Users\bai\AppData\Local\Programs\Python\Python314\python.exe -m pytest tests/ -v
```

交付时列出真实适配器未在线验证的路径、fake 覆盖的失败分支及新增公开类型。
