from __future__ import annotations

import fnmatch
import hashlib
import logging
import os
import shutil
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Iterable

LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True)
class FileInfo:
    path: Path
    size: int


@dataclass(frozen=True)
class Snapshot:
    root: Path
    files: dict[Path, FileInfo]
    directories: frozenset[Path]
    small_file_parents: Counter[Path]


class ActionKind(str, Enum):
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
) -> Snapshot:
    root = root.expanduser().resolve()
    if not root.is_dir():
        raise ValueError(f"目录不存在或不可读: {root}")

    patterns = tuple(p.strip() for p in ignore if p.strip() and not p.lstrip().startswith("#"))
    files: dict[Path, FileInfo] = {}
    directories: set[Path] = set()
    small_file_parents: Counter[Path] = Counter()

    for current, dirnames, filenames in os.walk(root, topdown=True, followlinks=False):
        current_path = Path(current)
        relative_dir = current_path.relative_to(root)
        dirnames[:] = sorted(
            name for name in dirnames
            if not _matches(relative_dir / name, patterns)
            and not (current_path / name).is_symlink()
        )
        if relative_dir != Path("."):
            directories.add(relative_dir)
        for name in sorted(filenames):
            relative = relative_dir / name
            absolute = current_path / name
            if _matches(relative, patterns) or absolute.is_symlink():
                continue
            try:
                size = absolute.stat().st_size
            except OSError as exc:
                raise OSError(f"无法读取文件信息: {absolute}") from exc
            files[relative] = FileInfo(relative, size)
            if size <= small_file_size:
                small_file_parents[relative.parent] += 1
    return Snapshot(root, files, frozenset(directories), small_file_parents)


def file_digest(path: Path, algorithm: str = "sha256") -> str:
    digest = hashlib.new(algorithm)
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _same_file(source: Snapshot, target: Snapshot, relative: Path) -> bool:
    if source.files[relative].size != target.files[relative].size:
        return False
    return file_digest(source.root / relative) == file_digest(target.root / relative)


def build_plan(source: Snapshot, target: Snapshot, detect_renames: bool = True) -> Plan:
    source_paths = set(source.files)
    target_paths = set(target.files)
    common = source_paths & target_paths
    unchanged = 0
    actions: list[Action] = []

    for path in sorted(common):
        if _same_file(source, target, path):
            unchanged += 1
        else:
            actions.append(Action(ActionKind.UPDATE, path, size=source.files[path].size))

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

        for size in sorted(old_by_size.keys() & new_by_size.keys()):
            old_by_hash: dict[str, list[Path]] = defaultdict(list)
            new_by_hash: dict[str, list[Path]] = defaultdict(list)
            for path in sorted(old_by_size[size]):
                old_by_hash[file_digest(target.root / path)].append(path)
            for path in sorted(new_by_size[size]):
                new_by_hash[file_digest(source.root / path)].append(path)
            for digest in sorted(old_by_hash.keys() & new_by_hash.keys()):
                for old, new in zip(old_by_hash[digest], new_by_hash[digest]):
                    actions.append(Action(ActionKind.RENAME, new, source=old, size=size))
                    renamed_old.add(old)
                    renamed_new.add(new)

    for path in sorted(additions - renamed_new):
        actions.append(Action(ActionKind.COPY, path, size=source.files[path].size))
    for path in sorted(removals - renamed_old):
        actions.append(Action(ActionKind.REMOVE, path, size=target.files[path].size))

    for path in sorted(source.directories - target.directories, key=lambda p: (len(p.parts), str(p))):
        actions.append(Action(ActionKind.MKDIR, path))
    for path in sorted(target.directories - source.directories, key=lambda p: (-len(p.parts), str(p))):
        actions.append(Action(ActionKind.RMDIR, path))

    order = {
        ActionKind.REMOVE: 0, ActionKind.UPDATE: 1, ActionKind.RENAME: 2,
        ActionKind.MKDIR: 3, ActionKind.COPY: 4, ActionKind.RMDIR: 5,
    }
    def action_key(action: Action) -> tuple[int, int, str]:
        depth = -len(action.path.parts) if action.kind == ActionKind.RMDIR else len(action.path.parts)
        return order[action.kind], depth, str(action.path)

    return Plan(tuple(sorted(actions, key=action_key)), unchanged)


def _archive(path: Path, relative: Path, recycle_root: Path) -> None:
    if not path.exists():
        return
    destination = recycle_root / relative
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists():
        suffix = datetime.now().strftime(".%H%M%S%f")
        destination = destination.with_name(destination.name + suffix)
    shutil.move(str(path), str(destination))


def execute(plan: Plan, source: Snapshot, target: Snapshot, recycle_root: Path) -> None:
    recycle_root.mkdir(parents=True, exist_ok=True)
    # Clear already-empty obsolete directories first. A second pass below handles
    # directories that become empty after file removals or renames.
    for action in plan.actions:
        if action.kind == ActionKind.RMDIR:
            try:
                (target.root / action.path).rmdir()
            except (FileNotFoundError, OSError):
                pass
    for action in plan.actions:
        destination = target.root / action.path
        detail = f"{action.source} -> {action.path}" if action.source else str(action.path)
        LOGGER.info("%s %s", action.kind.value, detail)
        if action.kind in (ActionKind.REMOVE, ActionKind.UPDATE):
            _archive(destination, action.path, recycle_root)
        if action.kind == ActionKind.RENAME:
            old = target.root / action.source  # type: ignore[arg-type]
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(old), str(destination))
        elif action.kind in (ActionKind.COPY, ActionKind.UPDATE):
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source.root / action.path, destination)
        elif action.kind == ActionKind.MKDIR:
            destination.mkdir(parents=True, exist_ok=True)
        elif action.kind == ActionKind.RMDIR:
            try:
                destination.rmdir()
            except FileNotFoundError:
                pass
            except OSError:
                if destination.is_dir():
                    LOGGER.warning("目录非空，保留: %s", destination)


def format_size(value: int) -> str:
    size = float(value)
    for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
        if size < 1024 or unit == "TiB":
            return f"{size:.0f} {unit}" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024
    raise AssertionError("unreachable")
