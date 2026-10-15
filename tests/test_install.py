import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


REPO = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("avito_install", REPO / "install.py")
installer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(installer)


class InstallTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()

    def test_complete_install_and_first_run(self):
        project = self.root / "shopping project"
        destination = installer.install(project)
        self.assertEqual(destination, project / ".agents/skills/avito-search")
        for relative in installer.skill_files():
            self.assertEqual((destination / relative).read_bytes(), (installer.SOURCE / relative).read_bytes())
        env = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1"}
        env.pop("AVITO_HOME", None)
        for command, expected in [("init", None), ("list", []), ("profile", {"categories": {}, "preferences": {}})]:
            result = subprocess.run([sys.executable, str(destination / "scripts/catalog.py"), command], cwd=project, env=env, text=True, capture_output=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            payload = json.loads(result.stdout)
            if expected is not None:
                self.assertEqual(payload, expected)
        self.assertTrue((project / ".avito-search/data/catalog.sqlite").exists())
        self.assertFalse((destination / "data").exists())

    def test_idempotent_install_and_refuse_changed_skill(self):
        project = self.root / "project"
        destination = installer.install(project)
        self.assertEqual(installer.install(project), destination)
        marker = destination / "SKILL.md"
        marker.write_text("User's existing skill\n", encoding="utf-8")
        with self.assertRaises(ValueError):
            installer.install(project)
        self.assertEqual(marker.read_text(), "User's existing skill\n")

    def test_claude_target(self):
        project = self.root / "project"
        self.assertEqual(installer.install(project, "claude"), project / ".claude/skills/avito-search")
        self.assertFalse((project / ".agents").exists())

    def test_destination_symlink_escape_is_rejected(self):
        project, elsewhere = self.root / "project", self.root / "elsewhere"
        project.mkdir()
        elsewhere.mkdir()
        (project / ".agents").symlink_to(elsewhere, target_is_directory=True)
        with self.assertRaises(ValueError):
            installer.install(project)
        self.assertEqual(list(elsewhere.iterdir()), [])


if __name__ == "__main__":
    unittest.main()
