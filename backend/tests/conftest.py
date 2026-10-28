"""测试目录分层与 pytest marker 的统一收集规则。"""

from pathlib import Path

import pytest


_TEST_ROOT = Path(__file__).resolve().parent
_STAGE_MARKERS = frozenset(
    {"unit", "contract", "integration", "system", "resilience", "phase6"}
)


def pytest_collection_modifyitems(
    config: pytest.Config,
    items: list[pytest.Item],
) -> None:
    """
    根据测试文件的第一层目录自动附加对应测试阶段 marker。

    Args:
        config: 当前 pytest 配置，本钩子无需读取。
        items: pytest 已收集的测试项。
    """
    del config
    for item in items:
        try:
            relative_path = Path(str(item.path)).resolve().relative_to(_TEST_ROOT)
        except ValueError:
            continue
        if relative_path.parts and relative_path.parts[0] in _STAGE_MARKERS:
            item.add_marker(getattr(pytest.mark, relative_path.parts[0]))
