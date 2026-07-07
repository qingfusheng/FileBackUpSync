from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import tomllib
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from .analyzers import ANALYZERS, AnalyzeContext
from .checkpoint import Checkpoint, RunRecord, list_runs
from .config import Config, load_config
from .core import (
    ActionKind,
    ActionResult,
    Plan,
    Snapshot,
    VerifyMode,
    build_plan,
    execute,
    format_size,
    scan,
)
from .progress import ProgressDisplay
from .reporting import build_report, new_run_id, write_json_atomic


@dataclass(frozen=True)
class PlannedSync:
    source: Snapshot
    target: Snapshot
    plan: Plan
    compare: str


def _add_common(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--config", type=Path, default=Path("backup.toml"), help="TOML 配置文件")
    parser.add_argument(
        "--progress",
        choices=("auto", "always", "never"),
        default="auto",
        help="进度条显示方式",
    )
    parser.add_argument("--verbose", action="store_true")


def _add_compare(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--compare", choices=("smart", "hash"), help="同路径文件比较方式")
    parser.add_argument("--no-renames", action="store_true", help="禁用 rename 检测")


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(prog="backup-sync", description="安全的单向增量目录备份")
    commands = root.add_subparsers(dest="command", required=True)

    plan = commands.add_parser("plan", help="生成同步计划，不写入目标")
    _add_common(plan)
    _add_compare(plan)

    sync = commands.add_parser("sync", help="生成计划并确认后执行")
    _add_common(sync)
    _add_compare(sync)
    sync.add_argument("--yes", action="store_true", help="跳过交互确认")

    resume = commands.add_parser("resume", help="恢复失败或中断的任务")
    resume.add_argument("run_id")
    _add_common(resume)
    resume.add_argument("--yes", action="store_true", help="跳过交互确认")

    runs = commands.add_parser("runs", help="查看历史任务")
    run_commands = runs.add_subparsers(dest="runs_command", required=True)
    runs_list = run_commands.add_parser("list", help="列出全部任务")
    _add_common(runs_list)
    runs_list.add_argument("--json", action="store_true", dest="as_json")
    runs_failed = run_commands.add_parser("failed", help="列出失败或中断任务")
    _add_common(runs_failed)
    runs_failed.add_argument("--json", action="store_true", dest="as_json")
    runs_show = run_commands.add_parser("show", help="查看任务详情")
    runs_show.add_argument("run_id")
    _add_common(runs_show)
    runs_show.add_argument("--json", action="store_true", dest="as_json")

    analyze = commands.add_parser("analyze", help="运行只读分析器")
    analyze_commands = analyze.add_subparsers(dest="analyzer", required=True)
    for analyzer in ANALYZERS.values():
        analyzer_parser = analyze_commands.add_parser(analyzer.name, help=analyzer.description)
        _add_common(analyzer_parser)
        analyzer_parser.add_argument("--json", action="store_true", dest="as_json")
        analyzer.add_arguments(analyzer_parser)
    return root


def _load(path: Path) -> Config:
    return load_config(path)


def _plan_sync(config: Config, args: argparse.Namespace, progress: ProgressDisplay) -> PlannedSync:
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
    compare = getattr(args, "compare", None) or config.compare
    try:
        plan = build_plan(
            source,
            target,
            config.detect_renames and not getattr(args, "no_renames", False),
            progress_callback=progress.plan,
            compare_mode=compare,
        )
    finally:
        progress.close_plan()
    return PlannedSync(source, target, plan, compare)


def _show_plan(planned: PlannedSync, config: Config) -> None:
    source, target, plan = planned.source, planned.target, planned.plan
    hotspots = [
        (path, count)
        for path, count in source.small_file_parents.items()
        if count >= config.small_file_count
    ]
    if hotspots:
        print("\n小文件热点（可使用 analyze small-files 查看详情）：")
        for path, count in sorted(hotspots, key=lambda item: (-item[1], str(item[0])))[:10]:
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


def _confirm(planned: PlannedSync, assume_yes: bool) -> bool:
    if assume_yes:
        return True
    interactive = sys.stdin.isatty() or os.environ.get("PYCHARM_HOSTED") == "1"
    if not interactive:
        print("错误: 非交互环境执行 sync/resume 必须使用 --yes", file=sys.stderr)
        return False
    writes = sum(
        action.size
        for action in planned.plan.actions
        if action.kind in (ActionKind.COPY, ActionKind.UPDATE)
    )
    removals = planned.plan.count(ActionKind.REMOVE) + planned.plan.count(ActionKind.UPDATE)
    print(f"\n预计写入 {format_size(writes)}；{removals} 个旧文件将进入回收目录。")
    try:
        return input("输入 yes 继续: ").strip().lower() == "yes"
    except (EOFError, KeyboardInterrupt):
        print()
        return False


def _execute_sync(
    config: Config,
    planned: PlannedSync,
    progress: ProgressDisplay,
    checkpoint: Checkpoint | None = None,
    run_id: str | None = None,
) -> int:
    run_id = run_id or new_run_id()
    started_at = checkpoint.started_at if checkpoint else datetime.now().astimezone()
    recycle = checkpoint.recycle if checkpoint else config.recycle / run_id
    if checkpoint is None:
        checkpoint = Checkpoint.create(
            config.state, run_id, planned.source.root, planned.target.root, recycle, started_at
        )
    verify = VerifyMode(config.verify)

    def record_progress(action_result: ActionResult) -> None:
        checkpoint.record(action_result)
        progress.action_finished(action_result)

    progress.start_execution(len(planned.plan.actions))
    try:
        result = execute(
            planned.plan,
            planned.source,
            planned.target,
            recycle,
            verify=verify,
            retry_max=config.retry_max,
            retry_delay=config.retry_delay,
            progress_callback=record_progress,
            action_started_callback=progress.action_started,
        )
    finally:
        progress.close_execution()
    finished_at = datetime.now().astimezone()
    report_path = config.reports / f"{run_id}.json"
    report = build_report(
        run_id,
        started_at,
        finished_at,
        planned.source,
        planned.target,
        planned.plan,
        result,
        verify,
        recycle,
        planned.compare,
    )
    write_json_atomic(report_path, report)
    checkpoint.finish("success" if result.failed == 0 else "partial_failure", report_path)
    print(f"运行 {run_id} 完成：成功 {result.succeeded}，失败 {result.failed}；报告: {report_path}")
    if recycle.exists() and any(recycle.iterdir()):
        print(f"替换/删除的文件保存在: {recycle}")
    return 1 if result.failed else 0


def _record_dict(record: RunRecord) -> dict[str, object]:
    return {
        "run_id": record.run_id,
        "status": record.status,
        "started_at": record.started_at,
        "updated_at": record.updated_at,
        "source": record.source,
        "target": record.target,
        "succeeded": record.succeeded,
        "failed": record.failed,
        "error": record.error,
    }


def _print_runs(records: list[RunRecord], as_json: bool) -> None:
    if as_json:
        print(
            json.dumps([_record_dict(record) for record in records], ensure_ascii=False, indent=2)
        )
        return
    if not records:
        print("没有任务记录。")
        return
    print(f"{'RUN ID':30} {'STATUS':16} {'STARTED':25} {'OK':>6} {'FAIL':>6}")
    for record in records:
        print(
            f"{record.run_id:30} {record.status:16} "
            f"{record.started_at[:25]:25} {record.succeeded:6} {record.failed:6}"
        )


def _handle_runs(args: argparse.Namespace, config: Config) -> int:
    if args.runs_command == "show":
        try:
            checkpoint = Checkpoint.load(config.state, args.run_id)
        except ValueError as exc:
            print(f"任务错误: {exc}", file=sys.stderr)
            return 2
        if args.as_json:
            print(json.dumps(checkpoint.payload, ensure_ascii=False, indent=2))
        else:
            payload = checkpoint.payload
            print(f"run_id:     {payload['run_id']}")
            print(f"status:     {payload['status']}")
            print(f"source:     {payload['source']}")
            print(f"target:     {payload['target']}")
            print(f"started_at: {payload['started_at']}")
            print(f"updated_at: {payload['updated_at']}")
            failures = [result for result in payload.get("results", []) if not result["success"]]
            print(f"failures:   {len(failures)}")
            for failure in failures:
                print(f"  {failure['kind']:7} {failure['path']}: {failure['error']}")
        return 0
    records = list_runs(config.state)
    if args.runs_command == "failed":
        records = [
            record
            for record in records
            if record.status in {"partial_failure", "interrupted", "invalid"}
        ]
    _print_runs(records, args.as_json)
    return 0


def _handle_analyze(args: argparse.Namespace, config: Config) -> int:
    analyzer = ANALYZERS[args.analyzer]
    try:
        result = analyzer.analyze(AnalyzeContext(config, ProgressDisplay(args.progress)), args)
    except (OSError, ValueError) as exc:
        print(f"分析错误: {exc}", file=sys.stderr)
        return 3
    if args.as_json:
        print(json.dumps(result.as_dict(), ensure_ascii=False, indent=2))
    else:
        analyzer.render(result)
    return 0 if result.summary.get("healthy", True) is not False else 1


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )
    try:
        config = _load(args.config)
    except (ValueError, tomllib.TOMLDecodeError, FileNotFoundError) as exc:
        print(f"配置错误: {exc}", file=sys.stderr)
        return 2
    if args.command == "runs":
        return _handle_runs(args, config)
    if args.command == "analyze":
        return _handle_analyze(args, config)

    checkpoint: Checkpoint | None = None
    if args.command == "resume":
        try:
            checkpoint = Checkpoint.load(config.state, args.run_id)
            checkpoint.validate_paths(config.source, config.target)
        except (OSError, ValueError) as exc:
            print(f"任务错误: {exc}", file=sys.stderr)
            return 2
    progress = ProgressDisplay(args.progress)
    try:
        planned = _plan_sync(config, args, progress)
    except (OSError, ValueError) as exc:
        print(f"I/O 错误: {exc}", file=sys.stderr)
        return 3
    _show_plan(planned, config)
    if args.command == "plan":
        return 0
    if not planned.plan.actions and args.command == "sync":
        print("目标目录已经与源目录一致。")
        return 0
    if not _confirm(planned, args.yes):
        print("已取消，未修改目标目录。")
        return 0
    try:
        return _execute_sync(
            config,
            planned,
            progress,
            checkpoint=checkpoint,
            run_id=args.run_id if args.command == "resume" else None,
        )
    except OSError as exc:
        print(f"同步已部分执行，但状态或报告写入失败: {exc}", file=sys.stderr)
        return 3
