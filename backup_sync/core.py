from __future__ import annotations

import fnmatch
import logging
import os
import shutil
import time
import uuid
from collections import Counter, defaultdict
from collections.abc import Callable, Iterable
from contextlib import suppress
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from pathlib import Path

from .fingerprint import FULL_QUICK_LIMIT, FingerprintEngine, strong_digest

LOGGER = logging.getLogger(__name__)

ScanProgress = Callable[[Path], None]
PlanProgress = Callable[[str, int, int, Path | None], None]


@dataclass(frozen=True)
class FileInfo:
    path: Path
    size: int
    mtime_ns: int
    ctime_ns: int = 0
    device: int = 0
    inode: int = 0


@dataclass(frozen=True)
class Snapshot:
    root: Path
    files: dict[Path, FileInfo]
    directories: frozenset[Path]
    small_file_parents: Counter[Path]


class ActionKind(StrEnum):
    COPY = "copy"
    UPDATE = "update"
    RENAME = "rename"
    REMOVE = "remove"
    MKDIR = "mkdir"
    RMDIR = "rmdir"


@dataclass(frozen=True)
class Action:
    kind: ActionKind
    path: Path
    source: Path | None = None
    size: int = 0


@dataclass(frozen=True)
class Plan:
    actions: tuple[Action, ...]
    unchanged: int

    def count(self, kind: ActionKind) -> int:
        return sum(action.kind == kind for action in self.actions)


class VerifyMode(StrEnum):
    SIZE = "size"
    HASH = "hash"


@dataclass(frozen=True)
class ActionResult:
    action: Action
    success: bool
    attempts: int
    error: str | None = None


@dataclass(frozen=True)
class ExecutionResult:
    results: tuple[ActionResult, ...]

    @property
    def succeeded(self) -> int:
        return sum(result.success for result in self.results)

    @property
    def failed(self) -> int:
        return len(self.results) - self.succeeded


def _matches(path: Path, patterns: Iterable[str]) -> bool:
    value = path.as_posix()
    return any(
        fnmatch.fnmatchcase(value, pattern.rstrip("/"))
        or fnmatch.fnmatchcase(path.name, pattern.rstrip("/"))
        for pattern in patterns
    )


def scan(
    root: Path,
    ignore: Iterable[str] = (),
    small_file_size: int = 64 * 1024,
    progress_callback: ScanProgress | None = None,
) -> Snapshot:
    root = root.expanduser().resolve()
    if not root.is_dir():
        raise ValueError(f"目录不存在或不可读: {str(root)!r}")

    patterns = tuple(p.strip() for p in ignore if p.strip() and not p.lstrip().startswith("#"))
    files: dict[Path, FileInfo] = {}
    directories: set[Path] = set()
    small_file_parents: Counter[Path] = Counter()

    pending = [root]
    while pending:
        current_path = pending.pop()
        relative_dir = current_path.relative_to(root)
        if relative_dir != Path("."):
            directories.add(relative_dir)
        try:
            entries = sorted(os.scandir(current_path), key=lambda entry: entry.name)
        except OSError as exc:
            raise OSError(f"无法读取目录: {current_path}") from exc
        child_directories: list[Path] = []
        for entry in entries:
            relative = relative_dir / entry.name
            if _matches(relative, patterns) or entry.is_symlink():
                continue
            try:
                if entry.is_dir(follow_symlinks=False):
                    child_directories.append(Path(entry.path))
                    continue
                if not entry.is_file(follow_symlinks=False):
                    continue
                stat = entry.stat(follow_symlinks=False)
            except OSError as exc:
                raise OSError(f"无法读取文件信息: {entry.path}") from exc
            files[relative] = FileInfo(
                relative,
                stat.st_size,
                stat.st_mtime_ns,
                stat.st_ctime_ns,
                stat.st_dev,
                stat.st_ino,
            )
            if progress_callback:
                progress_callback(relative)
            if stat.st_size <= small_file_size:
                small_file_parents[relative.parent] += 1
        pending.extend(reversed(child_directories))
    return Snapshot(root, files, frozenset(directories), small_file_parents)


def empty_snapshot(root: Path) -> Snapshot:
    return Snapshot(root.expanduser().resolve(), {}, frozenset(), Counter())


def file_digest(path: Path) -> str:
    return strong_digest(path)[0]


def _same_file(
    source: Snapshot,
    target: Snapshot,
    relative: Path,
    compare_mode: str,
    fingerprints: FingerprintEngine,
) -> bool:
    source_info = source.files[relative]
    target_info = target.files[relative]
    if source_info.size != target_info.size:
        return False
    if compare_mode == "smart" and source_info.mtime_ns == target_info.mtime_ns:
        return True
    use_cache = compare_mode != "hash"
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
    source_paths = set(source.files)
    target_paths = set(target.files)
    common = source_paths & target_paths
    unchanged = 0
    actions: list[Action] = []
    fingerprints = fingerprint_engine or FingerprintEngine()

    common_paths = sorted(common)
    for index, path in enumerate(common_paths):
        if progress_callback:
            progress_callback("比较同路径文件", index, len(common_paths), path)
        if _same_file(source, target, path, compare_mode, fingerprints):
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


def _archive_destination(relative: Path, recycle_root: Path) -> Path:
    destination = recycle_root / relative
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists():
        suffix = datetime.now().strftime(".%H%M%S%f")
        destination = destination.with_name(destination.name + suffix)
    return destination


def _archive(path: Path, relative: Path, recycle_root: Path) -> None:
    if not path.exists():
        return
    destination = _archive_destination(relative, recycle_root)
    shutil.move(str(path), str(destination))


def _backup_existing(path: Path, relative: Path, recycle_root: Path) -> None:
    if path.exists():
        shutil.copy2(path, _archive_destination(relative, recycle_root))


def _verify_copy(source: Path, destination: Path, mode: VerifyMode) -> None:
    if source.stat().st_size != destination.stat().st_size:
        raise OSError(f"复制校验失败（大小不一致）: {destination}")
    if mode == VerifyMode.HASH and file_digest(source) != file_digest(destination):
        raise OSError(f"复制校验失败（SHA-256 不一致）: {destination}")


def _atomic_copy(source: Path, destination: Path, mode: VerifyMode) -> Path:
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.parent / f".{destination.name}.backup-sync-{uuid.uuid4().hex}.tmp"
    try:
        before = source.stat()
        shutil.copy2(source, temporary)
        _verify_copy(source, temporary, mode)
        after = source.stat()
        if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
            raise OSError(f"源文件在复制过程中发生变化: {source}")
        return temporary
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise


def _execute_action(
    action: Action,
    source: Snapshot,
    target: Snapshot,
    recycle_root: Path,
    verify: VerifyMode,
) -> None:
    destination = target.root / action.path
    if action.kind == ActionKind.REMOVE:
        _archive(destination, action.path, recycle_root)
    elif action.kind == ActionKind.UPDATE:
        temporary = _atomic_copy(source.root / action.path, destination, verify)
        try:
            _backup_existing(destination, action.path, recycle_root)
            os.replace(temporary, destination)
        except BaseException:
            temporary.unlink(missing_ok=True)
            raise
    elif action.kind == ActionKind.RENAME:
        if action.source is None:
            raise ValueError("rename 动作缺少原路径")
        old = target.root / action.source
        if not old.is_file():
            raise FileNotFoundError(f"rename 源文件不存在: {old}")
        if file_digest(old) != file_digest(source.root / action.path):
            raise OSError(f"源文件在计划生成后发生变化，拒绝 rename: {action.path}")
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(old), str(destination))
    elif action.kind == ActionKind.COPY:
        temporary = _atomic_copy(source.root / action.path, destination, verify)
        try:
            os.replace(temporary, destination)
        except BaseException:
            temporary.unlink(missing_ok=True)
            raise
    elif action.kind == ActionKind.MKDIR:
        destination.mkdir(parents=True, exist_ok=True)
    elif action.kind == ActionKind.RMDIR:
        try:
            destination.rmdir()
        except FileNotFoundError:
            pass
        except OSError as exc:
            if destination.is_dir():
                raise OSError(f"目录非空，无法清理: {destination}") from exc


def execute(
    plan: Plan,
    source: Snapshot,
    target: Snapshot,
    recycle_root: Path,
    verify: VerifyMode = VerifyMode.HASH,
    retry_max: int = 3,
    retry_delay: float = 0.5,
    progress_callback: Callable[[ActionResult], None] | None = None,
    action_started_callback: Callable[[Action], None] | None = None,
) -> ExecutionResult:
    recycle_root.mkdir(parents=True, exist_ok=True)
    # Clear already-empty obsolete directories first. A second pass below handles
    # directories that become empty after file removals or renames.
    for action in plan.actions:
        if action.kind == ActionKind.RMDIR:
            with suppress(FileNotFoundError, OSError):
                (target.root / action.path).rmdir()
    results: list[ActionResult] = []
    for action in plan.actions:
        detail = f"{action.source} -> {action.path}" if action.source else str(action.path)
        LOGGER.debug("%s %s", action.kind.value, detail)
        if action_started_callback:
            action_started_callback(action)
        action_result: ActionResult
        for attempt in range(1, retry_max + 2):
            try:
                _execute_action(action, source, target, recycle_root, verify)
                action_result = ActionResult(action, True, attempt)
                results.append(action_result)
                break
            except OSError as exc:
                if attempt > retry_max:
                    LOGGER.error("动作失败 %s: %s", detail, exc)
                    action_result = ActionResult(action, False, attempt, str(exc))
                    results.append(action_result)
                    break
                delay = retry_delay * (2 ** (attempt - 1))
                LOGGER.warning(
                    "动作失败，%.1fs 后重试（%d/%d）%s: %s",
                    delay,
                    attempt,
                    retry_max,
                    detail,
                    exc,
                )
                time.sleep(delay)
        if progress_callback:
            progress_callback(action_result)
    return ExecutionResult(tuple(results))


def format_size(value: int) -> str:
    size = float(value)
    for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
        if size < 1024 or unit == "TiB":
            return f"{size:.0f} {unit}" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024
    raise AssertionError("unreachable")
