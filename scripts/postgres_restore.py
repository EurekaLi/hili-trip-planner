"""校验并恢复由 postgres_backup.py 创建的 PostgreSQL 逻辑备份。"""

import argparse
import hashlib
import subprocess
from pathlib import Path


PROJECT_DIR = Path(__file__).resolve().parents[1]
COMPOSE_FILE = PROJECT_DIR / "compose.yaml"


def verify_checksum(backup_path: Path) -> None:
    """
    校验备份旁的 SHA-256 文件，阻止损坏数据进入数据库。

    Args:
        backup_path: 需要恢复的备份文件。

    Raises:
        RuntimeError: 校验文件缺失或摘要不匹配时抛出。
    """
    checksum_path = backup_path.with_suffix(backup_path.suffix + ".sha256")
    if not checksum_path.exists():
        raise RuntimeError(f"缺少校验文件: {checksum_path}")
    expected = checksum_path.read_text(encoding="ascii").split()[0]
    actual = hashlib.sha256(backup_path.read_bytes()).hexdigest()
    if actual != expected:
        raise RuntimeError("备份SHA-256校验失败，已停止恢复")


def restore_backup(backup_path: Path, confirmed_database: str) -> None:
    """
    清理目标数据库对象并从 custom-format 备份恢复。

    Args:
        backup_path: 已通过校验的备份文件。
        confirmed_database: 操作者显式确认的目标数据库名。

    Raises:
        RuntimeError: 确认值为空、恢复命令失败时抛出。
    """
    if not confirmed_database.strip():
        raise RuntimeError("必须通过 --confirm-database 显式确认目标数据库")
    command = [
        "docker",
        "compose",
        "-f",
        str(COMPOSE_FILE),
        "exec",
        "-T",
        "-e",
        f"CONFIRMED_DATABASE={confirmed_database}",
        "postgres",
        "sh",
        "-c",
        'test "$CONFIRMED_DATABASE" = "$POSTGRES_DB" && '
        'exec pg_restore --clean --if-exists --no-owner --no-privileges '
        '-U "$POSTGRES_USER" -d "$POSTGRES_DB"',
    ]
    result = subprocess.run(
        command,
        cwd=PROJECT_DIR,
        check=False,
        input=backup_path.read_bytes(),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if result.returncode != 0:
        error = result.stderr.decode("utf-8", errors="replace").strip()
        raise RuntimeError(f"PostgreSQL恢复失败: {error}")


def parse_args() -> argparse.Namespace:
    """
    解析备份路径和目标数据库二次确认。

    Returns:
        恢复命令参数。
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("backup", type=Path)
    parser.add_argument("--confirm-database", required=True)
    return parser.parse_args()


def main() -> int:
    """
    校验并恢复指定备份。

    Returns:
        成功时返回 0。
    """
    args = parse_args()
    backup_path = args.backup.resolve()
    verify_checksum(backup_path)
    restore_backup(backup_path, args.confirm_database)
    print(f"Backup restored: {backup_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
