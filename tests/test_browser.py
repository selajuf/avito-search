"""Offline checks: no personal profile or network calls."""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

SCRIPTS = Path(__file__).resolve().parents[1] / "skills/avito-search/scripts"
sys.path.insert(0, str(SCRIPTS))
import browser
import catalog
import setup_browser


class BrowserTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name).resolve()
        self.root_patch = patch.object(browser, "ROOT", self.root)
        self.root_patch.start()

    def tearDown(self):
        self.root_patch.stop()
        self.temp.cleanup()

    def test_public_urls_reject_credentials_external_actions_and_traversal(self):
        accepted = "https://www.avito.ru/all/odezhda_obuv_aksessuary?q=jeans"
        self.assertEqual(browser.public_url(accepted), accepted)
        for url in ("https://avito.ru.evil.test/all", "https://u:p@avito.ru/all", "http://avito.ru/all",
                    "https://avito.ru:443/all", "https://avito.ru/profile", "https://avito.ru/messages",
                    "https://avito.ru/all/odezhda_obuv_aksessuary/../profile", "file:///tmp/index.html",
                    "https://avito.ru/all%2F..%2Fmessages", "https://avito.ru\\@evil.test/all"):
            with self.subTest(url=url), self.assertRaises(ValueError):
                browser.public_url(url)

    def test_media_paths_reject_traversal_and_symlinks(self):
        media = self.root / "media"
        media.mkdir()
        self.assertEqual(browser.media_path("safe.png"), media / "safe.png")
        outside = self.root / "outside"
        outside.mkdir()
        (media / "link").symlink_to(outside, target_is_directory=True)
        for path in ("../outside/leak.png", "link/leak.png", "not-a-picture.json"):
            with self.subTest(path=path), self.assertRaises(ValueError):
                browser.media_path(path)
        media.rename(self.root / "old-media")
        media.symlink_to(outside, target_is_directory=True)
        with self.assertRaises(ValueError):
            browser.media_path("leak.png")

    def test_only_local_gallery_is_accepted(self):
        gallery = self.root / "galleries/demo/index.html"
        gallery.parent.mkdir(parents=True)
        gallery.write_text("<p>local</p>")
        self.assertEqual(browser.public_url(gallery.as_uri()), gallery.as_uri())
        other = self.root / "other/index.html"
        other.parent.mkdir()
        other.write_text("private")
        (self.root / "galleries/escape").symlink_to(other.parent, target_is_directory=True)
        with self.assertRaises(ValueError):
            browser.public_url((self.root / "galleries/escape/index.html").as_uri())

    def test_no_raw_secrets_commands_or_file_flags(self):
        b = object.__new__(browser.Browser)
        b.tab = 2
        with patch.object(b, "current_url", return_value="https://www.avito.ru/all"), patch.object(b, "_run") as execute:
            for name, args in (("js", ["document.cookie"]), ("cookies", []), ("storage", []),
                               ("restart", []), ("screenshot", ["../../leak.png"]),
                               ("snapshot", ["-a", "-o", "/tmp/leak.png"]),
                               ("text", ["--tab-id", "4"])):
                with self.subTest(name=name), self.assertRaises(ValueError):
                    b.run(name, args)
            execute.assert_not_called()

    def test_navigation_click_opens_validated_link_without_click_handler(self):
        b = object.__new__(browser.Browser)
        b.tab = 3
        with patch.object(b, "current_url", return_value="https://www.avito.ru/all"), patch.object(b, "no_captcha"), \
             patch.object(b, "_run", side_effect=[json.dumps({"href": "/all/odezhda_obuv_aksessuary?q=jeans"}), "loaded"]) as execute:
            self.assertEqual(b.run("click", ["@e3"]), "loaded")
            self.assertEqual(execute.call_args_list[1].args, ("goto", "https://www.avito.ru/all/odezhda_obuv_aksessuary?q=jeans"))

    def test_captcha_never_clicks(self):
        b = object.__new__(browser.Browser)
        b.tab = 2
        with patch.object(b, "current_url", return_value="https://www.avito.ru/all"), \
             patch.object(b, "_run", return_value="true") as execute:
            with self.assertRaisesRegex(RuntimeError, "CAPTCHA"):
                b.run("click", ["@e3"])
            self.assertEqual(execute.call_count, 1)
            self.assertEqual(execute.call_args.args[0], "js")

    def test_stale_tab_is_rejected(self):
        with patch.object(browser, "healthy"), patch.object(browser, "tabs", return_value=[{"id": 3}]):
            with self.assertRaisesRegex(ValueError, "Вкладка не найдена"):
                browser.Browser(2)

    def test_missing_daemon_never_invokes_a_process(self):
        with patch.object(browser, "STATE", self.root / "missing.json"), patch.object(subprocess, "run") as run:
            with self.assertRaisesRegex(RuntimeError, "не запущен"):
                browser.command("status")
            run.assert_not_called()

    def test_headless_daemon_is_rejected(self):
        with patch.object(browser, "command", return_value="Status: healthy\nMode: launched"):
            with self.assertRaisesRegex(RuntimeError, "headed"):
                browser.healthy()

    def test_child_environment_drops_secrets_and_keeps_home_unchanged(self):
        with patch.dict(os.environ, {"HOME": "/existing-home", "OPENAI_API_KEY": "dummy", "BROWSE_STATE_FILE": "/foreign/state", "GSTACK_TELEMETRY_OFF": "0"}):
            env = setup_browser.child_env()
        self.assertEqual(env["HOME"], "/existing-home")
        self.assertNotIn("OPENAI_API_KEY", env)
        self.assertEqual(env["GSTACK_TELEMETRY_OFF"], "1")
        self.assertEqual(env["BROWSE_NO_AUTOSTART"], "1")
        self.assertNotEqual(env["BROWSE_STATE_FILE"], "/foreign/state")

    def test_patch_requires_pinned_file_hash(self):
        source = self.root / "browser-manager.ts"
        source.write_text("untrusted runtime")
        before = source.read_bytes()
        with self.assertRaisesRegex(RuntimeError, "unverified runtime patch"):
            setup_browser.isolate_extension(source)
        self.assertEqual(source.read_bytes(), before)

    def test_capture_paths_can_be_imported_from_another_batch_directory(self):
        b = object.__new__(browser.Browser)
        b.tab = 2

        def execute(name, *args):
            if name == "screenshot":
                Path(args[-1]).write_bytes(b"mock photo")
            return "ok"

        with patch.object(b, "inspect", return_value={"photos": [0, 1]}), patch.object(b, "_run", side_effect=execute), \
             patch.object(b, "image_ready", side_effect=[{"source": "photo-1"}, {"source": "photo-2"}]) as ready:
            result = b.capture("1234567890", "#gallery-image")
        self.assertEqual(ready.call_args_list[1].args, ("#gallery-image", "photo-1"))
        self.assertTrue(result["visual_review_required"])
        self.assertEqual(len(result["photos"]), 2)
        for photo in result["photos"]:
            path = Path(photo["path"])
            self.assertTrue(path.is_absolute())
            self.assertTrue(path.is_relative_to(self.root / "media"))
            self.assertTrue(path.is_file())
        incoming = self.root / "incoming"
        incoming.mkdir()
        batch = incoming / "batch.json"
        batch.write_text(json.dumps({"items": [{"id": result["id"], "title": "Local test item",
                                              "url": "https://www.avito.ru/test/odezhda_obuv_aksessuary/item_1234567890",
                                              "photos": result["photos"]}]}))
        self.assertEqual(catalog.import_catalog(batch, "test", root=self.root), 1)
        with catalog.database(self.root) as db:
            saved = json.loads(db.execute("SELECT payload FROM listings").fetchone()[0])
        self.assertEqual(len(saved["photos"]), 2)
        for photo in saved["photos"]:
            self.assertTrue((self.root / photo["path"]).is_file())

    def test_capture_stops_when_gallery_changes(self):
        b = object.__new__(browser.Browser)
        b.tab = 2
        with patch.object(b, "inspect", side_effect=[{"photos": [0]}, {"photos": [0]}, {"photos": [0, 1]}]), \
             patch.object(b, "_run") as execute:
            with self.assertRaisesRegex(RuntimeError, "Галерея изменилась"):
                b.capture("1234567890", "#gallery-image")
        self.assertEqual(execute.call_count, 1)
        self.assertEqual(execute.call_args.args, ("reload",))

    def test_image_ready_rejects_unchanged_source_and_escapes_selector_data(self):
        b = object.__new__(browser.Browser)
        selector = 'img[data-test="quoted"]'
        with patch.object(b, "_run", return_value=json.dumps({"source": "same", "box": [0, 0, 300, 300]})) as execute:
            with self.assertRaisesRegex(RuntimeError, "загруженной фотографии"):
                b.image_ready(selector, "same")
            self.assertIn(json.dumps({"selector": selector, "previous": "same"}), execute.call_args.args[1])

    def test_capture_does_not_save_an_unconfirmed_image(self):
        b = object.__new__(browser.Browser)
        with patch.object(b, "inspect", return_value={"photos": [0]}), \
             patch.object(b, "image_ready", side_effect=RuntimeError("image not decoded")), patch.object(b, "_run") as execute:
            with self.assertRaisesRegex(RuntimeError, "not decoded"):
                b.capture("1234567890", "#gallery-image")
        self.assertEqual(execute.call_count, 1)
        self.assertEqual(execute.call_args.args, ("reload",))

    def test_scroll_and_wait_cannot_exceed_fixed_budgets(self):
        b = object.__new__(browser.Browser)
        with patch.object(b, "current_url", return_value="https://www.avito.ru/all"), \
             patch.object(b, "no_captcha"), patch.object(b, "_run", return_value="ok") as execute:
            for command, args in (("scroll", ["--times", "999999"]), ("scroll", ["--wait", "999999"]),
                                  ("wait", ["body", "999999"]), ("wait", ["--load"]), ("wait", ["--domcontentloaded"])):
                with self.subTest(command=command, args=args), self.assertRaises(ValueError):
                    b.run(command, args)
            execute.assert_not_called()
            b.run("wait", ["body"])
            self.assertEqual(execute.call_args.args, ("wait", "body", "10000"))
            b.run("scroll", ["#result"])
            self.assertEqual(execute.call_args.args, ("scroll", "--times", "1", "#result"))
            b.run("scroll", [])
            self.assertEqual(execute.call_args.args, ("scroll",))


if __name__ == "__main__":
    unittest.main()
