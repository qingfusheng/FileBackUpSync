from __future__ import annotations

import argparse
import os
from pathlib import Path

from ..ignore_rules import matches_ignore, normalize_ignore_patterns
from .base import AnalysisResult, AnalyzeContext, Analyzer, Finding


class IgnoredAnalyzer(Analyzer):
    name = "ignored"
    description = "检查 ignore 规则命中的文件和目录"

    def add_arguments(self, parser: argparse.ArgumentParser) -> None:
        parser.add_argument("--limit", type=int, default=50, help="最多展示的忽略项数量")

    def analyze(self, context: AnalyzeContext, args: argparse.Namespace) -> AnalysisResult:
        if args.limit < 1:
            raise ValueError("limit 必须大于 0")
        patterns = normalize_ignore_patterns(context.config.ignore)
        if not patterns:
            return AnalysisResult(
                self.name,
                {"patterns": 0, "ignored_files": 0, "ignored_directories": 0, "shown": 0},
                (),
                ("配置中没有启用 ignore 规则。",),
            )

        root = context.config.source.expanduser().resolve()
        if not root.is_dir():
            raise ValueError(f"目录不存在或不可读: {str(root)!r}")

        ignored_files = 0
        ignored_directories = 0
        findings: list[Finding] = []
        pending = [root]

        with context.progress.scan("分析忽略规则") as bar:
            while pending:
                current_path = pending.pop()
                relative_dir = current_path.relative_to(root)
                try:
                    entries = sorted(os.scandir(current_path), key=lambda entry: entry.name)
                except OSError as exc:
                    raise OSError(f"无法读取目录: {current_path}") from exc

                child_directories: list[Path] = []
                for entry in entries:
                    relative = relative_dir / entry.name
                    if matches_ignore(relative, patterns):
                        try:
                            is_directory = entry.is_dir(follow_symlinks=False)
                        except OSError as exc:
                            raise OSError(f"无法读取文件信息: {entry.path}") from exc
                        if is_directory:
                            ignored_directories += 1
                        else:
                            ignored_files += 1
                        if len(findings) < args.limit:
                            findings.append(
                                Finding(
                                    "info",
                                    relative.as_posix(),
                                    {"kind": "directory" if is_directory else "file"},
                                )
                            )
                        bar.update(1)
                        continue
                    if entry.is_symlink():
                        continue
                    try:
                        if entry.is_dir(follow_symlinks=False):
                            child_directories.append(Path(entry.path))
                    except OSError as exc:
                        raise OSError(f"无法读取文件信息: {entry.path}") from exc
                pending.extend(reversed(child_directories))

        ignored_total = ignored_files + ignored_directories
        warnings: tuple[str, ...] = ()
        if ignored_total > len(findings):
            warnings = (f"仅展示前 {len(findings)} 个忽略项，共 {ignored_total} 个。",)
        return AnalysisResult(
            self.name,
            {
                "patterns": len(patterns),
                "ignored_files": ignored_files,
                "ignored_directories": ignored_directories,
                "shown": len(findings),
            },
            tuple(findings),
            warnings,
        )

    def render(self, result: AnalysisResult) -> None:
        summary = result.summary
        print(
            f"检查 {summary['patterns']} 条 ignore 规则；命中 "
            f"{summary['ignored_files']} 个文件、{summary['ignored_directories']} 个目录。"
        )
        for finding in result.findings:
            print(f"  {finding.title}: {finding.details['kind']}")
        for warning in result.warnings:
            print(f"警告: {warning}")
