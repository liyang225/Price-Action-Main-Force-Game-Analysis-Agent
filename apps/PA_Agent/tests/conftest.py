import shutil
import tempfile
from pathlib import Path

import pytest


@pytest.fixture
def tmp_path(request) -> Path:
    """替代 pytest 默认 tmp_path，使用项目内目录，手动管理清理。"""
    # 把临时目录放在项目根下，避开系统 %TEMP%
    project_root = Path(request.config.rootdir)
    base = project_root / ".pytest_tmp"
    base.mkdir(parents=True, exist_ok=True)

    tmp = Path(tempfile.mkdtemp(dir=str(base)))
    yield tmp

    # 手动清理，ignore_errors=True 绕过权限问题
    shutil.rmtree(str(tmp), ignore_errors=True)


@pytest.fixture
def tmp_path_factory(request):
    """如果某些测试用了 tmp_path_factory，同样覆盖。"""
    project_root = Path(request.config.rootdir)
    base = project_root / ".pytest_tmp"
    base.mkdir(parents=True, exist_ok=True)
    tmp = Path(tempfile.mkdtemp(dir=str(base)))
    yield tmp
    shutil.rmtree(str(tmp), ignore_errors=True)
