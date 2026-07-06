from __future__ import annotations

import argparse
import logging
import sys
import tomllib
from datetime import datetime
from pathlib import Path

from .checkpoint import Checkpoint
from .config import load_config
from .core import ActionKind, ActionResult, VerifyMode, build_plan, execute, format_size, scan
from .progress import ProgressDisplay
from .reporting import build_report, new_run_id, write_json_atomic


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description="源目录到目标目录的增量镜像备份")
    result.add_argument("--config", type=Path, default=Path("backup.toml"), help="TOML 配置文件")
    result.add_argument("--apply", action="store_true", help="执行计划；默认只预览")
    result.add_argument("--no-renames", action="store_true", help="禁用内容相同文件的 rename 检测")
    result.add_argument("--resume", metavar="RUN_ID", help="恢复未完成的运行（仍需 --apply）")
    result.add_argument(
        "--progress",
        choices=("auto", "always", "never"),
        default="auto",
        help="进度条显示方式；默认自动识别终端和 PyCharm",
    )
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
    except (ValueError, tomllib.TOMLDecodeError, FileNotFoundError) as exc:
        print(f"配置错误: {exc}", file=sys.stderr)
        return 2
    if args.resume and not args.apply:
        print("配置错误: --resume 必须与 --apply 一起使用", file=sys.stderr)
        return 2
    checkpoint: Checkpoint | None = None
    if args.resume:
        try:
            checkpoint = Checkpoint.load(config.state, args.resume)
            checkpoint.validate_paths(config.source, config.target)
        except (OSError, ValueError) as exc:
            print(f"配置错误: {exc}", file=sys.stderr)
            return 2
    progress = ProgressDisplay(args.progress)
    try:
        config.target.mkdir(parents=True, exist_ok=True)
        with progress.scan("扫描源目录") as source_bar:

            def source_progress(_path: Path) -> None:
                source_bar.update(1)

            source = scan(
                config.source,
                config.ignore,
                config.small_file_size,
                progress_callback=source_progress,
            )
        with progress.scan("扫描目标目录") as target_bar:

            def target_progress(_path: Path) -> None:
                target_bar.update(1)

            target = scan(
                config.target,
                (),
                config.small_file_size,
                progress_callback=target_progress,
            )
        try:
            plan = build_plan(
                source,
                target,
                config.detect_renames and not args.no_renames,
                progress_callback=progress.plan,
            )
        finally:
            progress.close_plan()
    except (OSError, ValueError) as exc:
        print(f"I/O 错误: {exc}", file=sys.stderr)
        return 3

    hotspots = [
        (path, count)
        for path, count in source.small_file_parents.items()
        if count >= config.small_file_count
    ]
    if hotspots:
        print("\n小文件热点（可考虑加入 ignore.patterns）：")
        for path, count in sorted(hotspots, key=lambda item: (-item[1], str(item[0])))[:20]:
            print(f"  {path.as_posix()}: {count} 个 <= {format_size(config.small_file_size)}")

    print(f"\n扫描: 源 {len(source.files)} 个文件，目标 {len(target.files)} 个文件")
    print(
        "计划: "
        + ", ".join(f"{kind.value}={plan.count(kind)}" for kind in ActionKind)
        + f", unchanged={plan.unchanged}"
    )
    for action in plan.actions:
        detail = f"{action.source} -> {action.path}" if action.source else str(action.path)
        print(f"  {action.kind.value:7} {detail}")

    if not args.apply:
        print("\n当前为预览模式；确认后使用 --apply 执行。")
        return 0
    if not plan.actions and not args.resume:
        print("目标目录已经与源目录一致。")
        return 0
    run_id = args.resume or new_run_id()
    started_at = checkpoint.started_at if checkpoint else datetime.now().astimezone()
    recycle = checkpoint.recycle if checkpoint else config.recycle / run_id
    if checkpoint is None:
        try:
            checkpoint = Checkpoint.create(
                config.state,
                run_id,
                source.root,
                target.root,
                recycle,
                started_at,
            )
        except OSError as exc:
            print(f"无法创建 checkpoint: {exc}", file=sys.stderr)
            return 3
    verify = VerifyMode(config.verify)

    def record_progress(action_result: ActionResult) -> None:
        # Kept local so checkpoint persistence and UI progress advance together.
        checkpoint.record(action_result)
        progress.action_finished(action_result)

    progress.start_execution(len(plan.actions))
    try:
        try:
            result = execute(
                plan,
                source,
                target,
                recycle,
                verify=verify,
                retry_max=config.retry_max,
                retry_delay=config.retry_delay,
                progress_callback=record_progress,
                action_started_callback=progress.action_started,
            )
        finally:
            progress.close_execution()
    except OSError as exc:
        print(f"同步已部分执行，但 checkpoint 写入失败: {exc}", file=sys.stderr)
        return 3
    finished_at = datetime.now().astimezone()
    report_path = config.reports / f"{run_id}.json"
    report = build_report(
        run_id,
        started_at,
        finished_at,
        source,
        target,
        plan,
        result,
        verify,
        recycle,
    )
    try:
        write_json_atomic(report_path, report)
        checkpoint.finish("success" if result.failed == 0 else "partial_failure", report_path)
    except OSError as exc:
        print(f"同步已执行，但报告写入失败: {exc}", file=sys.stderr)
        return 3
    print(f"运行 {run_id} 完成：成功 {result.succeeded}，失败 {result.failed}；报告: {report_path}")
    if recycle.exists() and any(recycle.iterdir()):
        print(f"替换/删除的文件保存在: {recycle}")
    return 1 if result.failed else 0
