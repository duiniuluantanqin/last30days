from contextlib import chdir
from dataclasses import replace
import os
from pathlib import Path
import shutil
import subprocess
import tarfile
import tempfile
import tomllib
import unittest

from forum_archive.config import Settings
from scripts.remote import Remote


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/deploy-analytics.sh"


class AnalyticsDeployTests(unittest.TestCase):
    def test_bundle_contains_only_service_code_and_minimal_runtime_config(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            settings = replace(Settings.load(ROOT / "config.toml"), root=root)
            remote = Remote.__new__(Remote)
            remote.root = "/var/www/html/last30days"
            uploaded = []
            commands = []

            def upload(local, destination):
                with tarfile.open(local) as bundle:
                    names = bundle.getnames()
                    self.assertIn("forum_archive/analytics.py", names)
                    self.assertIn("pyproject.toml", names)
                    self.assertIn("analytics.service", names)
                    self.assertFalse(any(name.startswith(("blacklist/", "site/", "data/", ".git/")) for name in names))
                    self.assertFalse(any(name.endswith((".sqlite3", ".pyc", ".html", ".js")) for name in names))
                    config = tomllib.loads(bundle.extractfile("config.toml").read().decode())
                    self.assertEqual(config["site"]["analytics_origin"], settings.site.analytics_origin)
                    self.assertNotIn("filter", config)
                    unit = bundle.extractfile("analytics.service").read().decode()
                    self.assertIn("/var/www/html/last30days/analytics/active/.venv/bin/python", unit)
                    self.assertIn("/var/www/html/last30days/state/analytics.sqlite3", unit)
                    self.assertNotIn("User=", unit)
                uploaded.append(destination)

            remote.upload = upload
            remote.execute = lambda command, **kwargs: commands.append((command, kwargs))
            with chdir(root):
                remote.deploy_analytics(settings)
            self.assertEqual(len(uploaded), 1)
            self.assertRegex(uploaded[0], r"/incoming/analytics-[0-9]+\.tar\.gz$")
            self.assertIn("sh -s -- /var/www/html/last30days analytics-", commands[-1][0])
            self.assertIn("systemctl --user", commands[-1][1]["input"])
            self.assertNotIn("save-state", commands[-1][1]["input"])


class AnalyticsDeployShellTests(unittest.TestCase):
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
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.deployment = self.root / "deployment"
        for directory in ("incoming", "state", "analytics/releases/analytics-1", "releases/site-old"):
            (self.deployment / directory).mkdir(parents=True)
        self.analytics = self.deployment / "analytics"
        try:
            (self.analytics / "active").symlink_to("releases/analytics-1", target_is_directory=True)
        except OSError as error:
            self.skipTest(f"无法创建测试软链接：{error}")
        for name in ("archive.sqlite3", "archive.sqlite3.bak", "analytics.sqlite3"):
            (self.deployment / "state" / name).write_bytes(name.encode())
        (self.deployment / "releases/site-old/index.html").write_text("keep website")
        self.bin = self.root / "bin"
        self.bin.mkdir()
        self.write_command("loginctl", 'printf "%s\\n" "${TEST_LINGER:-yes}"\n')
        self.write_command("systemctl", '''printf "%s\\n" "$*" >> "$TEST_SYSTEMCTL_LOG"
if [ "$1" = "--system" ]; then exit "${TEST_SYSTEM_SERVICE_STATUS:-1}"; fi
''')
        self.write_command("python3", '''if [ "$1" = "-m" ] && [ "$2" = "venv" ]; then
    mkdir -p "$3/bin"
    cp "$TEST_VENV_PYTHON" "$3/bin/python"
    chmod +x "$3/bin/python"
fi
''')
        self.python_stub = self.write_command("venv-python", '''if [ "$1" = "-m" ] && [ "$2" = "pip" ]; then
    exit "${TEST_PIP_STATUS:-0}"
fi
if [ "$1" = "-" ]; then
    cat > /dev/null
    exit "${TEST_HEALTH_STATUS:-0}"
fi
''')
        self.config_dir = self.root / "config/systemd/user"
        self.config_dir.mkdir(parents=True)
        self.unit = self.config_dir / "forum-analytics.service"
        self.unit.write_text("previous unit\n")
        package = self.root / "package"
        package.mkdir()
        (package / "analytics.service").write_text("new unit\n")
        (package / "config.toml").write_text('[source]\nlist_url="https://example.com/"\n')
        with tarfile.open(self.deployment / "incoming/analytics-2.tar.gz", "w:gz") as bundle:
            for path in package.iterdir():
                bundle.add(path, arcname=path.name)

    def write_command(self, name, content):
        path = self.bin / name
        path.write_text("#!/bin/sh\n" + content, encoding="utf-8", newline="\n")
        path.chmod(0o755)
        return path

    def run_deploy(self, **environment):
        env = dict(os.environ)
        env.update(PATH=str(self.bin) + os.pathsep + env["PATH"],
                   XDG_CONFIG_HOME=str(self.root / "config"),
                   TEST_SYSTEMCTL_LOG=str(self.root / "systemctl.log"),
                   TEST_VENV_PYTHON=str(self.python_stub), **environment)
        if os.name == "nt":
            env["MSYS"] = "winsymlinks:nativestrict"
        return subprocess.run([self.shell, str(SCRIPT), "deployment", "analytics-2",
                               "https://duiniuluantanqin.github.io"], cwd=self.root, env=env,
                              capture_output=True, text=True)

    def assert_state_unchanged(self):
        for name in ("archive.sqlite3", "archive.sqlite3.bak", "analytics.sqlite3"):
            self.assertEqual((self.deployment / "state" / name).read_bytes(), name.encode())
        self.assertEqual((self.deployment / "releases/site-old/index.html").read_text(), "keep website")

    def test_success_switches_only_service_code_and_preserves_all_databases(self):
        result = self.run_deploy()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual((self.analytics / "active").resolve(), self.analytics / "releases/analytics-2")
        self.assertEqual(self.unit.read_text(), "new unit\n")
        commands = (self.root / "systemctl.log").read_text()
        self.assertIn("--user enable forum-analytics.service", commands)
        self.assertIn("--user restart forum-analytics.service", commands)
        self.assertTrue((self.analytics / "releases/analytics-1").is_dir())
        self.assert_state_unchanged()

    def test_failed_dependency_install_does_not_change_running_release(self):
        result = self.run_deploy(TEST_PIP_STATUS="1")
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual((self.analytics / "active").resolve(), self.analytics / "releases/analytics-1")
        self.assertEqual(self.unit.read_text(), "previous unit\n")
        self.assertNotIn("restart", (self.root / "systemctl.log").read_text())
        self.assert_state_unchanged()

    def test_first_deploy_installs_and_enables_user_service(self):
        (self.analytics / "active").unlink()
        self.unit.unlink()
        result = self.run_deploy()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual((self.analytics / "active").resolve(), self.analytics / "releases/analytics-2")
        self.assertEqual(self.unit.read_text(), "new unit\n")
        self.assertIn("--user enable forum-analytics.service", (self.root / "systemctl.log").read_text())
        self.assert_state_unchanged()

    def test_failed_health_check_restores_previous_release_and_unit(self):
        result = self.run_deploy(TEST_HEALTH_STATUS="1")
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual((self.analytics / "active").resolve(), self.analytics / "releases/analytics-1")
        self.assertEqual(self.unit.read_text(), "previous unit\n")
        self.assertEqual((self.root / "systemctl.log").read_text().count("--user restart forum-analytics.service"), 2)
        self.assert_state_unchanged()

    def test_first_deploy_failure_disables_service_without_leaving_active_code(self):
        (self.analytics / "active").unlink()
        self.unit.unlink()
        result = self.run_deploy(TEST_HEALTH_STATUS="1")
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse((self.analytics / "active").exists())
        self.assertIn("--user disable --now forum-analytics.service", (self.root / "systemctl.log").read_text())
        self.assert_state_unchanged()

    def test_missing_linger_stops_before_code_or_service_changes(self):
        result = self.run_deploy(TEST_LINGER="no")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("loginctl enable-linger", result.stderr)
        self.assertFalse((self.analytics / "releases/analytics-2").exists())
        self.assertEqual(self.unit.read_text(), "previous unit\n")
        self.assert_state_unchanged()

    def test_old_system_service_requires_explicit_migration_before_installing(self):
        result = self.run_deploy(TEST_SYSTEM_SERVICE_STATUS="0")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("sudo systemctl disable --now", result.stderr)
        self.assertFalse((self.analytics / "releases/analytics-2").exists())
        self.assert_state_unchanged()


if __name__ == "__main__":
    unittest.main()
