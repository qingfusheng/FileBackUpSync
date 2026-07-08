import json
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

from backup_sync.runs.reporting import build_report, write_json_atomic
from backup_sync.sync import VerifyMode, build_plan, execute, scan


class ReportingTests(unittest.TestCase):
    def test_report_contains_run_summary_and_actions(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source_dir = root / "source"
            target_dir = root / "target"
            source_dir.mkdir()
            target_dir.mkdir()
            (source_dir / "new.txt").write_text("content")
            source, target = scan(source_dir), scan(target_dir)
            plan = build_plan(source, target)
            started = datetime.now().astimezone()
            result = execute(plan, source, target, root / "recycle")
            finished = datetime.now().astimezone()
            report = build_report(
                "run-123",
                started,
                finished,
                source,
                target,
                plan,
                result,
                VerifyMode.HASH,
                root / "recycle",
            )
            output = root / "reports/run-123.json"
            write_json_atomic(output, report)

            saved = json.loads(output.read_text())
            self.assertEqual(saved["run_id"], "run-123")
            self.assertEqual(saved["status"], "success")
            self.assertEqual(saved["compare"], "smart")
            self.assertEqual(saved["fingerprints"]["algorithm"], "blake3")
            self.assertEqual(saved["summary"]["succeeded"], 1)
            self.assertEqual(saved["actions"][0]["kind"], "copy")
