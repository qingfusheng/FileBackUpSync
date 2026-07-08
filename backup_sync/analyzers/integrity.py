from __future__ import annotations

import argparse
from pathlib import Path

from ..formatting import format_size
from ..storage.fingerprint import strong_digest
from ..sync import Snapshot, empty_snapshot, scan
from .base import AnalysisResult, AnalyzeContext, Analyzer, Finding


class IntegrityAnalyzer(Analyzer):
    name = "integrity"
    description = "按同路径文件 hash 校验源目录和目标目录一致性"

    def add_arguments(self, parser: argparse.ArgumentParser) -> None:
        parser.add_argument("--limit", type=int, default=50, help="最多展示的问题数量")
        parser.add_argument("--estimate-only", action="store_true", help="只估算 hash 读取量")
        parser.add_argument(
            "--yes",
            action="store_true",
            help="确认执行可能大量读取文件的 hash 校验",
        )

    def analyze(self, context: AnalyzeContext, args: argparse.Namespace) -> AnalysisResult:
        if args.limit < 1:
            raise ValueError("limit 必须大于 0")
        source = context.source or self._scan(context.config.source, context.config.ignore, context)
        if context.target is not None:
            target = context.target
        elif context.config.target.exists():
            target = self._scan(context.config.target, (), context)
        else:
            target = empty_snapshot(context.config.target)

        source_paths = set(source.files)
        target_paths = set(target.files)
        missing = sorted(source_paths - target_paths)
        extra = sorted(target_paths - source_paths)
        common = sorted(source_paths & target_paths)
        size_mismatches = [
            path for path in common if source.files[path].size != target.files[path].size
        ]
        hash_candidates = [
            path for path in common if source.files[path].size == target.files[path].size
        ]
        estimated_bytes = sum(
            source.files[path].size + target.files[path].size for path in hash_candidates
        )

        if args.estimate_only:
            return self._estimate_result(
                source,
                target,
                missing,
                extra,
                size_mismatches,
                estimated_bytes,
            )
        if estimated_bytes and not args.yes:
            raise ValueError(
                f"integrity 需要读取约 {format_size(estimated_bytes)} 计算 hash；"
                "请添加 --yes 确认，或使用 --estimate-only 仅查看估算。"
            )

        hash_mismatches: list[Path] = []
        with context.progress.scan("校验文件 hash") as bar:
            for path in hash_candidates:
                source_digest, _source_bytes = strong_digest(source.root / path)
                target_digest, _target_bytes = strong_digest(target.root / path)
                if source_digest != target_digest:
                    hash_mismatches.append(path)
                bar.update(1)

        findings = self._build_findings(
            source,
            target,
            missing,
            extra,
            size_mismatches,
            hash_mismatches,
            args.limit,
        )
        issue_count = len(missing) + len(extra) + len(size_mismatches) + len(hash_mismatches)
        return AnalysisResult(
            self.name,
            {
                "source_files": len(source.files),
                "target_files": len(target.files),
                "missing": len(missing),
                "extra": len(extra),
                "size_mismatches": len(size_mismatches),
                "hash_mismatches": len(hash_mismatches),
                "estimated_hash_bytes": estimated_bytes,
                "estimated_hash_read": format_size(estimated_bytes),
                "healthy": issue_count == 0,
            },
            tuple(findings),
            (f"本次 hash 读取约 {format_size(estimated_bytes)}。",),
        )

    def _scan(self, root: Path, ignore: tuple[str, ...], context: AnalyzeContext) -> Snapshot:
        with context.progress.scan(f"扫描 {root}") as bar:

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
        source: Snapshot,
        target: Snapshot,
        missing: list[Path],
        extra: list[Path],
        size_mismatches: list[Path],
        estimated_bytes: int,
    ) -> AnalysisResult:
        issue_count = len(missing) + len(extra) + len(size_mismatches)
        return AnalysisResult(
            self.name,
            {
                "source_files": len(source.files),
                "target_files": len(target.files),
                "missing": len(missing),
                "extra": len(extra),
                "size_mismatches": len(size_mismatches),
                "hash_mismatches": None,
                "estimated_hash_bytes": estimated_bytes,
                "estimated_hash_read": format_size(estimated_bytes),
                "healthy": False if issue_count else None,
            },
            (),
            (
                f"预计需要读取约 {format_size(estimated_bytes)} 计算 hash；"
                "添加 --yes 执行完整一致性校验。",
            ),
        )

    def _build_findings(
        self,
        source: Snapshot,
        target: Snapshot,
        missing: list[Path],
        extra: list[Path],
        size_mismatches: list[Path],
        hash_mismatches: list[Path],
        limit: int,
    ) -> list[Finding]:
        findings: list[Finding] = []
        for path in missing:
            findings.append(
                Finding(
                    "error",
                    path.as_posix(),
                    {"issue": "missing-in-target", "source_size": source.files[path].size},
                )
            )
        for path in extra:
            findings.append(
                Finding(
                    "warning",
                    path.as_posix(),
                    {"issue": "extra-in-target", "target_size": target.files[path].size},
                )
            )
        for path in size_mismatches:
            findings.append(
                Finding(
                    "error",
                    path.as_posix(),
                    {
                        "issue": "size-mismatch",
                        "source_size": source.files[path].size,
                        "target_size": target.files[path].size,
                    },
                )
            )
        for path in hash_mismatches:
            findings.append(Finding("error", path.as_posix(), {"issue": "hash-mismatch"}))
        return findings[:limit]

    def render(self, result: AnalysisResult) -> None:
        summary = result.summary
        print(
            f"源 {summary['source_files']} 个文件，目标 {summary['target_files']} 个文件；"
            f"预计 hash 读取 {summary['estimated_hash_read']}。"
        )
        if summary["hash_mismatches"] is None:
            print(
                f"已知问题：missing={summary['missing']}, extra={summary['extra']}, "
                f"size_mismatch={summary['size_mismatches']}。"
            )
            for warning in result.warnings:
                print(f"提示: {warning}")
            return
        print(
            f"问题：missing={summary['missing']}, extra={summary['extra']}, "
            f"size_mismatch={summary['size_mismatches']}, "
            f"hash_mismatch={summary['hash_mismatches']}。"
        )
        for finding in result.findings:
            details = ", ".join(f"{key}={value}" for key, value in finding.details.items())
            print(f"  {finding.level} {finding.title}: {details}")
        for warning in result.warnings:
            print(f"提示: {warning}")
