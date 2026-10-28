"""把 API、Worker 和 Web 切回本机已有的历史镜像标签。"""

import argparse
import os
import subprocess
from pathlib import Path


PROJECT_DIR = Path(__file__).resolve().parents[1]
COMPOSE_FILE = PROJECT_DIR / "compose.yaml"


def ensure_images_exist(version: str) -> None:
    """
    确认目标版本的后端和前端镜像都存在于本机。

    Args:
        version: 需要回滚到的不可变发布标签。

    Raises:
        RuntimeError: 任一镜像不存在时抛出。
    """
    for image in (
        f"hili-trip-planner-backend:{version}",
        f"hili-trip-planner-web:{version}",
    ):
        result = subprocess.run(
            ["docker", "image", "inspect", image],
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        if result.returncode != 0:
            raise RuntimeError(f"本机不存在回滚镜像: {image}")


def rollback(version: str) -> None:
    """
    使用旧镜像重建应用容器，但保留现有 PostgreSQL 和 Redis。

    Args:
        version: 已验证存在的发布标签。

    Raises:
        subprocess.CalledProcessError: Compose 切换失败时抛出。
    """
    environment = os.environ.copy()
    environment["APP_VERSION"] = version
    subprocess.run(
        [
            "docker",
            "compose",
            "-f",
            str(COMPOSE_FILE),
            "up",
            "-d",
            "--no-build",
            "--no-deps",
            "api",
            "worker",
            "web",
        ],
        cwd=PROJECT_DIR,
        env=environment,
        check=True,
    )


def parse_args() -> argparse.Namespace:
    """
    解析需要回滚到的版本标签。

    Returns:
        包含目标版本的命令行参数。
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("version", help="例如 2026.09.17-1 或 Git SHA")
    return parser.parse_args()


def main() -> int:
    """
    校验镜像并执行应用层回滚。

    Returns:
        成功时返回 0。
    """
    version = parse_args().version.strip()
    ensure_images_exist(version)
    rollback(version)
    print(f"Application rolled back to: {version}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
