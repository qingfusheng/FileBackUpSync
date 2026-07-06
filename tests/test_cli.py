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

    def test_apply_writes_report_and_successful_checkpoint(self):
        (self.source / "file.txt").write_text("content")
        code = main(["--config", str(self.config), "--apply"])
        self.assertEqual(code, 0)
        reports = list((self.root / ".backup-sync/reports").glob("*.json"))
        states = list((self.root / ".backup-sync/state").glob("*.json"))
        self.assertEqual(len(reports), 1)
        self.assertEqual(len(states), 1)
        self.assertEqual(json.loads(reports[0].read_text())["status"], "success")
        self.assertEqual(json.loads(states[0].read_text())["status"], "success")

    def test_partial_failure_can_resume_with_fresh_plan(self):
        (self.source / "file.txt").write_text("content")
        with patch("backup_sync.core._verify_copy", side_effect=OSError("simulated")):
            first_code = main(["--config", str(self.config), "--apply"])
        self.assertEqual(first_code, 1)
        state_path = next((self.root / ".backup-sync/state").glob("*.json"))
        run_id = state_path.stem
        self.assertEqual(json.loads(state_path.read_text())["status"], "partial_failure")

        second_code = main([
            "--config", str(self.config), "--apply", "--resume", run_id,
        ])
        self.assertEqual(second_code, 0)
        self.assertEqual((self.target / "file.txt").read_text(), "content")
        self.assertEqual(json.loads(state_path.read_text())["status"], "success")

    def test_resume_requires_apply(self):
        code = main(["--config", str(self.config), "--resume", "missing"])
        self.assertEqual(code, 2)


if __name__ == "__main__":
    unittest.main()
