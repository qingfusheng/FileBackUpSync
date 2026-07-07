import argparse
import tempfile
import unittest
from pathlib import Path

from backup_sync.analyzers.base import AnalysisResult, AnalyzeContext, Analyzer
from backup_sync.analyzers.health import HealthAnalyzer
from backup_sync.analyzers.small_files import SmallFilesAnalyzer
from backup_sync.config import load_config
from backup_sync.progress import ProgressDisplay


class DummyAnalyzer(Analyzer):
    name = "dummy"
    description = "test"

    def add_arguments(self, parser: argparse.ArgumentParser) -> None:
        parser.add_argument("--value", default="ok")

    def analyze(self, context: AnalyzeContext, args: argparse.Namespace) -> AnalysisResult:
        return AnalysisResult(
            self.name, {"value": args.value, "source": str(context.config.source)}
        )

    def render(self, result: AnalysisResult) -> None:
        del result


class AnalyzerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.source = self.root / "source"
        self.target = self.root / "target"
        self.source.mkdir()
        self.target.mkdir()
        self.config_path = self.root / "backup.toml"
        self.config_path.write_text(
            f'[paths]\nsource = "{self.source}"\ntarget = "{self.target}"\n'
        )
        self.context = AnalyzeContext(load_config(self.config_path), ProgressDisplay("never"))

    def tearDown(self):
        self.temp.cleanup()

    def test_small_files_reports_hotspot(self):
        cache = self.source / "cache"
        cache.mkdir()
        for index in range(3):
            (cache / f"{index}.txt").write_bytes(b"x")
        args = argparse.Namespace(size=10, count=2, limit=20)
        result = SmallFilesAnalyzer().analyze(self.context, args)
        self.assertEqual(result.summary["hotspot_count"], 1)
        self.assertEqual(result.findings[0].title, "cache")
        self.assertEqual(result.findings[0].details["count"], 3)

    def test_health_checks_source_and_target(self):
        result = HealthAnalyzer().analyze(self.context, argparse.Namespace())
        self.assertTrue(result.summary["healthy"])
        self.assertEqual(
            {finding.title for finding in result.findings}, {"source", "target", "target-space"}
        )

    def test_analyzer_contract_is_extensible(self):
        analyzer = DummyAnalyzer()
        result = analyzer.analyze(self.context, argparse.Namespace(value="custom"))
        self.assertEqual(result.summary["value"], "custom")


if __name__ == "__main__":
    unittest.main()
