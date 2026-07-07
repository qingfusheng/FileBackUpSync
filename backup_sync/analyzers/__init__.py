from .base import AnalysisResult, AnalyzeContext, Analyzer, Finding
from .health import HealthAnalyzer
from .small_files import SmallFilesAnalyzer

ANALYZERS: dict[str, Analyzer] = {
    analyzer.name: analyzer for analyzer in (SmallFilesAnalyzer(), HealthAnalyzer())
}

__all__ = ["ANALYZERS", "AnalysisResult", "AnalyzeContext", "Analyzer", "Finding"]
