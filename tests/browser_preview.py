"""Check live preview in Chrome using an isolated copy of the application."""

import argparse
from pathlib import Path
import shutil
import sys
import tempfile
from threading import Event, Thread
import time

from playwright.sync_api import Error, expect, sync_playwright
import httpx


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--http-origin", action="store_true")
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    with tempfile.TemporaryDirectory() as temporary, sync_playwright() as playwright:
        isolated = Path(temporary)
        package = isolated / "forum_archive"
        shutil.copytree(root / "forum_archive", package, ignore=shutil.ignore_patterns("__pycache__"))
        config = isolated / "config.toml"
        config.write_text('[source]\nlist_url = "https://example.com/thread0806.php?fid=7"\n[site]\ntitle = "LivePreviewStart"\n', encoding="utf-8")
        sys.path.insert(0, str(isolated))
        from forum_archive.preview import serve_reload
        from forum_archive.site import ArchiveHandler, ArchiveServer

        with ArchiveServer(("127.0.0.1", 0), ArchiveHandler) as probe:
            port = probe.server_port
        stop = Event()
        failures = []

        def run():
            try:
                serve_reload(config, "127.0.0.1", port, demo=True, stop_event=stop)
            except Exception as error:
                failures.append(error)

        thread = Thread(target=run)
        thread.start()
        try:
            url = f"http://127.0.0.1:{port}"
            deadline = time.monotonic() + 10
            while True:
                assert not failures, failures
                try:
                    if httpx.get(url, timeout=0.5).status_code == 200:
                        break
                except httpx.TransportError:
                    pass
                assert time.monotonic() < deadline, "Preview did not start"
                stop.wait(0.1)
            chrome = Path(r"C:\Program Files\Google\Chrome\Application\chrome.exe")
            browser = playwright.chromium.launch(headless=True, **({"executable_path": str(chrome)} if chrome.is_file() else {}))
            context = browser.new_context(viewport={"width": 1440, "height": 900})
            browser_origin = "http://preview.test" if args.http_origin else url

            def route_request(route):
                if route.request.url.startswith(browser_origin + "/"):
                    if args.http_origin:
                        try:
                            response = route.fetch(url=route.request.url.replace(browser_origin, url, 1), timeout=2000)
                        except Error:
                            route.abort()
                        else:
                            route.fulfill(response=response)
                    else:
                        route.continue_()
                else:
                    route.abort()

            context.route("**/*", route_request)
            page = context.new_page()
            errors = []
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.on("console", lambda message: errors.append(message.text) if "Content Security Policy" in message.text else None)
            page.goto(browser_origin, wait_until="networkidle")
            if args.http_origin:
                assert not page.evaluate("isSecureContext")
            expect(page.locator(".brand")).to_contain_text("LivePreviewStart")

            config.write_text(config.read_text(encoding="utf-8").replace("LivePreviewStart", "LivePreviewChanged"), encoding="utf-8")
            expect(page.locator(".brand")).to_contain_text("LivePreviewChanged", timeout=10000)

            css = package / "static/style.css"
            css.write_text(css.read_text(encoding="utf-8") + "\n.brand{color:rgb(12,34,56)}\n", encoding="utf-8")
            page.wait_for_function("() => getComputedStyle(document.querySelector('.brand')).color === 'rgb(12, 34, 56)'", timeout=10000)

            script = package / "static/browse.js"
            script.write_text(script.read_text(encoding="utf-8") + '\ndocument.documentElement.dataset.previewProbe = "updated";\n', encoding="utf-8")
            page.wait_for_function("() => document.documentElement.dataset.previewProbe === 'updated'", timeout=10000)

            template = package / "templates/index.html"
            template.write_text(template.read_text(encoding="utf-8").replace('{% block content %}', '{% block content %}<p id="preview-template-probe">Template refreshed</p>'), encoding="utf-8")
            expect(page.locator("#preview-template-probe")).to_have_text("Template refreshed", timeout=10000)

            source = package / "site.py"
            with page.expect_response(lambda response: response.request.is_navigation_request() and response.headers.get("x-preview-probe") == "fresh-python", timeout=10000):
                source.write_text(source.read_text(encoding="utf-8").replace('self.send_header("Cache-Control", "no-store")', 'self.send_header("X-Preview-Probe", "fresh-python")\n        self.send_header("Cache-Control", "no-store")'), encoding="utf-8")
            page.wait_for_load_state("networkidle")

            (isolated / "blacklist").mkdir()
            (isolated / "blacklist/blacklist.txt").write_text("把旧电脑", encoding="utf-8")
            expect(page.locator(".article-info h2 a", has_text="把旧电脑")).to_have_count(0, timeout=10000)
            assert not errors, errors
            browser.close()
        finally:
            stop.set()
            thread.join(timeout=10)
            assert not thread.is_alive(), "Preview watcher did not stop"
            assert not failures, failures
    print("Live preview: automatic browser refresh for config, CSS, JS, templates, Python headers and blacklist passed")


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    main()
