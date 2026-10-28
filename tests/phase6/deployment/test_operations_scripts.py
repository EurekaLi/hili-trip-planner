"""备份、恢复和回滚脚本的无 Docker 单元测试。"""

import hashlib
import subprocess

import pytest

from scripts import postgres_backup, postgres_restore, rollback_release


def test_backup_writes_dump_and_checksum(tmp_path, monkeypatch) -> None:
    """备份成功时应原样保存二进制 dump 并生成可验证摘要。"""
    payload = b"postgres-custom-format"
    monkeypatch.setattr(
        postgres_backup.subprocess,
        "run",
        lambda *args, **kwargs: subprocess.CompletedProcess(
            args[0],
            0,
            stdout=payload,
            stderr=b"",
        ),
    )
    target = tmp_path / "backup.dump"

    result = postgres_backup.create_backup(target)

    assert result.read_bytes() == payload
    checksum = target.with_suffix(".dump.sha256").read_text(encoding="ascii")
    assert checksum.startswith(hashlib.sha256(payload).hexdigest())


def test_restore_rejects_modified_backup(tmp_path) -> None:
    """备份被修改后必须在执行破坏性 pg_restore 前失败。"""
    target = tmp_path / "backup.dump"
    target.write_bytes(b"modified")
    target.with_suffix(".dump.sha256").write_text(
        f"{'0' * 64}  backup.dump\n",
        encoding="ascii",
    )

    with pytest.raises(RuntimeError, match="SHA-256"):
        postgres_restore.verify_checksum(target)


def test_restore_requires_explicit_database_confirmation(tmp_path) -> None:
    """恢复函数缺少目标数据库确认时不得启动 Docker 子进程。"""
    target = tmp_path / "backup.dump"
    target.write_bytes(b"valid")

    with pytest.raises(RuntimeError, match="confirm-database"):
        postgres_restore.restore_backup(target, "")


def test_rollback_requires_both_versioned_images(monkeypatch) -> None:
    """后端或前端任一历史镜像缺失时不得执行部分回滚。"""
    calls = iter([0, 1])

    def fake_run(*args, **kwargs):
        """依次模拟后端镜像存在、前端镜像缺失。"""
        return subprocess.CompletedProcess(args[0], next(calls))

    monkeypatch.setattr(rollback_release.subprocess, "run", fake_run)

    with pytest.raises(RuntimeError, match="web:v1"):
        rollback_release.ensure_images_exist("v1")
