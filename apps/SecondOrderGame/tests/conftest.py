"""
pytest 配置与夹具 — SecondOrderGame

此文件集中提供测试夹具，取代之前每个测试文件各自手写 sys.path.insert 的模式。
pyproject.toml 已声明 pythonpath = ["src"]，测试文件直接 import src.* 即可。
"""

from hashlib import sha256
import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import pytest
import pandas as pd

from src.data.fake_client import FakeMarketDataSource
from src.data.rate_limiter import FakeClock


@dataclass
class MutableDateTimeClock:
    current: datetime

    def __call__(self) -> datetime:
        return self.current

# ============================================================================
# 夹具定义
# ============================================================================

@pytest.fixture
def fake_data_source():
    """空的假数据源，测试可注入任意数据"""
    return FakeMarketDataSource()


@pytest.fixture
def fake_clock():
    """假时钟，初始为 t=0"""
    return FakeClock()


@pytest.fixture
def daily_clock() -> MutableDateTimeClock:
    return MutableDateTimeClock(datetime(2026, 8, 10, 11, 30))


@pytest.fixture
def temp_db(tmp_path):
    """
    临时 SQLite 数据库，用于测试台账持久化（P0-2, P0-8）。

    每个测试用例独立文件，测试结束后自动清理。
    """
    db_path = tmp_path / "test_ledger.db"
    conn = sqlite3.connect(str(db_path))
    yield conn
    conn.close()


@pytest.fixture
def golden_labeler_sample():
    """
    标注器 golden 样本加载器（ADR-0020 二）。

    从 tests/fixtures/ 加载约2,000行分层抽样的固化样本。
    manifest 记录种子、规则哈希、各标签期望命中数。

    样本保存计算后的特征与合成的资金流边界值。前者让随机抽取的股票日仍可
    独立回归六条冻结规则，后者专门验证 ADR-0018 的二方参与者归类边界。
    """
    fixtures_dir = Path(__file__).parent / "fixtures"
    manifest_path = fixtures_dir / "labeler_manifest.json"
    sample_path = fixtures_dir / "golden_sample.csv.gz"
    return {
        "data": pd.read_csv(sample_path, compression="gzip"),
        "manifest": json.loads(manifest_path.read_text(encoding="utf-8")),
        "fixture_sha256": sha256(sample_path.read_bytes()).hexdigest(),
    }


# ============================================================================
# 配置加载辅助
# ============================================================================

@pytest.fixture
def config_root():
    """配置文件根目录"""
    return Path(__file__).parent.parent / "config"


@pytest.fixture
def parameter_config_path(tmp_path, config_root):
    """隔离的 HMM 参数文件，供会话、历史和 Qt 接缝共同使用。"""
    target = tmp_path / "config" / "hmm_prior.yaml"
    target.parent.mkdir()
    target.write_bytes((config_root / "hmm_prior.yaml").read_bytes())
    return target
