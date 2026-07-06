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
    if recycle == source or source in recycle.parents or recycle == target or target in recycle.parents:
        raise ValueError("回收目录不能等于或位于源目录/目标目录内部")
    verify = str(sync.get("verify", "hash"))
    if verify not in ("size", "hash"):
        raise ValueError("sync.verify 只能是 size 或 hash")
    retry_max = int(sync.get("retry_max", 3))
    retry_delay = float(sync.get("retry_delay", 0.5))
    if retry_max < 0 or retry_delay < 0:
        raise ValueError("retry_max 和 retry_delay 不能为负数")
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
        reports=resolve(raw.get("runtime", {}).get("reports", ".backup-sync/reports")),
        state=resolve(raw.get("runtime", {}).get("state", ".backup-sync/state")),
    )
