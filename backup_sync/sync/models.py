from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path


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
        return sum(a.kind == kind for a in self.actions)


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
