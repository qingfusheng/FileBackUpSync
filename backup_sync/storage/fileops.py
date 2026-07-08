"""
跨平台安全文件操作。

负责封装：
- 文件复制
- 文件移动
- 原子替换
- 删除文件
- 删除目录
- 原子复制

所有修改文件状态的操作都会自动清除保护属性，
避免 macOS immutable flag 或 Windows readonly 导致失败。
"""

from __future__ import annotations

import os
import shutil
import uuid
from collections.abc import Callable
from pathlib import Path

from .protection import clear_file_protection


def safe_copy(source: Path, destination: Path) -> None:
    """
    安全复制文件。

    自动创建目标目录。
    """
    destination.parent.mkdir(parents=True, exist_ok=True)
    clear_file_protection(destination)
    shutil.copy2(source, destination)


def safe_move(source: Path, destination: Path) -> None:
    """
    安全移动文件或目录。

    移动前自动清除源文件保护属性。
    """
    clear_file_protection(source)
    clear_file_protection(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(source), str(destination))


def safe_replace(source: Path, destination: Path) -> None:
    """
    原子替换目标文件。

    若目标文件存在，则先清除保护属性，
    再使用 os.replace() 完成原子替换。
    """
    clear_file_protection(source)
    clear_file_protection(destination)

    destination.parent.mkdir(parents=True, exist_ok=True)
    os.replace(source, destination)


def safe_unlink(path: Path) -> None:
    """
    安全删除文件。
    文件不存在时直接返回。
    """
    if not path.exists():
        return

    clear_file_protection(path)
    path.unlink(missing_ok=True)


def safe_rmdir(path: Path) -> None:
    """
    安全删除空目录。
    """
    if not path.exists():
        return

    clear_file_protection(path)
    path.rmdir()


def safe_mkdir(path: Path) -> None:
    """
    创建目录。
    若目录已存在，则忽略。
    """
    path.mkdir(parents=True, exist_ok=True)


def safe_atomic_copy(
    source: Path,
    destination: Path,
    verify: Callable[[Path, Path], None],
) -> Path:
    """
    原子复制文件。
    流程：
    1. 复制到临时文件；
    2. 校验复制结果；
    3. 检查源文件复制期间是否发生变化；
    4. 返回临时文件路径；
    5. 最终由 safe_replace() 提交。

    返回
    -------
    Path
        临时文件路径。
    """
    destination.parent.mkdir(parents=True, exist_ok=True)

    temporary = destination.parent / f".{destination.name}.backup-sync-{uuid.uuid4().hex}.tmp"

    try:
        before = source.stat()
        shutil.copy2(source, temporary)
        clear_file_protection(temporary)
        verify(source, temporary)
        after = source.stat()

        if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
            raise OSError(f"源文件在复制过程中发生变化：{source}")
        return temporary

    except BaseException:
        safe_unlink(temporary)
        raise


def commit_temp_file(temporary: Path, destination: Path) -> None:
    """
    提交临时文件。

    成功时原子替换目标文件；
    失败时自动清理临时文件。
    """
    try:
        safe_replace(temporary, destination)
    except BaseException:
        safe_unlink(temporary)
        raise
