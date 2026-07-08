"""
跨平台文件系统辅助操作。

主要处理：
- macOS immutable flag (uchg)
- Windows readonly attribute

同步工具在执行 rename/remove/replace 前，
需要确保目标路径可修改。
"""

from __future__ import annotations

import os
import stat
import sys
from pathlib import Path


def clear_file_protection(path: Path) -> None:
    """
    Clear platform-specific protection flags.

    macOS:
        uchg immutable flag

    Windows:
        readonly attribute

    Linux:
        currently no-op
    """
    if not path.exists() and not path.is_symlink():
        return
    current = path.stat(follow_symlinks=False)
    if sys.platform == "darwin" and hasattr(os, "chflags"):
        immutable = getattr(stat, "UF_IMMUTABLE", 0)
        if current.st_flags & immutable:
            os.chflags(path, current.st_flags & ~immutable, follow_symlinks=False)
    if sys.platform == "win32" or not current.st_mode & stat.S_IWUSR:
        path.chmod(current.st_mode | stat.S_IWUSR, follow_symlinks=False)


def clear_file_protection_recursive(path: Path) -> None:
    """
    Recursively clear protection flags for an entire
    directory tree.

    Children are processed before parents.
    """
    if not path.exists():
        return

    if path.is_dir():
        for child in path.iterdir():
            clear_file_protection_recursive(child)

    clear_file_protection(path)


def clear_protection_iter_up(
    path: Path,
    root: Path | None = None,
) -> None:
    """
    Clear protection flags from the given path upwards
    until the filesystem root or the specified root.
    """
    current = path

    while True:
        clear_file_protection(current)

        if root is not None and current == root:
            break

        parent = current.parent

        if parent == current:
            break

        current = parent
