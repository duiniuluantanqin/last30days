"""Restore state and publish a static release over SSH. Never invoked by local crawl."""

import argparse
from datetime import datetime
import os
from pathlib import Path, PurePosixPath
import re
import shlex
import subprocess
import tarfile
import time
from zoneinfo import ZoneInfo

from forum_archive.config import Settings
from forum_archive.store import Store


class Remote:
    def __init__(self):
        names = ("DEPLOY_HOST", "DEPLOY_USER", "DEPLOY_SSH_KEY", "DEPLOY_KNOWN_HOSTS")
        if any(not os.environ.get(name) for name in names):
            raise ValueError("需要配置 DEPLOY_HOST、DEPLOY_USER、DEPLOY_SSH_KEY、DEPLOY_KNOWN_HOSTS")
        host, user = os.environ["DEPLOY_HOST"], os.environ["DEPLOY_USER"]
        if not re.fullmatch(r"[A-Za-z0-9.-]+", host) or host.startswith("-"):
            raise ValueError("DEPLOY_HOST 必须为域名或 IPv4 地址")
        if not re.fullmatch(r"[a-z_][a-z0-9_-]*", user) or user == "root":
            raise ValueError("请使用非 root 的专用部署用户")
        self.root = os.environ.get("DEPLOY_ROOT", "/var/www/html/last30days").rstrip("/")
        if (not re.fullmatch(r"/[A-Za-z0-9_./-]+", self.root)
                or ".." in PurePosixPath(self.root).parts
                or len(PurePosixPath(self.root).parts) < 3):
            raise ValueError("DEPLOY_ROOT 必须为独立的绝对网站目录，如 /var/www/html/last30days")
        port = int(os.environ.get("DEPLOY_PORT", "22"))
        if not 1 <= port <= 65535:
            raise ValueError("无效的 DEPLOY_PORT")
        directory = Path(".cache/deploy")
        directory.mkdir(parents=True, exist_ok=True)
        os.chmod(directory, 0o700)
        key = directory / "key"
        known = directory / "known_hosts"
        key.write_text(os.environ["DEPLOY_SSH_KEY"].strip() + "\n", encoding="utf-8")
        known.write_text(os.environ["DEPLOY_KNOWN_HOSTS"].strip() + "\n", encoding="utf-8")
        os.chmod(key, 0o600)
        os.chmod(known, 0o600)
        options = ["-i", str(key.resolve()), "-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=yes",
                   "-o", f"UserKnownHostsFile={known.resolve()}", "-o", "ConnectTimeout=20"]
        self.target = f"{user}@{host}"
        self.ssh = ["ssh", *options, "-p", str(port), self.target]
        self.scp = ["scp", *options, "-P", str(port)]

    def execute(self, command, **kwargs):
        return subprocess.run([*self.ssh, command], **kwargs)

    def upload(self, local: Path, destination: str):
        subprocess.run([*self.scp, str(local), f"{self.target}:{destination}"], check=True)

    def restore(self, settings: Settings):
        path = shlex.quote(f"{self.root}/state/archive.sqlite3")
        result = self.execute(f"if test -f {path}; then cat {path}; else exit 3; fi", stdout=subprocess.PIPE)
        if result.returncode == 3:
            print("服务器尚无状态文件，将开始首次采集。")
            return
        result.check_returncode()
        settings.database.parent.mkdir(parents=True, exist_ok=True)
        temporary = settings.database.with_suffix(".incoming")
        temporary.write_bytes(result.stdout)
        temporary.replace(settings.database)
        print("已恢复服务器上的采集进度。")

    def publish(self, settings: Settings, release: Path):
        release = release.resolve()
        if release.parent != settings.output.resolve() or not (release / "index.html").is_file():
            raise ValueError("只允许发布本程序生成的完整版本目录")
        if not settings.database.is_file():
            raise ValueError("采集状态不存在，拒绝发布")
        identifier = f"release-{time.time_ns()}"
        archive = Path(".cache/deploy/site.tar.gz")
        removed = Path(".cache/deploy/removed.txt")
        with Store(settings, datetime.now(ZoneInfo(settings.source.timezone))) as store:
            removed_ids = sorted(store.removed_ids())
        if any(not re.fullmatch(r"[0-9]+", thread_id) for thread_id in removed_ids):
            raise ValueError("删除清单包含无效的帖子 ID，拒绝发布")
        removed.write_bytes("".join(f"{thread_id}\n" for thread_id in removed_ids).encode("ascii"))
        with tarfile.open(archive, "w:gz") as bundle:
            for path in sorted(release.rglob("*")):
                if path.is_symlink():
                    raise ValueError("发布目录不能包含符号链接")
                if path.is_file():
                    bundle.add(path, arcname=str(path.relative_to(release)).replace("\\", "/"), recursive=False)
        root = self.root
        self.execute(f"mkdir -p {root}/incoming {root}/releases {root}/state", check=True)
        self.upload(archive, f"{root}/incoming/{identifier}.tar.gz")
        self.upload(settings.database, f"{root}/incoming/{identifier}.sqlite3")
        self.upload(removed, f"{root}/incoming/{identifier}.removed.txt")
        cleanup = Path(".cache/deploy/purge-blocked.sh")
        cleanup.write_bytes(Path(__file__).with_name("purge-blocked.sh").read_bytes().replace(b"\r\n", b"\n"))
        self.upload(cleanup, f"{root}/incoming/{identifier}.purge.sh")
        retention = Path(".cache/deploy/prune-deployments.sh")
        retention.write_bytes(Path(__file__).with_name("prune-deployments.sh").read_bytes().replace(b"\r\n", b"\n"))
        self.upload(retention, f"{root}/incoming/{identifier}.prune.sh")
        # Files/state are fully uploaded before switching the public entry point.
        # All paths are validated above; only a generated symlink/state is replaced.
        commands = [
            f"mkdir {root}/releases/{identifier}",
            f"tar -xzf {root}/incoming/{identifier}.tar.gz -C {root}/releases/{identifier}",
            f"test -f {root}/releases/{identifier}/index.html",
            f"if test -f {root}/state/archive.sqlite3; then "
            f"cp {root}/state/archive.sqlite3 {root}/incoming/{identifier}.backup.sqlite3 && "
            f"mv -Tf {root}/incoming/{identifier}.backup.sqlite3 {root}/state/archive.sqlite3.bak; fi",
            f"ln -s releases/{identifier} {root}/current-{identifier}",
            f"mv -Tf {root}/current-{identifier} {root}/current",
            f"mv {root}/incoming/{identifier}.sqlite3 {root}/state/archive.sqlite3",
            f"sh {root}/incoming/{identifier}.purge.sh {root} {root}/incoming/{identifier}.removed.txt",
            f"sh {root}/incoming/{identifier}.prune.sh {root} {identifier}",
        ]
        self.execute(" && ".join(commands), check=True)
        print("网站与采集进度已发布，命中文章、旧版本和上传残留已按规则清理。")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["restore", "publish"])
    parser.add_argument("--config", default="config.toml")
    parser.add_argument("--release-file", default=".cache/release.txt")
    args = parser.parse_args()
    settings = Settings.load(args.config)
    remote = Remote()
    if args.command == "restore":
        remote.restore(settings)
    else:
        remote.publish(settings, Path(Path(args.release_file).read_text(encoding="utf-8").strip()))


if __name__ == "__main__":
    main()
