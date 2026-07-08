import argparse
import tempfile
import unittest
from pathlib import Path

from backup_sync.analyzers.base import AnalysisResult, AnalyzeContext, Analyzer
from backup_sync.analyzers.duplicates import DuplicatesAnalyzer
from backup_sync.analyzers.health import HealthAnalyzer
from backup_sync.analyzers.ignored import IgnoredAnalyzer
from backup_sync.analyzers.integrity import IntegrityAnalyzer
from backup_sync.analyzers.large_files import LargeFilesAnalyzer
from backup_sync.analyzers.registry import ANALYZERS
from backup_sync.analyzers.small_files import SmallFilesAnalyzer
from backup_sync.analyzers.symlinks import SymlinksAnalyzer
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

    def test_small_files_can_scan_explicit_path(self):
        extra = self.root / "extra"
        cache = extra / "cache"
        cache.mkdir(parents=True)
        for index in range(3):
            (cache / f"{index}.txt").write_bytes(b"x")

        result = SmallFilesAnalyzer().analyze(
            self.context,
            argparse.Namespace(path=extra, size=10, count=2, limit=20),
        )

        self.assertEqual(result.summary["root"], str(extra.resolve()))
        self.assertEqual(result.summary["hotspot_count"], 1)
        self.assertEqual(result.findings[0].title, "cache")

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

    def test_registry_uses_explicit_analyzer_classes(self):
        self.assertIs(ANALYZERS["small-files"], SmallFilesAnalyzer)
        self.assertIs(ANALYZERS["health"], HealthAnalyzer)
        self.assertIs(ANALYZERS["large-files"], LargeFilesAnalyzer)
        self.assertIs(ANALYZERS["duplicates"], DuplicatesAnalyzer)
        self.assertIs(ANALYZERS["ignored"], IgnoredAnalyzer)
        self.assertIs(ANALYZERS["integrity"], IntegrityAnalyzer)
        self.assertIs(ANALYZERS["symlinks"], SymlinksAnalyzer)

    def test_ignored_reports_matches_without_touching_target(self):
        self.config_path.write_text(
            "\n".join(
                [
                    "[paths]",
                    f'source = "{self.source}"',
                    f'target = "{self.target}"',
                    "[ignore]",
                    'patterns = ["*.tmp", "cache/"]',
                ]
            )
        )
        (self.source / "keep.txt").write_text("keep")
        (self.source / "scratch.tmp").write_text("tmp")
        cache = self.source / "cache"
        cache.mkdir()
        (cache / "nested.txt").write_text("ignored with directory")
        before_target = set(self.target.iterdir())

        context = AnalyzeContext(load_config(self.config_path), ProgressDisplay("never"))
        result = IgnoredAnalyzer().analyze(context, argparse.Namespace(limit=10))

        self.assertEqual(result.summary["ignored_files"], 1)
        self.assertEqual(result.summary["ignored_directories"], 1)
        self.assertEqual({finding.title for finding in result.findings}, {"scratch.tmp", "cache"})
        self.assertEqual(set(self.target.iterdir()), before_target)

    def test_ignored_can_scan_explicit_path(self):
        self.config_path.write_text(
            "\n".join(
                [
                    "[paths]",
                    f'source = "{self.source}"',
                    f'target = "{self.target}"',
                    "[ignore]",
                    'patterns = ["*.tmp", "cache/"]',
                ]
            )
        )
        extra = self.root / "extra"
        cache = extra / "cache"
        cache.mkdir(parents=True)
        (extra / "scratch.tmp").write_text("tmp")
        (cache / "nested.txt").write_text("ignored with directory")

        context = AnalyzeContext(load_config(self.config_path), ProgressDisplay("never"))
        result = IgnoredAnalyzer().analyze(context, argparse.Namespace(path=extra, limit=10))

        self.assertEqual(result.summary["root"], str(extra.resolve()))
        self.assertEqual(result.summary["ignored_files"], 1)
        self.assertEqual(result.summary["ignored_directories"], 1)
        self.assertEqual({finding.title for finding in result.findings}, {"scratch.tmp", "cache"})

    def test_duplicates_reports_matching_content(self):
        (self.source / "a.txt").write_text("same")
        (self.source / "b.txt").write_text("same")
        (self.source / "c.txt").write_text("different")

        result = DuplicatesAnalyzer().analyze(
            self.context,
            argparse.Namespace(
                scope="source",
                path=(),
                limit=10,
                min_size=1,
                estimate_only=False,
                yes=True,
            ),
        )

        self.assertEqual(result.summary["duplicate_groups"], 1)
        self.assertEqual(result.summary["duplicate_files"], 2)
        self.assertEqual(
            set(result.findings[0].details["paths"]),
            {"source:a.txt", "source:b.txt"},
        )

    def test_duplicates_can_scan_target(self):
        (self.target / "a.txt").write_text("same")
        (self.target / "b.txt").write_text("same")

        result = DuplicatesAnalyzer().analyze(
            self.context,
            argparse.Namespace(
                scope="target",
                path=(),
                limit=10,
                min_size=1,
                estimate_only=False,
                yes=True,
            ),
        )

        self.assertEqual(result.summary["scope"], "target")
        self.assertEqual(result.summary["duplicate_groups"], 1)
        self.assertEqual(
            set(result.findings[0].details["paths"]),
            {"target:a.txt", "target:b.txt"},
        )

    def test_duplicates_can_scan_explicit_path(self):
        extra = self.root / "extra"
        extra.mkdir()
        (self.source / "source.txt").write_text("same")
        (extra / "a.txt").write_text("same")
        (extra / "b.txt").write_text("same")

        result = DuplicatesAnalyzer().analyze(
            self.context,
            argparse.Namespace(
                scope=None,
                path=(extra,),
                limit=10,
                min_size=1,
                estimate_only=False,
                yes=True,
            ),
        )

        self.assertEqual(result.summary["paths"], [str(extra)])
        self.assertEqual(result.summary["scope"], "path")
        self.assertEqual(result.summary["duplicate_groups"], 1)
        self.assertEqual(set(result.findings[0].details["paths"]), {"path1:a.txt", "path1:b.txt"})

    def test_duplicates_requires_confirmation_for_hashing(self):
        (self.source / "a.txt").write_text("same")
        (self.source / "b.txt").write_text("same")

        with self.assertRaises(ValueError):
            DuplicatesAnalyzer().analyze(
                self.context,
                argparse.Namespace(
                    scope="source",
                    path=(),
                    limit=10,
                    min_size=1,
                    estimate_only=False,
                    yes=False,
                ),
            )

    def test_integrity_reports_hash_mismatch(self):
        (self.source / "same-size.txt").write_text("left")
        (self.target / "same-size.txt").write_text("rift")

        result = IntegrityAnalyzer().analyze(
            self.context,
            argparse.Namespace(limit=10, estimate_only=False, yes=True),
        )

        self.assertFalse(result.summary["healthy"])
        self.assertEqual(result.summary["hash_mismatches"], 1)
        self.assertEqual(result.findings[0].details["issue"], "hash-mismatch")

    def test_integrity_estimate_only_skips_hashing(self):
        (self.source / "file.txt").write_text("left")
        (self.target / "file.txt").write_text("left")

        result = IntegrityAnalyzer().analyze(
            self.context,
            argparse.Namespace(limit=10, estimate_only=True, yes=False),
        )

        self.assertIsNone(result.summary["hash_mismatches"])
        self.assertEqual(result.summary["estimated_hash_bytes"], 8)

    def test_large_files_reports_largest_source_files(self):
        (self.source / "small.bin").write_bytes(b"x" * 3)
        (self.source / "large.bin").write_bytes(b"x" * 8)
        (self.source / "larger.bin").write_bytes(b"x" * 10)

        result = LargeFilesAnalyzer().analyze(
            self.context,
            argparse.Namespace(scope="source", path=(), min_size=8, limit=10),
        )

        self.assertEqual(result.summary["large_files"], 2)
        self.assertEqual(
            [finding.title for finding in result.findings],
            ["source:larger.bin", "source:large.bin"],
        )

    def test_large_files_can_scan_target(self):
        (self.target / "large.bin").write_bytes(b"x" * 8)

        result = LargeFilesAnalyzer().analyze(
            self.context,
            argparse.Namespace(scope="target", path=(), min_size=8, limit=10),
        )

        self.assertEqual(result.summary["scope"], "target")
        self.assertEqual(result.findings[0].title, "target:large.bin")

    def test_large_files_can_scan_explicit_path(self):
        extra = self.root / "extra"
        extra.mkdir()
        (extra / "large.bin").write_bytes(b"x" * 8)

        result = LargeFilesAnalyzer().analyze(
            self.context,
            argparse.Namespace(path=extra, min_size=8, limit=10),
        )

        self.assertEqual(result.summary["scope"], "path")
        self.assertEqual(result.summary["paths"], [str(extra)])
        self.assertEqual(result.findings[0].title, "path1:large.bin")

    def test_symlinks_reports_links_and_broken_links(self):
        (self.source / "real.txt").write_text("content")
        try:
            (self.source / "ok-link").symlink_to("real.txt")
            (self.source / "broken-link").symlink_to("missing.txt")
        except OSError as exc:
            self.skipTest(f"symlink unsupported: {exc}")

        result = SymlinksAnalyzer().analyze(
            self.context,
            argparse.Namespace(scope="source", path=(), limit=10, broken_only=False),
        )

        self.assertEqual(result.summary["symlinks"], 2)
        self.assertEqual(result.summary["broken"], 1)
        self.assertEqual(
            [finding.title for finding in result.findings],
            ["source:broken-link", "source:ok-link"],
        )

    def test_symlinks_can_filter_broken_only(self):
        (self.source / "real.txt").write_text("content")
        try:
            (self.source / "ok-link").symlink_to("real.txt")
            (self.source / "broken-link").symlink_to("missing.txt")
        except OSError as exc:
            self.skipTest(f"symlink unsupported: {exc}")

        result = SymlinksAnalyzer().analyze(
            self.context,
            argparse.Namespace(scope="source", path=(), limit=10, broken_only=True),
        )

        self.assertEqual(result.summary["symlinks"], 2)
        self.assertEqual(result.summary["shown"], 1)
        self.assertEqual(result.findings[0].title, "source:broken-link")

    def test_symlinks_can_scan_explicit_path(self):
        extra = self.root / "extra"
        extra.mkdir()
        try:
            (extra / "real.txt").write_text("content")
            (extra / "ok-link").symlink_to("real.txt")
        except OSError as exc:
            self.skipTest(f"symlink unsupported: {exc}")

        result = SymlinksAnalyzer().analyze(
            self.context,
            argparse.Namespace(path=(extra,), limit=10, broken_only=False),
        )

        self.assertEqual(result.summary["scope"], "path")
        self.assertEqual(result.summary["paths"], [str(extra)])
        self.assertEqual(result.findings[0].title, "path1:ok-link")


if __name__ == "__main__":
    unittest.main()
