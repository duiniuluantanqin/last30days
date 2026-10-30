from dataclasses import replace
from functools import partial
import json
from pathlib import Path
import tempfile
from threading import Event, Thread
import time
import unittest

import httpx

from forum_archive.config import Settings, TitleFilter
from forum_archive.preview import PreviewHandler, serve_reload, snapshot
from forum_archive.site import ArchiveHandler, ArchiveServer, build


ROOT = Path(__file__).resolve().parents[1]


class PreviewTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        settings = Settings.load(ROOT / "config.toml")
        self.settings = replace(settings, root=self.root, filters=TitleFilter([]),
                                database=self.root / "archive.sqlite3", output=self.root / "site")

    def server(self, handler):
        server = ArchiveServer(("127.0.0.1", 0), partial(handler, output=self.settings.output))
        thread = Thread(target=server.serve_forever, daemon=True)
        thread.start()

        def close():
            server.shutdown()
            server.server_close()
            thread.join()

        self.addCleanup(close)
        return server

    def test_reload_script_and_version_are_only_served_in_development(self):
        first = build(self.settings)
        server = self.server(PreviewHandler)
        url = f"http://127.0.0.1:{server.server_port}"
        with httpx.Client() as client:
            page = client.get(url)
            self.assertIn(f'/__preview__/reload.js?v={first.name}', page.text)
            self.assertEqual(page.headers["cache-control"], "no-store")
            self.assertIn("connect-src 'self'", page.headers["content-security-policy"])
            self.assertEqual(client.get(url + "/__preview__/version").json(), first.name)
            self.assertEqual(client.head(url).headers["content-length"], str(len(page.content)))
            script = client.get(url + "/__preview__/reload.js")
            self.assertEqual(script.status_code, 200)
            self.assertIn("location.reload()", script.text)
            second = build(self.settings)
            self.assertEqual(client.get(url + "/__preview__/version").json(), second.name)
        plain = self.server(ArchiveHandler)
        page = httpx.get(f"http://127.0.0.1:{plain.server_port}")
        self.assertNotIn("/__preview__/reload.js", page.text)
        self.assertNotIn("/__preview__/reload.js", (second / "index.html").read_text(encoding="utf-8"))

    def test_preview_port_cannot_be_shared_by_two_servers(self):
        with ArchiveServer(("127.0.0.1", 0), ArchiveHandler) as first:
            with self.assertRaises(OSError):
                with ArchiveServer(first.server_address, ArchiveHandler):
                    pass

    def test_reload_rebuilds_configuration_and_releases_port_on_stop(self):
        config = self.root / "config.toml"
        original = '[source]\nlist_url = "https://example.com/thread0806.php?fid=7"\n[site]\ntitle = "FirstPreview"\n'
        config.write_text(original, encoding="utf-8")
        with ArchiveServer(("127.0.0.1", 0), ArchiveHandler) as probe:
            port = probe.server_port
        stop = Event()
        errors = []

        def run():
            try:
                serve_reload(config, "127.0.0.1", port, demo=True, stop_event=stop)
            except Exception as error:
                errors.append(error)

        thread = Thread(target=run)
        thread.start()

        def close():
            stop.set()
            thread.join(timeout=10)
            self.assertFalse(thread.is_alive())

        self.addCleanup(close)

        def wait_for_title(title):
            deadline = time.monotonic() + 10
            while time.monotonic() < deadline:
                self.assertFalse(errors, errors)
                try:
                    response = httpx.get(f"http://127.0.0.1:{port}", timeout=0.5)
                    if f"{title}首页" in response.text:
                        return response
                except httpx.TransportError:
                    pass
                stop.wait(0.1)
            self.fail(f"Preview did not update to {title}")

        first = wait_for_title("FirstPreview")
        config.write_text(original.replace("FirstPreview", "ChangedPreview"), encoding="utf-8")
        second = wait_for_title("ChangedPreview")
        self.assertNotEqual(first.text, second.text)
        self.assertIn("connect-src 'self'", second.headers["content-security-policy"])
        close()
        with ArchiveServer(("127.0.0.1", port), ArchiveHandler):
            pass

    def test_watch_detects_source_config_and_keywords_but_ignores_build_output(self):
        config = self.root / "config.toml"
        config.write_text('[filter]\nkeyword_files = ["extra.txt"]\n', encoding="utf-8")
        package = self.root / "forum_archive"
        (package / "static").mkdir(parents=True)
        (package / "templates").mkdir()
        (self.root / "blacklist").mkdir()
        before = snapshot(config, package)
        for filename in ("forum_archive/site.py", "forum_archive/static/style.css",
                         "forum_archive/static/browse.js", "forum_archive/templates/index.html",
                         "blacklist/block-test.txt", "extra.txt", "config.toml"):
            with self.subTest(filename=filename):
                path = self.root / filename
                path.write_text(path.read_text(encoding="utf-8") + "\n" if path.exists() else "changed", encoding="utf-8")
                after = snapshot(config, package)
                self.assertNotEqual(before, after)
                before = after
        (self.root / "site").mkdir()
        (self.root / "site/current.json").write_text(json.dumps({"release": "test"}), encoding="utf-8")
        (package / "__pycache__").mkdir()
        (package / "__pycache__/site.pyc").write_bytes(b"generated")
        self.assertEqual(before, snapshot(config, package))


if __name__ == "__main__":
    unittest.main()
