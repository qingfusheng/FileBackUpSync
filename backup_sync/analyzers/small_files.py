from __future__ import annotations

import argparse
from collections import defaultdict
from pathlib import Path

from ..formatting import format_size
from ..sync import scan
from .base import AnalysisResult, AnalyzeContext, Analyzer, Finding


class SmallFilesAnalyzer(Analyzer):
    name = "small-files"
    description = "分析小文件热点目录"

    def add_arguments(self, parser: argparse.ArgumentParser) -> None:
        parser.add_argument("--path", type=Path, help="直接指定分析目录，覆盖配置中的 source")
        parser.add_argument("--size", type=int, help="小文件字节阈值，默认读取配置")
        parser.add_argument("--count", type=int, help="热点文件数量阈值，默认读取配置")
        parser.add_argument("--limit", type=int, default=20, help="最多展示的目录数")

    def analyze(self, context: AnalyzeContext, args: argparse.Namespace) -> AnalysisResult:
        threshold = args.size if args.size is not None else context.config.small_file_size
        minimum = args.count if args.count is not None else context.config.small_file_count
        if threshold < 0 or minimum < 1 or args.limit < 1:
            raise ValueError("size 不能为负数，count 和 limit 必须大于 0")
        explicit_path = getattr(args, "path", None)
        root = (
            explicit_path.expanduser().resolve()
            if explicit_path is not None
            else context.config.source.expanduser().resolve()
        )
        with context.progress.scan("分析小文件") as bar:

            def update(_path: Path) -> None:
                bar.update(1)

            snapshot = scan(
                root,
                context.config.ignore,
                threshold,
                progress_callback=update,
            )
        sizes: dict[Path, int] = defaultdict(int)
        for path, info in snapshot.files.items():
            if info.size <= threshold:
                sizes[path.parent] += info.size
        hotspots = [
            (path, count, sizes[path])
            for path, count in snapshot.small_file_parents.items()
            if count >= minimum
        ]
        hotspots.sort(key=lambda item: (-item[1], str(item[0])))
        findings = tuple(
            Finding(
                "warning",
                path.as_posix(),
                {"count": count, "bytes": size, "formatted_size": format_size(size)},
            )
            for path, count, size in hotspots[: args.limit]
        )
        return AnalysisResult(
            self.name,
            {
                "scanned_files": len(snapshot.files),
                "root": str(snapshot.root),
                "small_file_size": threshold,
                "minimum_count": minimum,
                "hotspot_count": len(hotspots),
            },
            findings,
        )

    def render(self, result: AnalysisResult) -> None:
        summary = result.summary
        print(
            f"扫描 {summary['scanned_files']} 个文件（{summary['root']}）；发现 "
            f"{summary['hotspot_count']} 个小文件热点目录。"
        )
        for finding in result.findings:
            print(
                f"  {finding.title}: {finding.details['count']} 个，"
                f"合计 {finding.details['formatted_size']}"
            )
