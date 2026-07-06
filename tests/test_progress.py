import os
import unittest
from unittest.mock import patch

from backup_sync.progress import ProgressDisplay


class ProgressDisplayTests(unittest.TestCase):
    def test_always_and_never_override_terminal_detection(self):
        self.assertTrue(ProgressDisplay("always").enabled)
        self.assertFalse(ProgressDisplay("never").enabled)

    def test_auto_detects_pycharm_console(self):
        with (
            patch.dict(os.environ, {"PYCHARM_HOSTED": "1"}),
            patch("backup_sync.progress.sys.stderr.isatty", return_value=False),
        ):
            self.assertTrue(ProgressDisplay("auto").enabled)

    def test_invalid_mode_is_rejected(self):
        with self.assertRaises(ValueError):
            ProgressDisplay("sometimes")


if __name__ == "__main__":
    unittest.main()
