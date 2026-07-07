from __future__ import annotations

import argparse
import os
import shutil
from pathlib import Path

from ..core import format_size
from .base import AnalysisResult, AnalyzeContext, Analyzer, Finding


class HealthAnalyzer(Analyzer):
    name = "health"
    description = "检查路径、权限和目标剩余空间"

    def add_arguments(self, parser: argparse.ArgumentParser) -> None:
        del parser

    def analyze(self, context: AnalyzeContext, args: argparse.Namespace) -> AnalysisResult:
        del args
        config = context.config
        findings: list[Finding] = []
        self._check_path("source", config.source, require_write=False, findings=findings)
        self._check_path("target", config.target, require_write=True, findings=findings)
        if config.target.exists():
            usage = shutil.disk_usage(config.target)
            findings.append(
                Finding(
                    "info",
                    "target-space",
                    {
                        "free_bytes": usage.free,
                        "free": format_size(usage.free),
                        "total_bytes": usage.total,
                        "total": format_size(usage.total),
                    },
                )
            )
        errors = sum(finding.level == "error" for finding in findings)
        warnings = sum(finding.level == "warning" for finding in findings)
        return AnalysisResult(
            self.name,
            {"healthy": errors == 0, "errors": errors, "warnings": warnings},
            tuple(findings),
        )

    @staticmethod
    def _check_path(name: str, path: Path, require_write: bool, findings: list[Finding]) -> None:
        if not path.is_dir():
            findings.append(Finding("error", name, {"path": str(path), "issue": "目录不存在"}))
            return
        if not os.access(path, os.R_OK):
            findings.append(Finding("error", name, {"path": str(path), "issue": "目录不可读"}))
            return
        if require_write and not os.access(path, os.W_OK):
            findings.append(Finding("error", name, {"path": str(path), "issue": "目录不可写"}))
            return
        findings.append(Finding("ok", name, {"path": str(path), "issue": "正常"}))

    def render(self, result: AnalysisResult) -> None:
        icons = {"ok": "✓", "info": "ℹ", "warning": "⚠", "error": "✗"}
        for finding in result.findings:
            details = ", ".join(f"{key}={value}" for key, value in finding.details.items())
            print(f"{icons[finding.level]} {finding.title}: {details}")
        print("检查通过。" if result.summary["healthy"] else "检查发现错误。")
