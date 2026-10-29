from datetime import datetime
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from zoneinfo import ZoneInfo

from forum_archive.config import Settings
from forum_archive.site import build
from forum_archive.source import Post, Thread
from forum_archive.store import Store


SCRIPT = Path(__file__).resolve().parents[1] / "scripts/purge-blocked.sh"


class RemoteCleanupTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.shell = shutil.which("sh")
        if not cls.shell:
            git = shutil.which("git")
            candidate = Path(git).parent.parent / "bin/bash.exe" if git else None
            if candidate and candidate.is_file():
                cls.shell = str(candidate)
        if not cls.shell:
            raise unittest.SkipTest("需要 POSIX shell；Linux CI 会运行这些检查")

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        for filename in ("deployment/releases/release-one/posts/101.html",
                         "deployment/releases/release-two/posts/101.html",
                         "deployment/releases/release-one/posts/102.html",
                         "deployment/releases/unrelated/posts/101.html",
                         "deployment/state/101.html", "outside/101.html"):
            path = self.root / filename
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("article", encoding="utf-8")

    def run_cleanup(self, manifest):
        (self.root / "blocked.txt").write_bytes(manifest.encode("ascii"))
        return subprocess.run([self.shell, str(SCRIPT), "deployment", "blocked.txt"],
                              cwd=self.root, capture_output=True, text=True)

    def test_only_matching_generated_pages_are_deleted(self):
        result = self.run_cleanup("101\n")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse((self.root / "deployment/releases/release-one/posts/101.html").exists())
        self.assertFalse((self.root / "deployment/releases/release-two/posts/101.html").exists())
        for filename in ("deployment/releases/release-one/posts/102.html",
                         "deployment/releases/unrelated/posts/101.html",
                         "deployment/state/101.html", "outside/101.html"):
            self.assertTrue((self.root / filename).is_file())
        self.assertEqual(self.run_cleanup("101\n").returncode, 0)

    def test_invalid_manifest_is_rejected_before_any_deletion(self):
        result = self.run_cleanup("101\n../../outside\n")
        self.assertNotEqual(result.returncode, 0)
        self.assertTrue((self.root / "deployment/releases/release-one/posts/101.html").is_file())
        self.assertTrue((self.root / "outside/101.html").is_file())

    def test_empty_manifest_keeps_all_pages(self):
        before = sorted(self.root.rglob("*.html"))
        result = self.run_cleanup("")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(sorted(self.root.rglob("*.html")), before)

    def test_removed_articles_invalidate_old_full_text_indexes_only(self):
        for name in ("release-one", "release-two", "unrelated"):
            index = self.root / "deployment/releases" / name / "assets/search-index.js"
            index.parent.mkdir()
            index.write_text("search data", encoding="utf-8")
        current = self.root / "deployment/current"
        current.symlink_to(Path("releases") / "release-two", target_is_directory=True)
        self.assertEqual(self.run_cleanup("").returncode, 0)
        self.assertTrue((self.root / "deployment/releases/release-one/assets/search-index.js").exists())
        self.assertEqual(self.run_cleanup("101\n").returncode, 0)
        self.assertFalse((self.root / "deployment/releases/release-one/assets/search-index.js").exists())
        self.assertTrue((current / "assets/search-index.js").exists())
        self.assertTrue((self.root / "deployment/releases/unrelated/assets/search-index.js").exists())

    def test_keyword_file_update_deletes_existing_title_and_body_matches(self):
        config = self.root / "config.toml"
        config.write_text('[source]\nlist_url = "https://forum.example/thread0806.php?fid=7"\n', encoding="utf-8")
        keywords = self.root / "blacklist/keywords.txt"
        keywords.parent.mkdir()
        keywords.write_text("", encoding="utf-8")
        settings = Settings.load(config)
        now = datetime.now(ZoneInfo(settings.source.timezone))
        with Store(settings, now) as store:
            for thread_id, title, body in (("101", "标题新增词讨论", "普通正文"),
                                          ("102", "保留讨论", "普通正文"),
                                          ("103", "正常标题", "普通文字" * 100 + "正文新增词")):
                store.discover(Thread(thread_id, settings.source.list_url, title, now.isoformat()), now)
                store.save_post(thread_id, Post(title, now.isoformat(), f"<p>{body}</p>", body[:180], 0), now, False)
        previous = build(settings, now=now)
        old_remote = self.root / "deployment/releases/release-existing"
        shutil.copytree(previous, old_remote)

        keywords.write_text("标题新增词\n正文新增词\n", encoding="utf-8")
        settings = Settings.load(config)
        latest = build(settings, now=now)
        with Store(settings, now) as store:
            removed = store.removed_ids()
        self.assertEqual(removed, {"101", "103"})
        # Publish uses these same database IDs as its remote removal manifest.
        result = self.run_cleanup("".join(f"{thread_id}\n" for thread_id in sorted(removed)))
        self.assertEqual(result.returncode, 0, result.stderr)
        for release in (old_remote, latest):
            self.assertFalse((release / "posts/101.html").exists())
            self.assertFalse((release / "posts/103.html").exists())
            self.assertTrue((release / "posts/102.html").is_file())
        index = (latest / "index.html").read_text(encoding="utf-8")
        self.assertNotIn("标题新增词讨论", index)
        self.assertNotIn("正常标题", index)
        self.assertIn("保留讨论", index)


if __name__ == "__main__":
    unittest.main()
