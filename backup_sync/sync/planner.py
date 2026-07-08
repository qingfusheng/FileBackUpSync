from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable
from pathlib import Path

from ..storage.fingerprint import FULL_QUICK_LIMIT, FingerprintEngine
from .models import Action, ActionKind, Plan, Snapshot

# 进度回调：阶段名称、当前索引、总数、当前文件路径（可选）
PlanProgress = Callable[[str, int, int, Path | None], None]


def same_file(
    source: Snapshot,
    target: Snapshot,
    relative: Path,
    compare_mode: str,
    fingerprints: FingerprintEngine,
) -> bool:
    """判断源快照与目标快照中同相对路径文件是否内容完全一致"""
    source_info = source.files[relative]
    target_info = target.files[relative]

    # 文件大小不同直接判定为修改，无需校验哈希
    if source_info.size != target_info.size:
        return False

    # smart模式：大小一致+修改时间一致则跳过哈希比对，视为未变更
    if compare_mode == "smart" and source_info.mtime_ns == target_info.mtime_ns:
        return True

    # hash模式强制重新计算哈希，不读取缓存
    use_cache = compare_mode != "hash"

    # 计算两份文件强哈希并对比
    return fingerprints.strong(
        source.root, relative, source_info, use_cache=use_cache
    ) == fingerprints.strong(target.root, relative, target_info, use_cache=use_cache)


def build_plan(
    source: Snapshot,
    target: Snapshot,
    detect_renames: bool = True,
    progress_callback: PlanProgress | None = None,
    compare_mode: str = "smart",
    fingerprint_engine: FingerprintEngine | None = None,
) -> Plan:
    if compare_mode not in {"smart", "hash"}:
        raise ValueError(f"无效的文件比较模式: {compare_mode}")

    fingerprints = fingerprint_engine or FingerprintEngine()

    source_paths = set(source.files)
    target_paths = set(target.files)
    common = source_paths & target_paths

    actions: list[Action] = []
    unchanged = 0

    common_paths = sorted(common)
    for index, path in enumerate(common_paths):
        if progress_callback:
            progress_callback("比较同路径文件", index, len(common_paths), path)

        if same_file(source, target, path, compare_mode, fingerprints):
            unchanged += 1
        else:
            actions.append(Action(ActionKind.UPDATE, path, size=source.files[path].size))
    if progress_callback and common_paths:
        progress_callback("比较同路径文件", len(common_paths), len(common_paths), None)

    additions = source_paths - target_paths
    removals = target_paths - source_paths

    renamed_new: set[Path] = set()
    renamed_old: set[Path] = set()

    if detect_renames:
        old_by_size: dict[int, list[Path]] = defaultdict(list)
        new_by_size: dict[int, list[Path]] = defaultdict(list)

        for path in removals:
            old_by_size[target.files[path].size].append(path)
        for path in additions:
            new_by_size[source.files[path].size].append(path)

        matching_sizes = sorted(old_by_size.keys() & new_by_size.keys())
        hash_total = sum(len(old_by_size[size]) + len(new_by_size[size]) for size in matching_sizes)
        hash_completed = 0
        old_by_quick: dict[tuple[int, str], list[Path]] = defaultdict(list)
        new_by_quick: dict[tuple[int, str], list[Path]] = defaultdict(list)

        for size in matching_sizes:
            for path in sorted(old_by_size[size]):
                if progress_callback:
                    progress_callback("计算 rename 指纹", hash_completed, hash_total, path)
                quick = fingerprints.quick(target.root, path, target.files[path])
                old_by_quick[(size, quick)].append(path)
                hash_completed += 1
            for path in sorted(new_by_size[size]):
                if progress_callback:
                    progress_callback("计算 rename 指纹", hash_completed, hash_total, path)
                quick = fingerprints.quick(source.root, path, source.files[path])
                new_by_quick[(size, quick)].append(path)
                hash_completed += 1
        if progress_callback and hash_total:
            progress_callback("计算 rename 指纹", hash_total, hash_total, None)

        matching_quick = sorted(old_by_quick.keys() & new_by_quick.keys())
        strong_total = sum(
            len(old_by_quick[key]) + len(new_by_quick[key])
            for key in matching_quick
            if key[0] > FULL_QUICK_LIMIT
        )
        strong_completed = 0
        for size, quick in matching_quick:
            if size <= FULL_QUICK_LIMIT:
                for old, new in zip(
                    old_by_quick[(size, quick)],
                    new_by_quick[(size, quick)],
                    strict=False,
                ):
                    actions.append(Action(ActionKind.RENAME, new, source=old, size=size))
                    renamed_old.add(old)
                    renamed_new.add(new)
                continue

            old_by_strong: dict[str, list[Path]] = defaultdict(list)
            new_by_strong: dict[str, list[Path]] = defaultdict(list)
            for path in old_by_quick[(size, quick)]:
                if progress_callback:
                    progress_callback("确认 rename 内容", strong_completed, strong_total, path)
                strong = fingerprints.strong(target.root, path, target.files[path])
                old_by_strong[strong].append(path)
                strong_completed += 1
            for path in new_by_quick[(size, quick)]:
                if progress_callback:
                    progress_callback("确认 rename 内容", strong_completed, strong_total, path)
                strong = fingerprints.strong(source.root, path, source.files[path])
                new_by_strong[strong].append(path)
                strong_completed += 1
            for digest in sorted(old_by_strong.keys() & new_by_strong.keys()):
                for old, new in zip(old_by_strong[digest], new_by_strong[digest], strict=False):
                    actions.append(Action(ActionKind.RENAME, new, source=old, size=size))
                    renamed_old.add(old)
                    renamed_new.add(new)
        if progress_callback and strong_total:
            progress_callback("确认 rename 内容", strong_total, strong_total, None)

    for path in sorted(additions - renamed_new):
        actions.append(Action(ActionKind.COPY, path, size=source.files[path].size))
    for path in sorted(removals - renamed_old):
        actions.append(Action(ActionKind.REMOVE, path, size=target.files[path].size))

    new_directories = sorted(
        source.directories - target.directories,
        key=lambda p: (len(p.parts), str(p)),
    )
    for path in new_directories:
        actions.append(Action(ActionKind.MKDIR, path))
    old_directories = sorted(
        target.directories - source.directories,
        key=lambda p: (-len(p.parts), str(p)),
    )
    for path in old_directories:
        actions.append(Action(ActionKind.RMDIR, path))

    order = {
        ActionKind.REMOVE: 0,
        ActionKind.UPDATE: 1,
        ActionKind.RENAME: 2,
        ActionKind.MKDIR: 3,
        ActionKind.COPY: 4,
        ActionKind.RMDIR: 5,
    }

    def action_key(action: Action) -> tuple[int, int, str]:
        if action.kind == ActionKind.RMDIR:
            depth = -len(action.path.parts)
        else:
            depth = len(action.path.parts)
        return order[action.kind], depth, str(action.path)

    return Plan(tuple(sorted(actions, key=action_key)), unchanged)
