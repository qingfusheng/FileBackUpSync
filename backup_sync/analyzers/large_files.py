from __future__ import annotations

import argparse
from dataclasses import dataclass
from pathlib import Path

from ..formatting import format_size
from ..sync import FileInfo, Snapshot, scan
from .base import AnalysisResult, AnalyzeContext, Analyzer, Finding

DEFAULT_MIN_SIZE = 100 * 1024 * 1024


@dataclass(frozen=True)
class LargeFileEntry:
    label: str
    root: Path
    path: Path
    info: FileInfo

    @property
    def display_path(self) -> str:
        return f"{self.label}:{self.path.as_posix()}"


class LargeFilesAnalyzer(Analyzer):
    name = "large-files"
    description = "找出大文件和空间占用热点"

    def add_arguments(self, parser: argparse.ArgumentParser) -> None:
        parser.add_argument(
            "--scope",
            choices=("source", "target"),
            default="source",
            help="分析源目录或目标目录",
        )
        parser.add_argument(
            "--path",
            type=Path,
            action="append",
            default=(),
            help="额外指定待分析目录，可重复传入",
        )
        parser.add_argument("--min-size", type=int, default=DEFAULT_MIN_SIZE, help="最小文件字节数")
        parser.add_argument("--limit", type=int, default=20, help="最多展示的文件数")

    def analyze(self, context: AnalyzeContext, args: argparse.Namespace) -> AnalysisResult:
        if args.min_size < 0 or args.limit < 1:
            raise ValueError("min-size 不能为负数，limit 必须大于 0")
        requested_paths = tuple(args.path)
        entries = self._collect_entries(context, args)
        large_files = [entry for entry in entries if entry.info.size >= args.min_size]
        large_files.sort(key=lambda entry: (-entry.info.size, entry.display_path))
        total_bytes = sum(entry.info.size for entry in large_files)
        findings = tuple(
            Finding(
                "info",
                entry.display_path,
                {
                    "size": entry.info.size,
                    "formatted_size": format_size(entry.info.size),
                    "root": str(entry.root),
                },
            )
            for entry in large_files[: args.limit]
        )
        return AnalysisResult(
            self.name,
            {
                "scope": args.scope,
                "paths": [str(path) for path in requested_paths],
                "scanned_files": len(entries),
                "min_size": args.min_size,
                "min_size_formatted": format_size(args.min_size),
                "large_files": len(large_files),
                "total_bytes": total_bytes,
                "total_size": format_size(total_bytes),
            },
            findings,
        )

    def _collect_entries(
        self,
        context: AnalyzeContext,
        args: argparse.Namespace,
    ) -> list[LargeFileEntry]:
        snapshots: list[tuple[str, Snapshot]] = []
        if args.scope == "source":
            snapshots.append(
                (
                    "source",
                    context.source
                    or self._scan("source", context.config.source, context.config.ignore, context),
                )
            )
        if args.scope == "target":
            target = context.target
            if target is None:
                if not context.config.target.exists():
                    raise ValueError(f"目标目录不存在: {context.config.target}")
                target = self._scan("target", context.config.target, (), context)
            snapshots.append(("target", target))
        for index, path in enumerate(tuple(args.path), start=1):
            root = path.expanduser().resolve()
            snapshots.append((f"path{index}", self._scan(f"path{index}", root, (), context)))

        entries: list[LargeFileEntry] = []
        for label, snapshot in snapshots:
            entries.extend(
                LargeFileEntry(label, snapshot.root, path, info)
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

    def render(self, result: AnalysisResult) -> None:
        summary = result.summary
        print(
            f"扫描 {summary['scanned_files']} 个文件；"
            f"发现 {summary['large_files']} 个 >= {summary['min_size_formatted']} 的文件；"
            f"合计 {summary['total_size']}。"
        )
        for finding in result.findings:
            print(f"  {finding.title}: {finding.details['formatted_size']}")
