"""
文件归档工具。

负责：
- 生成归档路径
- 删除文件时归档
- 覆盖文件前备份

归档目录会保持原始目录结构，
若目标文件已存在，则自动追加时间戳避免覆盖。
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from .fileops import safe_copy, safe_move


def archive_destination(relative: Path, recycle_root: Path) -> Path:
    """
    根据相对路径生成归档路径。

    保留原目录结构，
    若归档目标已存在，则自动追加时间戳。
    """
    destination = recycle_root / relative
    destination.parent.mkdir(parents=True, exist_ok=True)

    if destination.exists():
        timestamp_suffix = datetime.now().strftime(".%H%M%S%f")
        destination = destination.with_name(destination.name + timestamp_suffix)

    return destination


def archive_remove(path: Path, relative: Path, recycle_root: Path) -> None:
    """
    将文件或目录移动到归档目录。

    文件不存在时直接返回。
    """
    if not path.exists():
        return

    dest = archive_destination(relative, recycle_root)
    safe_move(path, dest)


def backup_existing(path: Path, relative: Path, recycle_root: Path) -> None:
    """
    覆盖文件前备份原文件。

    若目标文件不存在，则无需备份。
    """
    if not path.exists():
        return

    dest = archive_destination(relative, recycle_root)
    safe_copy(path, dest)
