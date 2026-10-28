"""Phase 6 跨栈测试的 marker 自动分类。"""

from pathlib import Path

import pytest


_TEST_ROOT = Path(__file__).resolve().parent
_MARKERS = frozenset(
    {"deployment", "e2e", "performance", "security", "live_contract"}
)


def pytest_collection_modifyitems(
    config: pytest.Config,
    items: list[pytest.Item],
) -> None:
    """
    根据 Phase 6 测试的第一层目录自动添加 marker。

    Args:
        config: 当前 pytest 配置，本钩子无需读取。
        items: pytest 已收集的测试项。
    """
    del config
    for item in items:
        try:
            relative = Path(str(item.path)).resolve().relative_to(_TEST_ROOT)
        except ValueError:
            continue
        if relative.parts and relative.parts[0] in _MARKERS:
            item.add_marker(getattr(pytest.mark, relative.parts[0]))
