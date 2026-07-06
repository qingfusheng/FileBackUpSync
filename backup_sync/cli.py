from __future__ import annotations

import argparse
import logging
import sys
import tomllib
from datetime import datetime
from pathlib import Path

from .config import load_config
from .core import ActionKind, build_plan, execute, format_size, scan


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description="源目录到目标目录的增量镜像备份")
    result.add_argument("--config", type=Path, default=Path("backup.toml"), help="TOML 配置文件")
    result.add_argument("--apply", action="store_true", help="执行计划；默认只预览")
    result.add_argument("--no-renames", action="store_true", help="禁用内容相同文件的 rename 检测")
    result.add_argument("--verbose", action="store_true")
    return result


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )
    try:
        config = load_config(args.config)
        config.target.mkdir(parents=True, exist_ok=True)
        source = scan(config.source, config.ignore, config.small_file_size)
        target = scan(config.target, (), config.small_file_size)
        plan = build_plan(source, target, config.detect_renames and not args.no_renames)
    except (OSError, ValueError, tomllib.TOMLDecodeError) as exc:
        print(f"错误: {exc}", file=sys.stderr)
        return 2

    hotspots = [(path, count) for path, count in source.small_file_parents.items() if count >= config.small_file_count]
    if hotspots:
        print("\n小文件热点（可考虑加入 ignore.patterns）：")
        for path, count in sorted(hotspots, key=lambda item: (-item[1], str(item[0])))[:20]:
            print(f"  {path.as_posix()}: {count} 个 <= {format_size(config.small_file_size)}")

    print(f"\n扫描: 源 {len(source.files)} 个文件，目标 {len(target.files)} 个文件")
    print(
        "计划: " + ", ".join(f"{kind.value}={plan.count(kind)}" for kind in ActionKind)
        + f", unchanged={plan.unchanged}"
    )
    for action in plan.actions:
        detail = f"{action.source} -> {action.path}" if action.source else str(action.path)
        print(f"  {action.kind.value:7} {detail}")

    if not args.apply:
        print("\n当前为预览模式；确认后使用 --apply 执行。")
        return 0
    if not plan.actions:
        print("目标目录已经与源目录一致。")
        return 0
    recycle = config.recycle / datetime.now().strftime("%Y-%m-%d_%H%M%S")
    execute(plan, source, target, recycle)
    print(f"同步完成；替换/删除的文件保存在: {recycle}")
    return 0
