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


def load_config(path: Path) -> Config:
    with path.open("rb") as stream:
        raw = tomllib.load(stream)
    base = path.resolve().parent
    paths = raw.get("paths", {})
    scan = raw.get("scan", {})

    def resolve(value: str) -> Path:
        candidate = Path(value).expanduser()
        return candidate if candidate.is_absolute() else (base / candidate).resolve()

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
    return Config(
        source=source,
        target=target,
        recycle=recycle,
        ignore=tuple(raw.get("ignore", {}).get("patterns", ())),
        detect_renames=bool(scan.get("detect_renames", True)),
        small_file_size=int(scan.get("small_file_size", 64 * 1024)),
        small_file_count=int(scan.get("small_file_count", 1000)),
    )
