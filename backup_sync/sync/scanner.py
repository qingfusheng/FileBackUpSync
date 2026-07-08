from __future__ import annotations

import fnmatch
import os
from collections import Counter
from collections.abc import Callable, Iterable
from pathlib import Path

from .models import FileInfo, Snapshot

# 扫描进度回调：遍历到单个文件时触发，传入文件相对路径
ScanProgress = Callable[[Path], None]


def scan(
    root: Path,
    ignore: Iterable[str] = (),
    small_file_size: int = 64 * 1024,
    progress_callback: ScanProgress | None = None,
) -> Snapshot:
    """
    递归扫描指定根目录，生成完整目录快照
    收集所有文件元信息、目录列表，并统计小文件所属父目录计数
    自动跳过符号链接、匹配忽略规则的文件/目录
    """
    # 展开用户目录并转为绝对路径
    root = root.expanduser().resolve()

    # 校验根目录合法性
    if not root.is_dir():
        raise ValueError(f"目录不存在或不可读: {str(root)!r}")

    # 过滤忽略规则：去空、去除注释行（#开头）
    patterns = tuple(p.strip() for p in ignore if p.strip() and not p.lstrip().startswith("#"))

    files: dict[Path, FileInfo] = {}  # key: 文件相对路径, value: 文件元数据
    directories: set[Path] = set()  # 所有子目录相对路径集合
    small_file_parents: Counter[Path] = Counter()  # 统计每个目录下小文件数量

    # DFS遍历栈，使用深度优先遍历目录
    pending = [root]

    while pending:
        current_path = pending.pop()
        # 计算当前目录相对于扫描根目录的相对路径
        relative_dir = current_path.relative_to(root)

        # 根目录无需存入目录集合，只保存子目录
        if relative_dir != Path("."):
            directories.add(relative_dir)

        try:
            # 读取目录项并按名称排序，保证遍历顺序稳定
            entries = sorted(os.scandir(current_path), key=lambda entry: entry.name)
        except OSError as exc:
            raise OSError(f"无法读取目录: {current_path}") from exc

        child_directories: list[Path] = []

        for entry in entries:
            relative = relative_dir / entry.name

            # 匹配忽略规则，直接跳过当前项
            if _matches(relative, patterns):
                continue
            # 跳过所有符号链接，不跟随软链
            if entry.is_symlink():
                continue

            try:
                # 判断是否为真实目录，加入待遍历列表
                if entry.is_dir(follow_symlinks=False):
                    child_directories.append(Path(entry.path))
                    continue
                # 跳过非普通文件（设备、管道等）
                if not entry.is_file(follow_symlinks=False):
                    continue
                # 获取文件原生stat信息（不跟随软链接）
                stat = entry.stat(follow_symlinks=False)
            except OSError as exc:
                raise OSError(f"无法读取文件信息: {entry.path}") from exc

            # 存入文件元信息快照
            files[relative] = FileInfo(
                path=relative,
                size=stat.st_size,
                mtime_ns=stat.st_mtime_ns,
                ctime_ns=stat.st_ctime_ns,
                device=stat.st_dev,
                inode=stat.st_ino,
            )

            # 触发进度回调通知外部
            if progress_callback:
                progress_callback(relative)

            # 若文件小于阈值，父目录小文件计数+1
            if stat.st_size <= small_file_size:
                small_file_parents[relative.parent] += 1

        # 反转子目录列表，保证遍历顺序与自然排序一致
        pending.extend(reversed(child_directories))

    # 组装快照对象，目录集合转为不可变frozenset
    return Snapshot(
        root=root,
        files=files,
        directories=frozenset(directories),
        small_file_parents=small_file_parents,
    )


def empty_snapshot(root: Path) -> Snapshot:
    """创建空快照，无任何文件与目录，用于空目录基准对比"""
    return Snapshot(
        root.expanduser().resolve(),
        {},
        frozenset(),
        Counter(),
    )


def _matches(path: Path, patterns: Iterable[str]) -> bool:
    """
    内部忽略规则匹配函数
    匹配逻辑二选一即命中：
    1. 文件完整相对路径（POSIX格式）匹配通配符
    2. 仅文件名匹配通配符
    自动剔除规则末尾的斜杠，兼容目录匹配写法
    """
    value = path.as_posix()
    return any(
        fnmatch.fnmatchcase(value, pattern.rstrip("/"))
        or fnmatch.fnmatchcase(path.name, pattern.rstrip("/"))
        for pattern in patterns
    )
