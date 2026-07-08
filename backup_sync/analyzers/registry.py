from __future__ import annotations

from .base import Analyzer
from .duplicates import DuplicatesAnalyzer
from .health import HealthAnalyzer
from .ignored import IgnoredAnalyzer
from .integrity import IntegrityAnalyzer
from .small_files import SmallFilesAnalyzer

ANALYZERS: dict[str, type[Analyzer]] = {
    "small-files": SmallFilesAnalyzer,
    "health": HealthAnalyzer,
    "duplicates": DuplicatesAnalyzer,
    "ignored": IgnoredAnalyzer,
    "integrity": IntegrityAnalyzer,
}
