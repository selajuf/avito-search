import base64
import copy
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch


SCRIPTS = Path(__file__).resolve().parents[1] / "skills/avito-search/scripts"
spec = importlib.util.spec_from_file_location("avito_catalog_test", SCRIPTS / "catalog.py")
catalog = importlib.util.module_from_spec(spec)
spec.loader.exec_module(catalog)
PNG = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aX9sAAAAASUVORK5CYII=")


class CatalogTests(unittest.TestCase):
    def setUp(self):
        self.scratch = tempfile.TemporaryDirectory()
        self.addCleanup(self.scratch.cleanup)
        self.work = Path(self.scratch.name)
        self.home = self.work / "home"
        self.incoming = self.work / "incoming"
        self.incoming.mkdir()
        (self.incoming / "item.png").write_bytes(PNG)
        self.env = {**os.environ, "AVITO_HOME": str(self.home), "PYTHONDONTWRITEBYTECODE": "1"}

    def cli(self, *args, success=True, env=None):
        result = subprocess.run([sys.executable, str(SCRIPTS / "catalog.py"), *map(str, args)], env=env or self.env, cwd=self.work, capture_output=True, text=True)
        if success:
            self.assertEqual(result.returncode, 0, result.stderr)
            return json.loads(result.stdout)
        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn("Traceback", result.stderr)
        return result

    def item(self, item_id="100001", **values):
        item = {
            "id": item_id,
            "title": "Вещь из тестовой подборки",
            "url": f"https://www.avito.ru/test/odezhda_obuv_aksessuary/item_{item_id}",
            "photos": [{"path": "item.png", "source": "original"}],
            "availability": {"status": "available", "checked_at": datetime.now(timezone.utc).isoformat(), "evidence": "Тестовая страница"},
        }
        item.update(values)
        return item

    def batch(self, items=None, **values):
        path = self.incoming / "batch.json"
        path.write_text(json.dumps({"title": "Подборка", "items": items or [self.item()], **values}), encoding="utf-8")
        return path

    def test_init_default_home_and_neutral_profile(self):
        self.cli("init")
        self.assertTrue((self.home / "data/catalog.sqlite").is_file())
        self.assertEqual(self.cli("list"), [])
        self.assertEqual(self.cli("profile"), {"categories": {}, "preferences": {}})
        env = dict(self.env)
        env.pop("AVITO_HOME")
        self.cli("init", env=env)
        self.assertTrue((self.work / ".avito-search/preferences.json").exists())

    def test_profile_save_and_preserve_on_init(self):
        profile = self.work / "custom.json"
        data = {"categories": {"my-custom-category": {"notes": "Любые личные критерии"}}, "preferences": {"budget": 1000}}
        profile.write_text(json.dumps(data))
        self.assertEqual(self.cli("profile", profile), data)
        self.cli("init")
        self.assertEqual(self.cli("profile"), data)
        profile.write_text('{"categories": []}')
        self.cli("profile", profile, success=False)
        self.assertEqual(self.cli("profile"), data)

    def test_import_numbering_feedback_and_rerender(self):
        path = self.batch([self.item(number=99), self.item("100002", number=99)])
        self.assertEqual(self.cli("import", path, "--category", "custom", "--batch", "first"), {"imported": 2})
        self.assertEqual([row["number"] for row in self.cli("list")], [1, 2])
        self.assertEqual(self.cli("show", "1")["id"], "100001")
        self.assertEqual(self.cli("show", "100002")["number"], 2)
        self.assertEqual(self.cli("new", "100001", "100003", "100003"), {"new_ids": ["100003"]})
        page = Path(self.cli("render", "first")["gallery"])
        self.assertIn('id="item-1"', page.read_text())
        self.assertIn('<base href="versions/', page.read_text())
        self.assertIsNotNone(self.cli("show", "1")["shown_at"])
        result = self.cli("feedback", "1", "rejected", "Не подходит")
        self.assertEqual(result["decision"], "rejected")
        self.assertNotIn('id="item-1"', page.read_text())
        path = self.batch([self.item(decision="kept"), self.item("100003")])
        self.cli("import", path, "--category", "custom", "--batch", "second")
        self.assertEqual(self.cli("show", "1")["decision"], "rejected")
        self.assertEqual(self.cli("show", "100003")["number"], 3)
        second = Path(self.cli("render", "second")["gallery"])
        self.assertNotIn('id="item-1"', second.read_text())
        self.cli("show", "999", success=False)

    def test_html_escapes_untrusted_content(self):
        marker = '<script>alert("example")</script>'
        path = self.batch([self.item(title=marker, description=marker, seller=marker, photos=[{"path": "item.png", "source": "original", "caption": marker}])], title=marker)
        self.cli("import", path, "--category", "custom", "--batch", "escape")
        page = Path(self.cli("render", "escape")["gallery"]).read_text()
        self.assertNotIn(marker, page)
        self.assertIn("&lt;script&gt;", page)

    def test_invalid_urls_and_mismatched_ids_are_rejected(self):
        valid = self.item()["url"]
        for url in [valid.replace("https:", "http:"), valid.replace("www.avito.ru", "example.com"), valid.replace("item_100001", "item_100002"), valid.replace("www.avito.ru", "user:pass@www.avito.ru"), valid.replace("www.avito.ru", "www.avito.ru:443"), "https://www.avito.ru/user/100001", valid + "\n"]:
            with self.subTest(url=url):
                self.cli("import", self.batch([self.item(url=url)]), "--category", "custom", success=False)
        self.cli("import", self.batch([self.item(url=valid + "?tracking=example#fragment")]), "--category", "custom")
        self.assertEqual(self.cli("show", "1")["payload"]["url"], valid)

    def test_photo_traversal_and_symlink_escape(self):
        outside = self.work / "outside.png"
        outside.write_bytes(PNG)
        link = self.incoming / "escape.png"
        link.symlink_to(outside)
        for path in ["../outside.png", str(outside), "escape.png"]:
            with self.subTest(path=path):
                self.cli("import", self.batch([self.item(photos=[{"path": path}])]), "--category", "custom", success=False)
                self.assertFalse((self.home / "media").exists())
        (self.home / "media").mkdir(parents=True)
        inside = self.home / "media/allowed.png"
        inside.write_bytes(PNG)
        self.cli("import", self.batch([self.item(photos=[{"path": str(inside)}])]), "--category", "custom")
        self.assertEqual(self.cli("show", "1")["number"], 1)

    def test_renderer_rejects_traversal_and_mismatch_directly(self):
        outside = self.work / "outside.png"
        outside.write_bytes(PNG)
        for item in [self.item(number=1, photos=[{"path": "../outside.png", "source": "original"}]), self.item(number=1, url=self.item("100002")["url"])]:
            path = self.batch([item])
            out = self.work / "bad-gallery"
            result = subprocess.run([sys.executable, str(SCRIPTS / "render_gallery.py"), str(path), "--output-dir", str(out)], env=self.env, capture_output=True, text=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertFalse(out.exists())

    def test_stale_availability_and_atomic_failed_render(self):
        item = self.item()
        path = self.batch([item])
        self.cli("import", path, "--category", "custom", "--batch", "atomic")
        page = Path(self.cli("render", "atomic")["gallery"])
        previous = page.read_bytes()
        with patch.object(catalog.subprocess, "run", side_effect=subprocess.CalledProcessError(1, ["renderer"])):
            with self.assertRaises(subprocess.CalledProcessError):
                catalog.render_batch("atomic", self.home)
        self.assertEqual(page.read_bytes(), previous)
        stale = copy.deepcopy(item)
        stale["availability"]["checked_at"] = "2000-01-01T00:00:00Z"
        self.cli("import", self.batch([stale]), "--category", "custom", "--batch", "atomic")
        self.cli("render", "atomic")
        self.assertNotIn('id="item-1"', page.read_text())
        self.assertIn("доступность нужно проверить", page.read_text())

    def test_blocked_seller_is_not_reintroduced(self):
        seller = "https://www.avito.ru/user/example-public-seller"
        path = self.batch([self.item(seller_url=seller), self.item("100002", seller_url=seller)])
        self.cli("import", path, "--category", "custom", "--batch", "seller")
        self.cli("feedback", "1", "rejected", "Не подходит продавец", "--block-seller")
        page = Path(self.cli("render", "seller")["gallery"]).read_text()
        self.assertNotIn('id="item-1"', page)
        self.assertNotIn('id="item-2"', page)


if __name__ == "__main__":
    unittest.main()
