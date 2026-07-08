from __future__ import annotations

import fnmatch
from collections.abc import Iterable
from pathlib import Path


def normalize_ignore_patterns(patterns: Iterable[str]) -> tuple[str, ...]:
    return tuple(
        pattern.strip()
        for pattern in patterns
        if pattern.strip() and not pattern.lstrip().startswith("#")
    )


def matches_ignore(path: Path, patterns: Iterable[str]) -> bool:
    value = path.as_posix()
    return any(
        fnmatch.fnmatchcase(value, pattern.rstrip("/"))
        or fnmatch.fnmatchcase(path.name, pattern.rstrip("/"))
        for pattern in patterns
    )
