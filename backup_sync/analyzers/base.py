from __future__ import annotations

import argparse
from abc import ABC, abstractmethod
from dataclasses import asdict, dataclass
from typing import Any

from ..config import Config
from ..progress import ProgressDisplay
from ..sync import Snapshot


@dataclass(frozen=True)
class AnalyzeContext:
    config: Config
    progress: ProgressDisplay
    source: Snapshot | None = None
    target: Snapshot | None = None


@dataclass(frozen=True)
class Finding:
    level: str
    title: str
    details: dict[str, Any]


@dataclass(frozen=True)
class AnalysisResult:
    analyzer: str
    summary: dict[str, Any]
    findings: tuple[Finding, ...] = ()
    warnings: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        return {
            "analyzer": self.analyzer,
            "summary": self.summary,
            "findings": [asdict(finding) for finding in self.findings],
            "warnings": list(self.warnings),
        }


class Analyzer(ABC):
    name: str
    description: str

    @abstractmethod
    def add_arguments(self, parser: argparse.ArgumentParser) -> None: ...

    @abstractmethod
    def analyze(self, context: AnalyzeContext, args: argparse.Namespace) -> AnalysisResult: ...

    @abstractmethod
    def render(self, result: AnalysisResult) -> None: ...
