from __future__ import annotations

from .base import Analyzer
from .duplicates import DuplicatesAnalyzer
from .health import HealthAnalyzer
from .ignored import IgnoredAnalyzer
from .integrity import IntegrityAnalyzer
from .large_files import LargeFilesAnalyzer
from .small_files import SmallFilesAnalyzer
from .symlinks import SymlinksAnalyzer

ANALYZERS: dict[str, type[Analyzer]] = {
    "small-files": SmallFilesAnalyzer,
    "health": HealthAnalyzer,
    "large-files": LargeFilesAnalyzer,
    "duplicates": DuplicatesAnalyzer,
    "ignored": IgnoredAnalyzer,
    "integrity": IntegrityAnalyzer,
    "symlinks": SymlinksAnalyzer,
}
