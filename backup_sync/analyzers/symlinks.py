from __future__ import annotations

import argparse
import os
from dataclasses import dataclass
from pathlib import Path

from ..ignore_rules import matches_ignore, normalize_ignore_patterns
from .base import AnalysisResult, AnalyzeContext, Analyzer, Finding


@dataclass(frozen=True)
class SymlinkEntry:
    label: str
    root: Path
    path: Path
    target: str
    broken: bool

    @property
    def display_path(self) -> str:
        return f"{self.label}:{self.path.as_posix()}"


class SymlinksAnalyzer(Analyzer):
    name = "symlinks"
    description = "列出扫描时会跳过的符号链接"

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
        parser.add_argument("--limit", type=int, default=50, help="最多展示的符号链接数量")
        parser.add_argument("--broken-only", action="store_true", help="只展示断开的符号链接")

    def analyze(self, context: AnalyzeContext, args: argparse.Namespace) -> AnalysisResult:
        if args.limit < 1:
            raise ValueError("limit 必须大于 0")
        explicit_paths = getattr(args, "path", None)
        if isinstance(explicit_paths, Path):
            requested_paths = (explicit_paths,)
        else:
            requested_paths = tuple(explicit_paths or ())
        scope = getattr(args, "scope", None)
        entries = self._collect_entries(context, args)
        if args.broken_only:  # noqa: SIM108 - keep this branch explicit for CLI option clarity.
            symlinks = [entry for entry in entries if entry.broken]
        else:
            symlinks = entries
        symlinks.sort(key=lambda entry: (not entry.broken, entry.display_path))
        findings = tuple(
            Finding(
                "warning" if entry.broken else "info",
                entry.display_path,
                {"target": entry.target, "broken": entry.broken, "root": str(entry.root)},
            )
            for entry in symlinks[: args.limit]
        )
        broken_count = sum(entry.broken for entry in entries)
        return AnalysisResult(
            self.name,
            {
                "scope": scope if scope is not None else "path",
                "paths": [str(path) for path in requested_paths],
                "symlinks": len(entries),
                "broken": broken_count,
                "shown": len(findings),
                "broken_only": args.broken_only,
            },
            findings,
        )

    def _collect_entries(
        self,
        context: AnalyzeContext,
        args: argparse.Namespace,
    ) -> list[SymlinkEntry]:
        roots: list[tuple[str, Path, tuple[str, ...]]]
        explicit_paths = getattr(args, "path", None)
        if explicit_paths:
            paths = (explicit_paths,) if isinstance(explicit_paths, Path) else tuple(explicit_paths)
            roots = [
                (f"path{index}", path.expanduser().resolve(), ())
                for index, path in enumerate(paths, start=1)
            ]
        else:
            scope = getattr(args, "scope", None)
            roots = []
            if scope == "source":
                roots.append(
                    (
                        "source",
                        context.config.source.expanduser().resolve(),
                        normalize_ignore_patterns(context.config.ignore),
                    )
                )
            if scope == "target":
                if not context.config.target.exists():
                    raise ValueError(f"目标目录不存在: {context.config.target}")
                roots.append(("target", context.config.target.expanduser().resolve(), ()))

        entries: list[SymlinkEntry] = []
        for label, root, ignore_patterns in roots:
            entries.extend(self._scan_root(label, root, ignore_patterns, context))
        return entries

    def _scan_root(
        self,
        label: str,
        root: Path,
        ignore_patterns: tuple[str, ...],
        context: AnalyzeContext,
    ) -> list[SymlinkEntry]:
        if not root.is_dir():
            raise ValueError(f"目录不存在或不可读: {str(root)!r}")
        entries: list[SymlinkEntry] = []
        pending = [root]
        with context.progress.scan(f"扫描 {label} 符号链接") as bar:
            while pending:
                current_path = pending.pop()
                relative_dir = current_path.relative_to(root)
                try:
                    children = sorted(os.scandir(current_path), key=lambda entry: entry.name)
                except OSError as exc:
                    raise OSError(f"无法读取目录: {current_path}") from exc
                child_directories: list[Path] = []
                for child in children:
                    relative = relative_dir / child.name
                    if matches_ignore(relative, ignore_patterns):
                        continue
                    if child.is_symlink():
                        path = Path(child.path)
                        target = os.readlink(path)
                        entries.append(
                            SymlinkEntry(
                                label,
                                root,
                                relative,
                                target,
                                not path.exists(),
                            )
                        )
                        bar.update(1)
                        continue
                    try:
                        if child.is_dir(follow_symlinks=False):
                            child_directories.append(Path(child.path))
                    except OSError as exc:
                        raise OSError(f"无法读取文件信息: {child.path}") from exc
                pending.extend(reversed(child_directories))
        return entries

    def render(self, result: AnalysisResult) -> None:
        summary = result.summary
        print(
            f"发现 {summary['symlinks']} 个符号链接，"
            f"其中 {summary['broken']} 个断开；展示 {summary['shown']} 个。"
        )
        for finding in result.findings:
            suffix = "broken" if finding.details["broken"] else "ok"
            print(f"  {finding.title} -> {finding.details['target']} ({suffix})")
