import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from backup_sync.cli import main


class CliTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.source = self.root / "source"
        self.target = self.root / "target"
        self.source.mkdir()
        self.target.mkdir()
        self.config = self.root / "backup.toml"
        self.config.write_text(
            "\n".join(
                [
                    "[paths]",
                    f'source = "{self.source}"',
                    f'target = "{self.target}"',
                    "[sync]",
                    'verify = "hash"',
                    "retry_max = 0",
                    "retry_delay = 0",
                ]
            )
        )

    def tearDown(self):
        self.temp.cleanup()

    def test_sync_yes_writes_report_and_successful_checkpoint(self):
        (self.source / "file.txt").write_text("content")
        code = main(["sync", "--config", str(self.config), "--yes"])
        self.assertEqual(code, 0)
        reports = list((self.root / ".backup-sync/reports").glob("*.json"))
        states = list((self.root / ".backup-sync/state").glob("*.json"))
        self.assertEqual(len(reports), 1)
        self.assertEqual(len(states), 1)
        self.assertEqual(json.loads(reports[0].read_text())["status"], "success")
        self.assertEqual(json.loads(states[0].read_text())["status"], "success")

    def test_partial_failure_can_resume_with_fresh_plan(self):
        (self.source / "file.txt").write_text("content")
        with patch("backup_sync.sync.operations.verify_copy", side_effect=OSError("simulated")):
            first_code = main(["sync", "--config", str(self.config), "--yes"])
        self.assertEqual(first_code, 1)
        state_path = next((self.root / ".backup-sync/state").glob("*.json"))
        run_id = state_path.stem
        self.assertEqual(json.loads(state_path.read_text())["status"], "partial_failure")

        second_code = main(
            [
                "resume",
                run_id,
                "--config",
                str(self.config),
                "--yes",
            ]
        )
        self.assertEqual(second_code, 0)
        self.assertEqual((self.target / "file.txt").read_text(), "content")
        self.assertEqual(json.loads(state_path.read_text())["status"], "success")

    def test_plan_never_modifies_target(self):
        (self.source / "file.txt").write_text("content")
        code = main(["plan", "--config", str(self.config)])
        self.assertEqual(code, 0)
        self.assertFalse((self.target / "file.txt").exists())
        self.assertTrue((self.root / ".backup-sync/fingerprints.sqlite3").is_file())
        self.assertFalse((self.root / ".backup-sync/state").exists())
        self.assertFalse((self.root / ".backup-sync/reports").exists())

    def test_plan_supports_missing_target_without_creating_it(self):
        self.target.rmdir()
        (self.source / "file.txt").write_text("content")
        code = main(["plan", "--config", str(self.config)])
        self.assertEqual(code, 0)
        self.assertFalse(self.target.exists())

    def test_sync_requires_yes_in_noninteractive_environment(self):
        (self.source / "file.txt").write_text("content")
        with patch("backup_sync.cli.sys.stdin.isatty", return_value=False):
            code = main(["sync", "--config", str(self.config)])
        self.assertEqual(code, 0)
        self.assertFalse((self.target / "file.txt").exists())

    def test_sync_accepts_explicit_confirmation(self):
        (self.source / "file.txt").write_text("content")
        with (
            patch("backup_sync.cli.sys.stdin.isatty", return_value=True),
            patch("builtins.input", return_value="yes"),
        ):
            code = main(["sync", "--config", str(self.config)])
        self.assertEqual(code, 0)
        self.assertEqual((self.target / "file.txt").read_text(), "content")

    def test_runs_list_and_show(self):
        (self.source / "file.txt").write_text("content")
        self.assertEqual(main(["sync", "--config", str(self.config), "--yes"]), 0)
        state_path = next((self.root / ".backup-sync/state").glob("*.json"))
        run_id = state_path.stem
        self.assertEqual(main(["runs", "list", "--config", str(self.config)]), 0)
        self.assertEqual(main(["runs", "show", run_id, "--config", str(self.config)]), 0)

    def test_analyze_small_files_command(self):
        (self.source / "tiny.txt").write_bytes(b"x")
        code = main(
            [
                "analyze",
                "small-files",
                "--config",
                str(self.config),
                "--size",
                "10",
                "--count",
                "1",
            ]
        )
        self.assertEqual(code, 0)

    def test_analyze_ignored_command(self):
        self.config.write_text(
            "\n".join(
                [
                    "[paths]",
                    f'source = "{self.source}"',
                    f'target = "{self.target}"',
                    "[ignore]",
                    'patterns = ["*.tmp"]',
                ]
            )
        )
        (self.source / "scratch.tmp").write_text("tmp")
        code = main(["analyze", "ignored", "--config", str(self.config)])
        self.assertEqual(code, 0)
        self.assertFalse(any(self.target.iterdir()))

    def test_analyze_duplicates_command(self):
        (self.source / "a.txt").write_text("same")
        (self.source / "b.txt").write_text("same")
        code = main(["analyze", "duplicates", "--config", str(self.config), "--yes"])
        self.assertEqual(code, 0)

    def test_analyze_duplicates_target_scope_command(self):
        (self.target / "a.txt").write_text("same")
        (self.target / "b.txt").write_text("same")
        code = main(
            [
                "analyze",
                "duplicates",
                "--config",
                str(self.config),
                "--scope",
                "target",
                "--yes",
            ]
        )
        self.assertEqual(code, 0)

    def test_analyze_integrity_estimate_command(self):
        (self.source / "file.txt").write_text("content")
        (self.target / "file.txt").write_text("content")
        code = main(["analyze", "integrity", "--config", str(self.config), "--estimate-only"])
        self.assertEqual(code, 0)

    def test_config_get_set_and_validate_commands(self):
        self.assertEqual(main(["config", "get", "paths.source", "--config", str(self.config)]), 0)
        self.assertEqual(
            main(["config", "set", "scan.compare", "hash", "--config", str(self.config)]),
            0,
        )
        self.assertEqual(main(["config", "validate", "--config", str(self.config)]), 0)


if __name__ == "__main__":
    unittest.main()
