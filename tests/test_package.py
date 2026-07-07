import tomllib
import unittest
from pathlib import Path

from backup_sync import __version__


class PackageMetadataTests(unittest.TestCase):
    def test_module_and_project_versions_match(self):
        project_root = Path(__file__).resolve().parents[1]
        with (project_root / "pyproject.toml").open("rb") as stream:
            project = tomllib.load(stream)
        self.assertEqual(__version__, project["project"]["version"])


if __name__ == "__main__":
    unittest.main()
