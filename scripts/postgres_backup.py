"""通过 Compose 中的 PostgreSQL 容器创建带校验和的逻辑备份。"""

import argparse
import hashlib
import subprocess
from datetime import datetime, timezone
from pathlib import Path


PROJECT_DIR = Path(__file__).resolve().parents[1]
COMPOSE_FILE = PROJECT_DIR / "compose.yaml"


def create_backup(output_path: Path) -> Path:
    """
    调用容器内 pg_dump，并在本机写入 custom-format 备份和校验和。

    Args:
        output_path: 本机备份文件路径。

    Returns:
        已写入的备份文件绝对路径。

    Raises:
        RuntimeError: pg_dump 执行失败或没有输出时抛出。
    """
    command = [
        "docker",
        "compose",
        "-f",
        str(COMPOSE_FILE),
        "exec",
        "-T",
        "postgres",
        "sh",
        "-c",
        'exec pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" '
        "--format=custom --no-owner --no-privileges",
    ]
    result = subprocess.run(
        command,
        cwd=PROJECT_DIR,
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if result.returncode != 0 or not result.stdout:
        error = result.stderr.decode("utf-8", errors="replace").strip()
        raise RuntimeError(f"PostgreSQL备份失败: {error or '没有输出'}")

    output_path = output_path.resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_bytes(result.stdout)
    digest = hashlib.sha256(result.stdout).hexdigest()
    output_path.with_suffix(output_path.suffix + ".sha256").write_text(
        f"{digest}  {output_path.name}\n",
        encoding="ascii",
    )
    return output_path


def parse_args() -> argparse.Namespace:
    """
    解析可选备份输出路径。

    Returns:
        包含输出路径的命令行参数。
    """
    default_name = datetime.now(timezone.utc).strftime("trip-planner-%Y%m%dT%H%M%SZ.dump")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output",
        type=Path,
        default=PROJECT_DIR / "backups" / default_name,
    )
    return parser.parse_args()


def main() -> int:
    """
    创建备份并输出文件位置。

    Returns:
        成功时返回 0。
    """
    backup_path = create_backup(parse_args().output)
    print(f"Backup created: {backup_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
