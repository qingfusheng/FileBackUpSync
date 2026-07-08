from __future__ import annotations

import argparse
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

from ..formatting import format_size
from ..storage.fingerprint import strong_digest
from ..sync import FileInfo, Snapshot, scan
from .base import AnalysisResult, AnalyzeContext, Analyzer, Finding


@dataclass(frozen=True)
class DuplicateEntry:
    label: str
    root: Path
    path: Path
    info: FileInfo

    @property
    def display_path(self) -> str:
        return f"{self.label}:{self.path.as_posix()}"


class DuplicatesAnalyzer(Analyzer):
    name = "duplicates"
    description = "按内容 hash 查找重复文件"

    def add_arguments(self, parser: argparse.ArgumentParser) -> None:
        group = parser.add_mutually_exclusive_group(required=True)
        group.add_argument(
            "--scope",
            choices=("source", "target"),
            help="分析配置中的源目录或目标目录，与 --path 二选一",
        )
        group.add_argument(
            "--path",
            type=Path,
            action="append",
            default=None,
            help="直接指定待分析目录；可重复传入，与 --scope 二选一",
        )
        parser.add_argument("--limit", type=int, default=20, help="最多展示的重复文件组数")
        parser.add_argument("--min-size", type=int, default=1, help="参与重复检测的最小文件字节数")
        parser.add_argument("--estimate-only", action="store_true", help="只估算 hash 读取量")
        parser.add_argument(
            "--yes",
            action="store_true",
            help="确认执行可能大量读取文件的 hash 检测",
        )

    def analyze(self, context: AnalyzeContext, args: argparse.Namespace) -> AnalysisResult:
        if args.limit < 1 or args.min_size < 0:
            raise ValueError("limit 必须大于 0，min-size 不能为负数")
        explicit_paths = getattr(args, "path", None)
        if isinstance(explicit_paths, Path):
            requested_paths = (explicit_paths,)
        else:
            requested_paths = tuple(explicit_paths or ())
        scope = getattr(args, "scope", None)
        entries = self._collect_entries(context, args)
        by_size: dict[int, list[DuplicateEntry]] = defaultdict(list)
        for entry in entries:
            if entry.info.size >= args.min_size:
                by_size[entry.info.size].append(entry)
        candidates = [files for files in by_size.values() if len(files) > 1]
        estimated_bytes = sum(entry.info.size for files in candidates for entry in files)
        if args.estimate_only:
            return self._estimate_result(args, entries, candidates, estimated_bytes)
        if estimated_bytes and not args.yes:
            raise ValueError(
                f"duplicates 需要读取约 {format_size(estimated_bytes)} 计算 hash；"
                "请添加 --yes 确认，或使用 --estimate-only 仅查看估算。"
            )

        by_hash: dict[str, list[DuplicateEntry]] = defaultdict(list)
        with context.progress.scan("计算重复文件 hash") as bar:
            for files in candidates:
                for entry in files:
                    digest, _bytes_read = strong_digest(entry.root / entry.path)
                    by_hash[digest].append(entry)
                    bar.update(1)
        duplicate_groups = [files for files in by_hash.values() if len(files) > 1]
        duplicate_groups.sort(
            key=lambda files: (-files[0].info.size * (len(files) - 1), files[0].display_path)
        )
        findings = tuple(
            Finding(
                "warning",
                files[0].display_path,
                {
                    "count": len(files),
                    "size": files[0].info.size,
                    "formatted_size": format_size(files[0].info.size),
                    "duplicate_bytes": files[0].info.size * (len(files) - 1),
                    "paths": [entry.display_path for entry in files],
                },
            )
            for files in duplicate_groups[: args.limit]
        )
        duplicate_files = sum(len(files) for files in duplicate_groups)
        duplicate_bytes = sum(files[0].info.size * (len(files) - 1) for files in duplicate_groups)
        return AnalysisResult(
            self.name,
            {
                "scope": scope if scope is not None else "path",
                "paths": [str(path) for path in requested_paths],
                "scanned_files": len(entries),
                "candidate_groups": len(candidates),
                "estimated_hash_bytes": estimated_bytes,
                "estimated_hash_read": format_size(estimated_bytes),
                "duplicate_groups": len(duplicate_groups),
                "duplicate_files": duplicate_files,
                "duplicate_bytes": duplicate_bytes,
                "duplicate_size": format_size(duplicate_bytes),
            },
            findings,
            (f"本次 hash 读取约 {format_size(estimated_bytes)}。",),
        )

    def _collect_entries(
        self,
        context: AnalyzeContext,
        args: argparse.Namespace,
    ) -> list[DuplicateEntry]:
        explicit_paths = getattr(args, "path", None)
        if isinstance(explicit_paths, Path):
            paths: tuple[Path, ...] = (explicit_paths,)
        else:
            paths = tuple(explicit_paths or ())
        scope = getattr(args, "scope", None)
        if explicit_paths:
            snapshots = [
                (f"path{index}", self._scan(f"path{index}", path.expanduser().resolve(), (), context))
                for index, path in enumerate(paths, start=1)
            ]
            return [
                DuplicateEntry(label, snapshot.root, path, info)
                for label, snapshot in snapshots
                for path, info in snapshot.files.items()
            ]
        snapshots: list[tuple[str, Snapshot]] = []
        if scope == "source":
            snapshots.append(
                (
                    "source",
                    context.source
                    or self._scan("source", context.config.source, context.config.ignore, context),
                )
            )
        if scope == "target":
            target = context.target
            if target is None:
                if not context.config.target.exists():
                    raise ValueError(f"目标目录不存在: {context.config.target}")
                target = self._scan("target", context.config.target, (), context)
            snapshots.append(("target", target))
        for index, path in enumerate(paths, start=1):
            root = path.expanduser().resolve()
            snapshots.append((f"path{index}", self._scan(f"path{index}", root, (), context)))
        entries: list[DuplicateEntry] = []
        for label, snapshot in snapshots:
            entries.extend(
                DuplicateEntry(label, snapshot.root, path, info)
                for path, info in snapshot.files.items()
            )
        return entries

    def _scan(
        self,
        label: str,
        root: Path,
        ignore: tuple[str, ...],
        context: AnalyzeContext,
    ) -> Snapshot:
        with context.progress.scan(f"扫描 {label}") as bar:

            def update(_path: Path) -> None:
                bar.update(1)

            return scan(
                root,
                ignore,
                context.config.small_file_size,
                progress_callback=update,
            )

    def _estimate_result(
        self,
        args: argparse.Namespace,
        entries: list[DuplicateEntry],
        candidates: list[list[DuplicateEntry]],
        estimated_bytes: int,
    ) -> AnalysisResult:
        explicit_paths = getattr(args, "path", None)
        if isinstance(explicit_paths, Path):
            requested_paths = (explicit_paths,)
        else:
            requested_paths = tuple(explicit_paths or ())
        scope = getattr(args, "scope", None)
        return AnalysisResult(
            self.name,
            {
                "scope": scope if scope is not None else "path",
                "paths": [str(path) for path in requested_paths],
                "scanned_files": len(entries),
                "candidate_groups": len(candidates),
                "estimated_hash_bytes": estimated_bytes,
                "estimated_hash_read": format_size(estimated_bytes),
                "duplicate_groups": None,
            },
            (),
            (
                f"预计需要读取约 {format_size(estimated_bytes)} 计算 hash；"
                "添加 --yes 执行完整重复检测。",
            ),
        )

    def render(self, result: AnalysisResult) -> None:
        summary = result.summary
        print(
            f"扫描 {summary['scanned_files']} 个文件；"
            f"候选重复大小组 {summary['candidate_groups']} 个；"
            f"预计 hash 读取 {summary['estimated_hash_read']}。"
        )
        if summary["duplicate_groups"] is None:
            for warning in result.warnings:
                print(f"提示: {warning}")
            return
        print(
            f"发现 {summary['duplicate_groups']} 个重复组，可节省约 {summary['duplicate_size']}。"
        )
        for finding in result.findings:
            print(
                f"  {finding.details['count']} 个 x {finding.details['formatted_size']}: "
                f"{', '.join(finding.details['paths'])}"
            )
        for warning in result.warnings:
            print(f"提示: {warning}")
