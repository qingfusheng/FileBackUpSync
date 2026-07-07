from __future__ import annotations

import tomllib
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Config:
    source: Path
    target: Path
    recycle: Path
    ignore: tuple[str, ...] = ()
    detect_renames: bool = True
    small_file_size: int = 64 * 1024
    small_file_count: int = 1000
    verify: str = "hash"
    retry_max: int = 3
    retry_delay: float = 0.5
    reports: Path = Path(".backup-sync/reports")
    state: Path = Path(".backup-sync/state")
    compare: str = "smart"
    fingerprint_cache: Path = Path(".backup-sync/fingerprints.sqlite3")


def load_config(path: Path) -> Config:
    with path.open("rb") as stream:
        raw = tomllib.load(stream)
    base = path.resolve().parent
    paths = raw.get("paths", {})
    scan = raw.get("scan", {})
    sync = raw.get("sync", {})

    def resolve(value: str) -> Path:
        candidate = Path(value).expanduser()
        return candidate.resolve() if candidate.is_absolute() else (base / candidate).resolve()

    try:
        source = resolve(paths["source"])
        target = resolve(paths["target"])
    except KeyError as exc:
        raise ValueError("配置必须包含 [paths] source 和 target") from exc
    if source == target or source in target.parents or target in source.parents:
        raise ValueError("源目录和目标目录不能相同或互相包含")
    recycle_value = paths.get("recycle", str(target.parent / ".backup-sync-trash" / target.name))
    recycle = resolve(recycle_value)
    if recycle in (source, target) or source in recycle.parents or target in recycle.parents:
        raise ValueError("回收目录不能等于或位于源目录/目标目录内部")
    verify = str(sync.get("verify", "hash"))
    if verify not in ("size", "hash"):
        raise ValueError("sync.verify 只能是 size 或 hash")
    retry_max = int(sync.get("retry_max", 3))
    retry_delay = float(sync.get("retry_delay", 0.5))
    if retry_max < 0 or retry_delay < 0:
        raise ValueError("retry_max 和 retry_delay 不能为负数")
    compare = str(scan.get("compare", "smart"))
    if compare not in ("smart", "hash"):
        raise ValueError("scan.compare 只能是 smart 或 hash")
    runtime = raw.get("runtime", {})
    reports = resolve(runtime.get("reports", ".backup-sync/reports"))
    state = resolve(runtime.get("state", ".backup-sync/state"))
    fingerprint_cache = resolve(
        runtime.get("fingerprint_cache", ".backup-sync/fingerprints.sqlite3")
    )
    for runtime_path in (reports, state, fingerprint_cache):
        if source in runtime_path.parents or target in runtime_path.parents:
            raise ValueError("runtime 目录和指纹缓存不能位于源目录或目标目录内部")
    return Config(
        source=source,
        target=target,
        recycle=recycle,
        ignore=tuple(raw.get("ignore", {}).get("patterns", ())),
        detect_renames=bool(scan.get("detect_renames", True)),
        small_file_size=int(scan.get("small_file_size", 64 * 1024)),
        small_file_count=int(scan.get("small_file_count", 1000)),
        verify=verify,
        retry_max=retry_max,
        retry_delay=retry_delay,
        reports=reports,
        state=state,
        compare=compare,
        fingerprint_cache=fingerprint_cache,
    )
