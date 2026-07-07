import tempfile
import unittest
from pathlib import Path

from backup_sync.config_manager import (
    flatten_document,
    get_value,
    read_document,
    update_file,
    validate_file,
)


class ConfigManagerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.source = self.root / "source"
        self.target = self.root / "target"
        self.source.mkdir()
        self.target.mkdir()
        self.path = self.root / "backup.toml"
        self.path.write_text(
            "# keep this comment\n"
            "[paths]\n"
            f'source = "{self.source}"\n'
            f'target = "{self.target}"\n'
            '\n[scan]\ncompare = "smart"\n'
        )

    def tearDown(self):
        self.temp.cleanup()

    def test_get_and_flatten(self):
        document = read_document(self.path)
        self.assertEqual(get_value(document, "scan.compare"), "smart")
        self.assertEqual(flatten_document(document)["paths.source"], str(self.source))

    def test_set_preserves_comments_and_validates(self):
        checks = update_file(self.path, "scan.compare", "hash")
        self.assertFalse(any(check.level == "error" for check in checks))
        self.assertEqual(get_value(read_document(self.path), "scan.compare"), "hash")
        self.assertIn("# keep this comment", self.path.read_text())

    def test_invalid_source_is_not_written(self):
        original = self.path.read_text()
        with self.assertRaises(ValueError):
            update_file(self.path, "paths.source", str(self.root / "missing"))
        self.assertEqual(self.path.read_text(), original)

    def test_trailing_space_in_path_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "空格"):
            update_file(self.path, "paths.source", f"{self.source} ")

    def test_validate_file_reports_healthy_paths(self):
        _, checks = validate_file(self.path)
        self.assertFalse(any(check.level == "error" for check in checks))


if __name__ == "__main__":
    unittest.main()
