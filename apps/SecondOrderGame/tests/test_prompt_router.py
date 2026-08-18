from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from config_validator import ConfigError, validate_prompt_routing
from reasoning.prompt_router import PromptRouter, load_prompt_router


FIXTURES = Path(__file__).parent / "fixtures"
VALID_CONFIG = FIXTURES / "prompt_routing_valid.yaml"
PROMPT_ROOT = FIXTURES / "prompts"


def _load_valid_config() -> dict:
    with VALID_CONFIG.open(encoding="utf-8") as source:
        return yaml.safe_load(source)


def test_router_returns_the_same_registered_file_for_the_same_input() -> None:
    router = load_prompt_router(VALID_CONFIG, PROMPT_ROOT)

    first = router.route("冰点", "主力")
    second = router.route("冰点", "主力")

    assert first == second
    assert first == (PROMPT_ROOT / "主力" / "冰点.txt").resolve()


def test_router_rejects_unknown_cycle_state_and_participant() -> None:
    router = load_prompt_router(VALID_CONFIG, PROMPT_ROOT)

    with pytest.raises(ValueError, match="情绪周期位置"):
        router.route("牛市", "主力")
    with pytest.raises(ValueError, match="参与者"):
        router.route("冰点", "做市商")


def test_router_rejects_missing_prompt_file(tmp_path: Path) -> None:
    config = _load_valid_config()
    config["routes"]["冰点"]["主力"] = "主力/不存在.txt"

    with pytest.raises(ConfigError, match="不存在或不是普通文件"):
        PromptRouter.from_config(config, PROMPT_ROOT)


@pytest.mark.parametrize("unsafe_path", ["C:/outside.txt", "/outside.txt"])
def test_router_rejects_absolute_prompt_path(unsafe_path: str) -> None:
    config = _load_valid_config()
    config["routes"]["冰点"]["主力"] = unsafe_path

    with pytest.raises(ConfigError, match="相对路径"):
        validate_prompt_routing(config, PROMPT_ROOT)


def test_router_rejects_parent_directory_escape() -> None:
    config = _load_valid_config()
    config["routes"]["冰点"]["主力"] = "../outside.txt"

    with pytest.raises(ConfigError, match="不允许 '..'"):
        validate_prompt_routing(config, PROMPT_ROOT)


def test_router_rejects_symlink_that_escapes_prompt_root(tmp_path: Path) -> None:
    import shutil

    # Operate on a throwaway copy: never touch the shared fixture tree, and
    # register the escaping link so the registry-completeness check cannot
    # mask the containment rejection it must exercise.
    root = tmp_path / "prompts"
    shutil.copytree(PROMPT_ROOT, root)
    outside = tmp_path / "outside.txt"
    outside.write_text("outside", encoding="utf-8")
    link = root / "主力" / "escaped.txt"
    try:
        link.symlink_to(outside)
    except OSError as error:
        # Windows 上符号链接创建失败可能残留 0 字节占位文件；这里用临时副本，
        # 不会污染共享 fixtures 目录。
        pytest.skip(f"symbolic links unavailable: {error}")
    if not link.is_symlink():
        # 缺少开发者模式时 symlink_to 可能静默产出 0 字节占位文件而非链接。
        pytest.skip("symbolic links unavailable: placeholder instead of link")

    config = _load_valid_config()
    config["registry"].append("主力/escaped.txt")
    config["routes"]["冰点"]["主力"] = "主力/escaped.txt"

    with pytest.raises(ConfigError, match="合法提示词根目录"):
        validate_prompt_routing(config, root)
