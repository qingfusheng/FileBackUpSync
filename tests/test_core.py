import tempfile
import unittest
from pathlib import Path

from backup_sync.core import ActionKind, build_plan, execute, scan


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


if __name__ == "__main__":
    unittest.main()
