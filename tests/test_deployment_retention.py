from contextlib import chdir
from dataclasses import replace
from datetime import datetime
from pathlib import Path
import os
import shutil
import subprocess
import tempfile
import time
import unittest
from unittest.mock import patch
from zoneinfo import ZoneInfo

from forum_archive.config import Settings
from forum_archive.store import Store
from scripts.remote import Remote


SCRIPT = Path(__file__).resolve().parents[1] / "scripts/prune-deployments.sh"


class DeploymentRetentionTests(unittest.TestCase):
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
        self.deployment = self.root / "deployment"
        for directory in ("releases", "incoming", "state"):
            (self.deployment / directory).mkdir(parents=True)
        self.current = self.deployment / "current"

    def release(self, identifier):
        path = self.deployment / "releases" / identifier
        path.mkdir()
        (path / "index.html").write_text(identifier, encoding="utf-8")
        return path

    def point_current(self, identifier):
        try:
            self.current.symlink_to(Path("releases") / identifier, target_is_directory=True)
        except OSError as error:
            self.skipTest(f"无法创建测试软链接：{error}")

    def run_cleanup(self, identifier):
        return subprocess.run([self.shell, str(SCRIPT), "deployment", identifier],
                              cwd=self.root, capture_output=True, text=True)

    def test_keeps_latest_three_by_version_number_and_protects_current(self):
        identifiers = ("release-9", "release-10", "release-99", "release-100", "release-200")
        for index, identifier in enumerate(identifiers):
            path = self.release(identifier)
            # Publication IDs determine order, even if directory mtimes disagree.
            date = time.time() - index * 3600
            os.utime(path, (date, date))
        self.point_current("release-9")
        unrelated = self.deployment / "releases/unrelated"
        unrelated.mkdir()
        state = self.deployment / "state/archive.sqlite3"
        backup = self.deployment / "state/archive.sqlite3.bak"
        state.write_bytes(b"current database")
        backup.write_bytes(b"previous database")
        result = self.run_cleanup("release-9")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(sorted(path.name for path in (self.deployment / "releases").iterdir()),
                         ["release-100", "release-200", "release-9", "release-99", "unrelated"])
        self.assertEqual((self.current / "index.html").read_text(), "release-9")
        self.assertEqual(state.read_bytes(), b"current database")
        self.assertEqual(backup.read_bytes(), b"previous database")
        self.assertEqual(self.run_cleanup("release-9").returncode, 0)

    def test_latest_current_leaves_exactly_three_versions(self):
        for identifier in ("release-1", "release-2", "release-3", "release-4", "release-5"):
            self.release(identifier)
        self.point_current("release-5")
        result = self.run_cleanup("release-5")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(sorted(path.name for path in (self.deployment / "releases").iterdir()),
                         ["release-3", "release-4", "release-5"])

    def test_removes_successful_uploads_and_only_old_failed_artifacts(self):
        self.release("release-100")
        self.point_current("release-100")
        incoming = self.deployment / "incoming"
        suffixes = (".tar.gz", ".sqlite3", ".backup.sqlite3", ".removed.txt", ".purge.sh", ".prune.sh")
        for identifier in ("release-100", "release-90", "release-80"):
            for suffix in suffixes:
                path = incoming / (identifier + suffix)
                path.write_text("temporary", encoding="utf-8")
                hours = 25 if identifier == "release-80" else 23
                date = time.time() - hours * 3600
                os.utime(path, (date, date))
        unrelated = incoming / "release-80.notes"
        unrelated.write_text("keep", encoding="utf-8")
        os.utime(unrelated, (0, 0))
        nested = incoming / "nested/release-80.tar.gz"
        nested.parent.mkdir()
        nested.write_text("keep", encoding="utf-8")
        os.utime(nested, (0, 0))
        result = self.run_cleanup("release-100")
        self.assertEqual(result.returncode, 0, result.stderr)
        for suffix in suffixes:
            self.assertFalse((incoming / ("release-100" + suffix)).exists())
            self.assertFalse((incoming / ("release-80" + suffix)).exists())
            self.assertTrue((incoming / ("release-90" + suffix)).is_file())
        self.assertTrue(unrelated.is_file())
        self.assertTrue(nested.is_file())

    def test_invalid_or_missing_current_stops_before_deleting(self):
        for identifier in ("release-1", "release-2", "release-3", "release-4"):
            self.release(identifier)
        result = self.run_cleanup("release-4")
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(len(list((self.deployment / "releases").iterdir())), 4)
        self.point_current("release-missing")
        result = self.run_cleanup("release-4")
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(len(list((self.deployment / "releases").iterdir())), 4)

    def test_current_outside_deployment_stops_before_deleting(self):
        self.release("release-1")
        outside = self.root / "outside"
        outside.mkdir()
        (outside / "index.html").write_text("outside", encoding="utf-8")
        try:
            self.current.symlink_to(outside, target_is_directory=True)
        except OSError as error:
            self.skipTest(f"无法创建测试软链接：{error}")
        self.assertNotEqual(self.run_cleanup("release-1").returncode, 0)
        self.assertTrue((outside / "index.html").is_file())
        self.assertTrue((self.deployment / "releases/release-1").is_dir())

    def test_invalid_identifier_stops_before_deleting(self):
        self.release("release-1")
        self.point_current("release-1")
        self.assertNotEqual(self.run_cleanup("../outside").returncode, 0)
        self.assertTrue((self.current / "index.html").is_file())

    def test_symlinked_upload_directory_is_not_traversed(self):
        self.release("release-100")
        self.point_current("release-100")
        outside = self.root / "outside"
        outside.mkdir()
        artifact = outside / "release-100.tar.gz"
        artifact.write_text("keep", encoding="utf-8")
        incoming = self.deployment / "incoming"
        incoming.rmdir()
        incoming.symlink_to(outside, target_is_directory=True)
        self.assertNotEqual(self.run_cleanup("release-100").returncode, 0)
        self.assertTrue(artifact.is_file())

    def test_old_release_symlinks_do_not_delete_external_files(self):
        for identifier in ("release-1", "release-2", "release-3", "release-4", "release-5"):
            self.release(identifier)
        self.point_current("release-5")
        outside = self.root / "outside"
        outside.mkdir()
        artifact = outside / "keep.txt"
        artifact.write_text("keep", encoding="utf-8")
        (self.deployment / "releases/release-0").symlink_to(outside, target_is_directory=True)
        (self.deployment / "releases/release-1/external").symlink_to(outside, target_is_directory=True)
        result = self.run_cleanup("release-5")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse((self.deployment / "releases/release-1").exists())
        self.assertTrue((self.deployment / "releases/release-0").is_symlink())
        self.assertEqual(artifact.read_text(), "keep")

    def local_publisher(self):
        original = Settings.load(SCRIPT.parent.parent / "config.toml")
        settings = replace(original, root=self.root, database=self.root / "data/archive.sqlite3", output=self.root / "site")
        now = datetime.now(ZoneInfo(settings.source.timezone))
        with Store(settings, now) as store:
            store.set("test_revision", "old")
        shutil.copyfile(settings.database, self.deployment / "state/archive.sqlite3")
        with Store(settings, now) as store:
            store.set("test_revision", "new")
        release = settings.output / "release-local"
        release.mkdir(parents=True)
        (release / "index.html").write_text("new website", encoding="utf-8")
        self.release("release-100")
        self.point_current("release-100")
        (self.root / ".cache/deploy").mkdir(parents=True)
        remote = Remote.__new__(Remote)
        remote.root = "deployment"

        def execute(command, **kwargs):
            environment = dict(os.environ)
            if os.name == "nt":
                environment["MSYS"] = "winsymlinks:nativestrict"
            return subprocess.run([self.shell, "-c", command], cwd=self.root, env=environment,
                                  capture_output=True, text=True, **kwargs)

        def upload(local, destination):
            target = self.root / destination
            self.assertTrue(target.resolve().is_relative_to(self.deployment.resolve()))
            shutil.copyfile(local, target)

        remote.execute = execute
        remote.upload = upload
        return remote, settings, release, now

    def test_publish_rotates_one_state_backup_and_removes_its_uploads(self):
        remote, settings, release, now = self.local_publisher()
        with chdir(self.root), patch("scripts.remote.time.time_ns", return_value=200):
            remote.publish(settings, release)
        backup_settings = replace(settings, database=self.deployment / "state/archive.sqlite3.bak")
        current_settings = replace(settings, database=self.deployment / "state/archive.sqlite3")
        with Store(backup_settings, now) as store:
            self.assertEqual(store.get("test_revision"), "old")
        with Store(current_settings, now) as store:
            self.assertEqual(store.get("test_revision"), "new")
        self.assertEqual(list((self.deployment / "incoming").iterdir()), [])
        with Store(settings, now) as store:
            store.set("test_revision", "next")
        with chdir(self.root), patch("scripts.remote.time.time_ns", return_value=300):
            remote.publish(settings, release)
        with Store(backup_settings, now) as store:
            self.assertEqual(store.get("test_revision"), "new")
        with Store(current_settings, now) as store:
            self.assertEqual(store.get("test_revision"), "next")
        self.assertEqual(sorted(path.name for path in (self.deployment / "state").iterdir()),
                         ["archive.sqlite3", "archive.sqlite3.bak"])
        self.assertEqual(list((self.deployment / "incoming").iterdir()), [])
        self.assertEqual((self.current / "index.html").read_text(), "new website")

    def test_backup_failure_does_not_switch_current_or_replace_state(self):
        remote, settings, release, now = self.local_publisher()
        backup = self.deployment / "state/archive.sqlite3.bak"
        backup.mkdir()
        with chdir(self.root), patch("scripts.remote.time.time_ns", return_value=200):
            with self.assertRaises(subprocess.CalledProcessError):
                remote.publish(settings, release)
        self.assertEqual((self.current / "index.html").read_text(), "release-100")
        with Store(replace(settings, database=self.deployment / "state/archive.sqlite3"), now) as store:
            self.assertEqual(store.get("test_revision"), "old")
        self.assertTrue((self.deployment / "incoming/release-200.backup.sqlite3").is_file())

    def test_first_publish_without_previous_state_needs_no_backup(self):
        remote, settings, release, now = self.local_publisher()
        self.current.unlink()
        (self.deployment / "state/archive.sqlite3").unlink()
        with chdir(self.root), patch("scripts.remote.time.time_ns", return_value=200):
            remote.publish(settings, release)
        self.assertFalse((self.deployment / "state/archive.sqlite3.bak").exists())
        with Store(replace(settings, database=self.deployment / "state/archive.sqlite3"), now) as store:
            self.assertEqual(store.get("test_revision"), "new")
        self.assertEqual(list((self.deployment / "incoming").iterdir()), [])
        self.assertEqual((self.current / "index.html").read_text(), "new website")


if __name__ == "__main__":
    unittest.main()
