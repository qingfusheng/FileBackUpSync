from __future__ import annotations

from .base import Analyzer
from .health import HealthAnalyzer
from .ignored import IgnoredAnalyzer
from .small_files import SmallFilesAnalyzer

ANALYZERS: dict[str, type[Analyzer]] = {
    "small-files": SmallFilesAnalyzer,
    "health": HealthAnalyzer,
    "ignored": IgnoredAnalyzer,
}
