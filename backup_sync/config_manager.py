from __future__ import annotations

import os
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import tomlkit
from tomlkit import TOMLDocument

from .config import Config, load_config

KNOWN_KEYS: dict[str, type[Any]] = {
    "paths.source": str,
    "paths.target": str,
    "paths.recycle": str,
    "scan.detect_renames": bool,
    "scan.compare": str,
    "scan.small_file_size": int,
    "scan.small_file_count": int,
    "sync.verify": str,
    "sync.retry_max": int,
    "sync.retry_delay": float,
    "runtime.reports": str,
    "runtime.state": str,
}


@dataclass(frozen=True)
class ConfigCheck:
    level: str
    name: str
    message: str


def read_document(path: Path) -> TOMLDocument:
    return tomlkit.parse(path.read_text(encoding="utf-8"))


def get_value(document: TOMLDocument, dotted_key: str) -> Any:
    value: Any = document
    try:
        for part in dotted_key.split("."):
            value = value[part]
    except (KeyError, TypeError) as exc:
        raise ValueError(f"配置项不存在: {dotted_key}") from exc
    return value.unwrap() if hasattr(value, "unwrap") else value


def flatten_document(document: TOMLDocument) -> dict[str, Any]:
    result: dict[str, Any] = {}

    def visit(prefix: str, value: Any) -> None:
        if hasattr(value, "items"):
            for key, child in value.items():
                visit(f"{prefix}.{key}" if prefix else str(key), child)
        else:
            result[prefix] = value.unwrap() if hasattr(value, "unwrap") else value

    visit("", document)
    return result


def parse_value(key: str, raw: str) -> Any:
    expected = KNOWN_KEYS.get(key)
    if expected is None:
        raise ValueError(f"不支持修改配置项: {key}")
    if expected is str:
        if key.startswith("paths.") and raw != raw.strip():
            raise ValueError("路径不能包含意外的前导或尾随空格")
        return raw
    if expected is bool:
        values = {"true": True, "false": False}
        try:
            return values[raw.lower()]
        except KeyError as exc:
            raise ValueError(f"{key} 必须是 true 或 false") from exc
    try:
        return expected(raw)
    except ValueError as exc:
        raise ValueError(f"{key} 的值类型无效: {raw}") from exc


def set_value(document: TOMLDocument, dotted_key: str, value: Any) -> None:
    parts = dotted_key.split(".")
    current: Any = document
    for part in parts[:-1]:
        if part not in current:
            current[part] = tomlkit.table()
        current = current[part]
    current[parts[-1]] = value


def validate_config(config: Config) -> list[ConfigCheck]:
    checks: list[ConfigCheck] = []
    if not config.source.is_dir():
        checks.append(ConfigCheck("error", "source", f"目录不存在: {config.source}"))
    elif not os.access(config.source, os.R_OK):
        checks.append(ConfigCheck("error", "source", f"目录不可读: {config.source}"))
    else:
        checks.append(ConfigCheck("ok", "source", f"可读: {config.source}"))

    if config.target.exists():
        if not config.target.is_dir():
            checks.append(ConfigCheck("error", "target", f"不是目录: {config.target}"))
        elif not os.access(config.target, os.W_OK):
            checks.append(ConfigCheck("error", "target", f"目录不可写: {config.target}"))
        else:
            checks.append(ConfigCheck("ok", "target", f"可写: {config.target}"))
    else:
        parent = _existing_parent(config.target)
        if parent is None or not os.access(parent, os.W_OK):
            checks.append(ConfigCheck("error", "target", f"无法创建目录: {config.target}"))
        else:
            checks.append(
                ConfigCheck("warning", "target", f"目录尚不存在，将创建: {config.target}")
            )
    checks.append(ConfigCheck("ok", "paths", "源、目标和回收目录关系安全"))
    return checks


def validate_file(path: Path) -> tuple[Config | None, list[ConfigCheck]]:
    try:
        config = load_config(path)
    except (OSError, ValueError) as exc:
        return None, [ConfigCheck("error", "syntax", str(exc))]
    return config, validate_config(config)


def update_file(path: Path, dotted_key: str, raw_value: str) -> list[ConfigCheck]:
    document = read_document(path)
    set_value(document, dotted_key, parse_value(dotted_key, raw_value))
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        temporary.write_text(tomlkit.dumps(document), encoding="utf-8")
        _, checks = validate_file(temporary)
        if any(check.level == "error" for check in checks):
            messages = "; ".join(check.message for check in checks if check.level == "error")
            raise ValueError(f"配置验证失败: {messages}")
        os.replace(temporary, path)
        return checks
    finally:
        temporary.unlink(missing_ok=True)


def _existing_parent(path: Path) -> Path | None:
    candidate = path.parent
    while not candidate.exists() and candidate != candidate.parent:
        candidate = candidate.parent
    return candidate if candidate.is_dir() else None
