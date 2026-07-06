import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from backup_sync.core import ActionKind, VerifyMode, build_plan, execute, scan


class SyncTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.source = root / "source"
        self.target = root / "target"
        self.recycle = root / "recycle"
        self.source.mkdir()
        self.target.mkdir()

    def tearDown(self):
        self.temp.cleanup()

    def snapshots(self):
        return scan(self.source), scan(self.target)

    def test_modified_file_is_updated_and_old_version_archived(self):
        (self.source / "same-name.txt").write_text("new content")
        (self.target / "same-name.txt").write_text("old content")
        source, target = self.snapshots()
        plan = build_plan(source, target)
        self.assertEqual(plan.count(ActionKind.UPDATE), 1)

        execute(plan, source, target, self.recycle)
        self.assertEqual((self.target / "same-name.txt").read_text(), "new content")
        self.assertEqual((self.recycle / "same-name.txt").read_text(), "old content")

    def test_identical_content_at_new_path_is_renamed(self):
        (self.source / "new").mkdir()
        (self.target / "old").mkdir()
        (self.source / "new/file.bin").write_bytes(b"payload")
        (self.target / "old/file.bin").write_bytes(b"payload")
        source, target = self.snapshots()
        plan = build_plan(source, target)
        self.assertEqual(plan.count(ActionKind.RENAME), 1)
        self.assertEqual(plan.count(ActionKind.COPY), 0)

        execute(plan, source, target, self.recycle)
        self.assertEqual((self.target / "new/file.bin").read_bytes(), b"payload")
        self.assertFalse((self.target / "old").exists())

    def test_removed_file_and_old_empty_directories_are_cleaned(self):
        old = self.target / "a/b/c"
        old.mkdir(parents=True)
        (old / "gone.txt").write_text("gone")
        source, target = self.snapshots()
        plan = build_plan(source, target)
        execute(plan, source, target, self.recycle)
        self.assertFalse((self.target / "a").exists())
        self.assertEqual((self.recycle / "a/b/c/gone.txt").read_text(), "gone")

    def test_empty_source_directories_are_created(self):
        (self.source / "empty/child").mkdir(parents=True)
        source, target = self.snapshots()
        plan = build_plan(source, target)
        execute(plan, source, target, self.recycle)
        self.assertTrue((self.target / "empty/child").is_dir())

    def test_target_empty_directory_can_be_replaced_by_source_file(self):
        (self.target / "item").mkdir()
        (self.source / "item").write_text("now a file")
        source, target = self.snapshots()
        execute(build_plan(source, target), source, target, self.recycle)
        self.assertEqual((self.target / "item").read_text(), "now a file")

    def test_target_file_can_be_replaced_by_source_directory(self):
        (self.target / "item").write_text("was a file")
        (self.source / "item").mkdir()
        (self.source / "item/child").write_text("child")
        source, target = self.snapshots()
        execute(build_plan(source, target), source, target, self.recycle)
        self.assertEqual((self.target / "item/child").read_text(), "child")
        self.assertEqual((self.recycle / "item").read_text(), "was a file")

    def test_small_file_hotspot_and_ignore(self):
        cache = self.source / "cache"
        cache.mkdir()
        for index in range(3):
            (cache / f"{index}.tmp").write_bytes(b"x")
        snapshot = scan(self.source, ignore=("1.tmp",), small_file_size=10)
        self.assertEqual(snapshot.small_file_parents[Path("cache")], 2)
        self.assertNotIn(Path("cache/1.tmp"), snapshot.files)

    def test_failed_update_keeps_existing_backup_and_removes_temporary_file(self):
        (self.source / "important.txt").write_text("new")
        (self.target / "important.txt").write_text("old")
        source, target = self.snapshots()
        with patch("backup_sync.core._verify_copy", side_effect=OSError("bad copy")):
            result = execute(
                build_plan(source, target),
                source,
                target,
                self.recycle,
                retry_max=0,
            )
        self.assertEqual(result.failed, 1)
        self.assertEqual((self.target / "important.txt").read_text(), "old")
        self.assertEqual(list(self.target.glob(".*.backup-sync-*.tmp")), [])
        self.assertFalse((self.recycle / "important.txt").exists())

    def test_copy_retries_and_reports_attempt_count(self):
        (self.source / "file.txt").write_text("content")
        source, target = self.snapshots()
        import backup_sync.core as core

        real_copy = core.shutil.copy2
        calls = 0

        def flaky_copy(*args, **kwargs):
            nonlocal calls
            calls += 1
            if calls == 1:
                raise OSError("temporary failure")
            return real_copy(*args, **kwargs)

        with (
            patch("backup_sync.core.shutil.copy2", side_effect=flaky_copy),
            patch("backup_sync.core.time.sleep"),
        ):
            result = execute(
                build_plan(source, target),
                source,
                target,
                self.recycle,
                verify=VerifyMode.HASH,
                retry_max=2,
            )
        self.assertEqual(result.failed, 0)
        copy_result = next(item for item in result.results if item.action.kind == ActionKind.COPY)
        self.assertEqual(copy_result.attempts, 2)
        self.assertEqual((self.target / "file.txt").read_text(), "content")

    def test_atomic_replace_failure_keeps_existing_backup(self):
        (self.source / "important.txt").write_text("new")
        (self.target / "important.txt").write_text("old")
        source, target = self.snapshots()
        with patch("backup_sync.core.os.replace", side_effect=OSError("replace failed")):
            result = execute(
                build_plan(source, target),
                source,
                target,
                self.recycle,
                retry_max=0,
            )
        self.assertEqual(result.failed, 1)
        self.assertEqual((self.target / "important.txt").read_text(), "old")
        self.assertEqual((self.recycle / "important.txt").read_text(), "old")
        self.assertEqual(list(self.target.glob(".*.backup-sync-*.tmp")), [])

    def test_scan_and_plan_publish_progress(self):
        (self.source / "same.txt").write_text("same")
        (self.target / "same.txt").write_text("same")
        (self.source / "new.txt").write_text("moved")
        (self.target / "old.txt").write_text("moved")
        scanned: list[Path] = []
        source = scan(self.source, progress_callback=scanned.append)
        target = scan(self.target)
        events: list[tuple[str, int, int, Path | None]] = []
        plan = build_plan(source, target, progress_callback=lambda *event: events.append(event))

        self.assertEqual(set(scanned), {Path("same.txt"), Path("new.txt")})
        self.assertEqual(plan.count(ActionKind.RENAME), 1)
        self.assertIn(("比较同路径文件", 1, 1, None), events)
        self.assertIn(("计算 rename 指纹", 2, 2, None), events)

    def test_execute_publishes_started_and_finished_progress(self):
        (self.source / "file.txt").write_text("content")
        source, target = self.snapshots()
        started = []
        finished = []
        execute(
            build_plan(source, target),
            source,
            target,
            self.recycle,
            action_started_callback=started.append,
            progress_callback=finished.append,
        )
        self.assertEqual([item.kind for item in started], [ActionKind.COPY])
        self.assertTrue(finished[0].success)


if __name__ == "__main__":
    unittest.main()
