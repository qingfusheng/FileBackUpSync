from __future__ import annotations

import logging
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Literal

from blake3 import blake3

if TYPE_CHECKING:
    from ..sync.models import FileInfo

LOGGER = logging.getLogger(__name__)
FingerprintKind = Literal["quick", "strong"]
ALGORITHM = "blake3-v1"
SAMPLE_SIZE = 64 * 1024
FULL_QUICK_LIMIT = SAMPLE_SIZE * 3


@dataclass(frozen=True)
class FingerprintStats:
    cache_hits: int = 0
    quick_computed: int = 0
    strong_computed: int = 0
    bytes_read: int = 0


class FingerprintEngine:
    """Layered BLAKE3 fingerprints with optional persistent SQLite caching."""

    def __init__(self, cache_path: Path | None = None) -> None:
        self._connection: sqlite3.Connection | None = None
        self._memory: dict[tuple[str, str, int, int, int, str], str] = {}
        self._cache_hits = 0
        self._quick_computed = 0
        self._strong_computed = 0
        self._bytes_read = 0
        if cache_path is not None:
            self._open(cache_path)

    def _open(self, path: Path) -> None:
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            self._connection = sqlite3.connect(path)
            self._connection.execute(
                """
                CREATE TABLE IF NOT EXISTS fingerprints (
                    root TEXT NOT NULL,
                    identity TEXT NOT NULL,
                    path TEXT NOT NULL,
                    size INTEGER NOT NULL,
                    mtime_ns INTEGER NOT NULL,
                    ctime_ns INTEGER NOT NULL DEFAULT 0,
                    algorithm TEXT NOT NULL,
                    quick TEXT,
                    strong TEXT,
                    PRIMARY KEY (root, identity, algorithm)
                )
                """
            )
            columns = {
                str(row[1]) for row in self._connection.execute("PRAGMA table_info(fingerprints)")
            }
            if "ctime_ns" not in columns:
                self._connection.execute(
                    "ALTER TABLE fingerprints ADD COLUMN ctime_ns INTEGER NOT NULL DEFAULT 0"
                )
        except (OSError, sqlite3.Error) as exc:
            LOGGER.warning("指纹缓存不可用，降级到内存模式: %s", exc)
            if self._connection is not None:
                self._connection.close()
            self._connection = None

    @property
    def stats(self) -> FingerprintStats:
        return FingerprintStats(
            self._cache_hits,
            self._quick_computed,
            self._strong_computed,
            self._bytes_read,
        )

    def quick(self, root: Path, relative: Path, info: FileInfo) -> str:
        return self._fingerprint("quick", root, relative, info)

    def strong(self, root: Path, relative: Path, info: FileInfo, *, use_cache: bool = True) -> str:
        return self._fingerprint("strong", root, relative, info, use_cache=use_cache)

    def _fingerprint(
        self,
        kind: FingerprintKind,
        root: Path,
        relative: Path,
        info: FileInfo,
        *,
        use_cache: bool = True,
    ) -> str:
        root_key = str(root)
        identity = _identity(relative, info)
        memory_key = (root_key, identity, info.size, info.mtime_ns, info.ctime_ns, kind)
        if use_cache and memory_key in self._memory:
            self._cache_hits += 1
            return self._memory[memory_key]
        cached = self._load(kind, root_key, identity, info) if use_cache else None
        if cached is not None:
            self._cache_hits += 1
            self._memory[memory_key] = cached
            return cached
        absolute = root / relative
        if kind == "quick":
            value, bytes_read = quick_digest(absolute, info.size)
            self._quick_computed += 1
        else:
            value, bytes_read = strong_digest(absolute)
            self._strong_computed += 1
        self._bytes_read += bytes_read
        self._memory[memory_key] = value
        self._store(kind, root_key, identity, relative, info, value)
        return value

    def _load(self, kind: FingerprintKind, root: str, identity: str, info: FileInfo) -> str | None:
        if self._connection is None:
            return None
        try:
            row = self._connection.execute(
                f"SELECT {kind}, size, mtime_ns, ctime_ns FROM fingerprints "
                "WHERE root = ? AND identity = ? AND algorithm = ?",
                (root, identity, ALGORITHM),
            ).fetchone()
        except sqlite3.Error as exc:
            LOGGER.warning("读取指纹缓存失败，继续直接计算: %s", exc)
            return None
        if row is None or row[0] is None:
            return None
        if int(row[1]) != info.size or int(row[2]) != info.mtime_ns or int(row[3]) != info.ctime_ns:
            return None
        return str(row[0])

    def _store(
        self,
        kind: FingerprintKind,
        root: str,
        identity: str,
        relative: Path,
        info: FileInfo,
        value: str,
    ) -> None:
        if self._connection is None:
            return
        quick = value if kind == "quick" else None
        strong = value if kind == "strong" else None
        try:
            self._connection.execute(
                """
                INSERT INTO fingerprints
                    (root, identity, path, size, mtime_ns, ctime_ns, algorithm, quick, strong)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(root, identity, algorithm) DO UPDATE SET
                    path = excluded.path,
                    size = excluded.size,
                    mtime_ns = excluded.mtime_ns,
                    ctime_ns = excluded.ctime_ns,
                    quick = CASE
                        WHEN fingerprints.size != excluded.size
                          OR fingerprints.mtime_ns != excluded.mtime_ns
                          OR fingerprints.ctime_ns != excluded.ctime_ns
                        THEN excluded.quick
                        ELSE COALESCE(excluded.quick, fingerprints.quick)
                    END,
                    strong = CASE
                        WHEN fingerprints.size != excluded.size
                          OR fingerprints.mtime_ns != excluded.mtime_ns
                          OR fingerprints.ctime_ns != excluded.ctime_ns
                        THEN excluded.strong
                        ELSE COALESCE(excluded.strong, fingerprints.strong)
                    END
                """,
                (
                    root,
                    identity,
                    relative.as_posix(),
                    info.size,
                    info.mtime_ns,
                    info.ctime_ns,
                    ALGORITHM,
                    quick,
                    strong,
                ),
            )
        except sqlite3.Error as exc:
            LOGGER.warning("写入指纹缓存失败，继续当前任务: %s", exc)

    def close(self) -> None:
        if self._connection is not None:
            try:
                self._connection.commit()
            except sqlite3.Error as exc:
                LOGGER.warning("提交指纹缓存失败: %s", exc)
            finally:
                self._connection.close()
                self._connection = None

    def __enter__(self) -> FingerprintEngine:
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()


def quick_digest(path: Path, size: int) -> tuple[str, int]:
    digest = blake3()
    digest.update(b"file-backup-sync:quick:v1\0")
    digest.update(size.to_bytes(8, "big", signed=False))
    positions = _sample_positions(size)
    bytes_read = 0
    with path.open("rb") as stream:
        for position in positions:
            stream.seek(position)
            chunk = stream.read(SAMPLE_SIZE)
            digest.update(position.to_bytes(8, "big", signed=False))
            digest.update(chunk)
            bytes_read += len(chunk)
    return digest.hexdigest(), bytes_read


def strong_digest(path: Path) -> tuple[str, int]:
    digest = blake3()
    bytes_read = 0
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
            bytes_read += len(chunk)
    return digest.hexdigest(), bytes_read


def _sample_positions(size: int) -> tuple[int, ...]:
    if size <= FULL_QUICK_LIMIT:
        return tuple(range(0, max(size, 1), SAMPLE_SIZE))
    last = max(0, size - SAMPLE_SIZE)
    middle = max(0, (size - SAMPLE_SIZE) // 2)
    return tuple(dict.fromkeys((0, middle, last)))


def _identity(relative: Path, info: FileInfo) -> str:
    if info.inode:
        return f"inode:{info.device}:{info.inode}"
    return f"path:{relative.as_posix()}"


def file_digest(path: Path) -> str:
    return strong_digest(path)[0]
