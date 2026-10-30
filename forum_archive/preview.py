"""Local development preview; no watcher or reload script is published."""

from io import BytesIO
import json
from multiprocessing import get_context
from pathlib import Path
from threading import Event
import time
import tomllib
from urllib.parse import urlsplit

from .config import Settings
from .site import ArchiveHandler, ArchiveServer, PACKAGE, build, current_release, serve


RELOAD_SCRIPT = b"""(() => {
  const version = new URL(document.currentScript.src).searchParams.get("v");
  async function check() {
    try {
      const response = await fetch("/__preview__/version", {cache: "no-store"});
      if (response.ok && await response.json() !== version) {
        location.reload();
        return;
      }
    } catch {}
    setTimeout(check, 1000);
  }
  setTimeout(check, 1000);
})();
"""


class PreviewHandler(ArchiveHandler):
    def send_head(self):
        path = urlsplit(self.path).path
        if path == "/__preview__/version":
            return self.respond(json.dumps(current_release(self.output).name).encode(), "application/json")
        if path == "/__preview__/reload.js":
            return self.respond(RELOAD_SCRIPT, "text/javascript; charset=utf-8")
        file = Path(self.translate_path(self.path))
        if file.is_dir():
            file /= "index.html"
        if file.suffix == ".html" and file.is_file():
            version = Path(self.directory).name
            script = f'<script src="/__preview__/reload.js?v={version}" defer></script>'
            content = file.read_text(encoding="utf-8").replace("</body>", script + "</body>")
            return self.respond(content.encode("utf-8"), "text/html; charset=utf-8")
        return super().send_head()

    def respond(self, content, content_type):
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(content)))
        self.end_headers()
        return BytesIO(content)

    def log_request(self, code="-", size="-"):
        if not self.path.startswith("/__preview__/"):
            super().log_request(code, size)


def snapshot(config: Path, package: Path = PACKAGE):
    paths = {config, *config.parent.joinpath("blacklist").glob("*.txt")}
    paths.update(path for path in package.rglob("*") if path.suffix in {".py", ".html", ".css", ".js"})
    try:
        with config.open("rb") as stream:
            raw = tomllib.load(stream)
        paths.update(config.parent / name for name in raw.get("filter", {}).get("keyword_files", []))
    except (OSError, tomllib.TOMLDecodeError):
        # An editor can briefly leave the configuration incomplete during a save.
        pass
    result = {}
    for path in paths:
        try:
            stat = path.stat()
            result[path] = (stat.st_mtime_ns, stat.st_size)
        except FileNotFoundError:
            pass
    return result


def run_worker(config, host, port, demo):
    settings = Settings.load(config)
    if demo:
        from .__main__ import create_demo, demo_settings
        settings = demo_settings(settings)
        if not settings.database.is_file():
            create_demo(settings)
        else:
            build(settings)
    else:
        build(settings)
    serve(settings, host, port, handler_class=PreviewHandler)


def serve_reload(config: Path, host: str, port: int, *, demo=False, stop_event=None):
    # Reject an occupied port before starting a watcher, including on Windows.
    with ArchiveServer((host, port), ArchiveHandler):
        pass
    context = get_context("spawn")
    stop_event = stop_event or Event()

    def start():
        worker = context.Process(target=run_worker, args=(config, host, port, demo), daemon=True)
        worker.start()
        return worker

    def stop(worker):
        if worker.is_alive():
            worker.terminate()
        worker.join(timeout=5)
        if worker.is_alive():
            worker.kill()
            worker.join()
        worker.close()

    previous = snapshot(config)
    worker = start()
    print("自动更新已开启：保存 Python、模板、CSS/JS、配置或词库后自动重建并刷新（Ctrl+C 停止）。", flush=True)
    try:
        while not stop_event.wait(0.5):
            if snapshot(config) == previous:
                continue
            time.sleep(0.2)
            previous = snapshot(config)
            print("检测到文件变化，正在重建并重启预览…", flush=True)
            stop(worker)
            worker = start()
    except KeyboardInterrupt:
        pass
    finally:
        stop(worker)
