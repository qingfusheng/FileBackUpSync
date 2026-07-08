import os
import tempfile
import unittest
from pathlib import Path

from backup_sync.storage.fingerprint import SAMPLE_SIZE, FingerprintEngine
from backup_sync.sync import ActionKind, build_plan, scan


class FingerprintTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.source = self.root / "source"
        self.target = self.root / "target"
        self.source.mkdir()
        self.target.mkdir()
        self.cache = self.root / "fingerprints.sqlite3"

    def tearDown(self):
        self.temp.cleanup()

    def test_strong_fingerprint_is_reused_across_runs(self):
        path = self.source / "file.bin"
        path.write_bytes(b"payload")
        snapshot = scan(self.source)
        info = snapshot.files[Path("file.bin")]
        with FingerprintEngine(self.cache) as first:
            expected = first.strong(snapshot.root, info.path, info)
            self.assertEqual(first.stats.strong_computed, 1)
        with FingerprintEngine(self.cache) as second:
            actual = second.strong(snapshot.root, info.path, info)
            self.assertEqual(actual, expected)
            self.assertEqual(second.stats.cache_hits, 1)
            self.assertEqual(second.stats.strong_computed, 0)

    def test_metadata_change_invalidates_cached_fingerprint(self):
        path = self.source / "file.bin"
        path.write_bytes(b"AAAA")
        first_snapshot = scan(self.source)
        first_info = first_snapshot.files[Path("file.bin")]
        with FingerprintEngine(self.cache) as engine:
            old = engine.strong(first_snapshot.root, first_info.path, first_info)
        path.write_bytes(b"BBBB")
        stat = path.stat()
        os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns + 1_000_000))
        second_snapshot = scan(self.source)
        second_info = second_snapshot.files[Path("file.bin")]
        with FingerprintEngine(self.cache) as engine:
            new = engine.strong(second_snapshot.root, second_info.path, second_info)
            self.assertNotEqual(new, old)
            self.assertEqual(engine.stats.strong_computed, 1)

    def test_quick_fingerprint_reads_only_three_samples_of_large_file(self):
        path = self.source / "large.bin"
        path.write_bytes(b"x" * (SAMPLE_SIZE * 8))
        snapshot = scan(self.source)
        info = snapshot.files[Path("large.bin")]
        with FingerprintEngine() as engine:
            engine.quick(snapshot.root, info.path, info)
            self.assertEqual(engine.stats.bytes_read, SAMPLE_SIZE * 3)

    def test_nonmatching_quick_fingerprints_skip_full_hash(self):
        (self.source / "new.bin").write_bytes(b"A" * 1024)
        (self.target / "old.bin").write_bytes(b"B" * 1024)
        with FingerprintEngine() as engine:
            plan = build_plan(scan(self.source), scan(self.target), fingerprint_engine=engine)
            self.assertEqual(plan.count(ActionKind.RENAME), 0)
            self.assertEqual(engine.stats.quick_computed, 2)
            self.assertEqual(engine.stats.strong_computed, 0)

    def test_small_matching_files_need_no_second_full_read(self):
        (self.source / "new.bin").write_bytes(b"same payload")
        (self.target / "old.bin").write_bytes(b"same payload")
        with FingerprintEngine() as engine:
            plan = build_plan(scan(self.source), scan(self.target), fingerprint_engine=engine)
            self.assertEqual(plan.count(ActionKind.RENAME), 1)
            self.assertEqual(engine.stats.quick_computed, 2)
            self.assertEqual(engine.stats.strong_computed, 0)

    def test_layered_rename_fingerprints_are_persistent(self):
        (self.source / "new.bin").write_bytes(b"same payload")
        (self.target / "old.bin").write_bytes(b"same payload")
        source = scan(self.source)
        target = scan(self.target)
        with FingerprintEngine(self.cache) as engine:
            first = build_plan(source, target, fingerprint_engine=engine)
            self.assertEqual(first.count(ActionKind.RENAME), 1)
            self.assertEqual(engine.stats.quick_computed, 2)
        with FingerprintEngine(self.cache) as engine:
            second = build_plan(source, target, fingerprint_engine=engine)
            self.assertEqual(second.count(ActionKind.RENAME), 1)
            self.assertEqual(engine.stats.cache_hits, 2)
            self.assertEqual(engine.stats.quick_computed, 0)

    def test_quick_collision_is_rejected_by_strong_fingerprint(self):
        size = SAMPLE_SIZE * 8
        source_data = bytearray(b"A" * size)
        target_data = bytearray(source_data)
        target_data[SAMPLE_SIZE * 2] = ord("B")
        (self.source / "new.bin").write_bytes(source_data)
        (self.target / "old.bin").write_bytes(target_data)
        with FingerprintEngine() as engine:
            plan = build_plan(scan(self.source), scan(self.target), fingerprint_engine=engine)
            self.assertEqual(plan.count(ActionKind.RENAME), 0)
            self.assertEqual(engine.stats.strong_computed, 2)

    def test_ctime_change_after_rename_invalidates_hash_safely(self):
        old = self.source / "old.bin"
        old.write_bytes(b"payload")
        first_snapshot = scan(self.source)
        first_info = first_snapshot.files[Path("old.bin")]
        with FingerprintEngine(self.cache) as engine:
            expected = engine.strong(first_snapshot.root, first_info.path, first_info)
        old.rename(self.source / "new.bin")
        second_snapshot = scan(self.source)
        second_info = second_snapshot.files[Path("new.bin")]
        with FingerprintEngine(self.cache) as engine:
            actual = engine.strong(second_snapshot.root, second_info.path, second_info)
            self.assertEqual(actual, expected)
            self.assertEqual(engine.stats.strong_computed, 1)

    def test_hash_compare_forces_full_audit_even_when_cache_exists(self):
        (self.source / "same.bin").write_bytes(b"payload")
        (self.target / "same.bin").write_bytes(b"payload")
        source = scan(self.source)
        target = scan(self.target)
        source_info = source.files[Path("same.bin")]
        target_info = target.files[Path("same.bin")]
        with FingerprintEngine(self.cache) as engine:
            engine.strong(source.root, source_info.path, source_info)
            engine.strong(target.root, target_info.path, target_info)
        with FingerprintEngine(self.cache) as engine:
            plan = build_plan(
                source,
                target,
                compare_mode="hash",
                fingerprint_engine=engine,
            )
            self.assertEqual(plan.unchanged, 1)
            self.assertEqual(engine.stats.cache_hits, 0)
            self.assertEqual(engine.stats.strong_computed, 2)

    def test_corrupt_database_falls_back_to_memory(self):
        self.cache.write_bytes(b"not a sqlite database")
        path = self.source / "file.bin"
        path.write_bytes(b"payload")
        snapshot = scan(self.source)
        info = snapshot.files[Path("file.bin")]
        with FingerprintEngine(self.cache) as engine:
            digest = engine.strong(snapshot.root, info.path, info)
        self.assertTrue(digest)


if __name__ == "__main__":
    unittest.main()
