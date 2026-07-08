import os
import stat
import sys
import tempfile
import unittest
from pathlib import Path

from backup_sync.storage.fileops import safe_replace, safe_unlink


@unittest.skipUnless(sys.platform == "darwin", "macOS immutable flags only")
class MacOSFileProtectionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)

    def tearDown(self) -> None:
        for path in self.root.iterdir():
            if path.exists():
                os.chflags(path, path.stat().st_flags & ~stat.UF_IMMUTABLE)
        self.temp.cleanup()

    def test_safe_unlink_clears_user_immutable_flag(self) -> None:
        locked = self.root / "locked.txt"
        locked.write_text("locked")
        os.chflags(locked, locked.stat().st_flags | stat.UF_IMMUTABLE)

        safe_unlink(locked)

        self.assertFalse(locked.exists())

    def test_safe_replace_clears_destination_immutable_flag(self) -> None:
        source = self.root / "temporary.txt"
        destination = self.root / "locked.txt"
        source.write_text("new")
        destination.write_text("old")
        os.chflags(destination, destination.stat().st_flags | stat.UF_IMMUTABLE)

        safe_replace(source, destination)

        self.assertEqual(destination.read_text(), "new")
