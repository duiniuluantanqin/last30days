from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest


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


if __name__ == "__main__":
    unittest.main()
