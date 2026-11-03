"""Check first-party analytics using real cross-origin requests and temporary SQLite files."""

import argparse
from dataclasses import replace
from datetime import datetime
from functools import partial
from pathlib import Path
import sys
import tempfile
from threading import Thread as ServerThread
from zoneinfo import ZoneInfo

from playwright.sync_api import sync_playwright

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from forum_archive.analytics import AnalyticsHandler, AnalyticsStore
from forum_archive.config import Settings, TitleFilter
from forum_archive.site import ArchiveHandler, ArchiveServer, build
from forum_archive.source import Post, Thread
from forum_archive.store import Store


class MountedHandler(ArchiveHandler):
    def do_GET(self):
        if not self.path.startswith(self.base_path + "/"):
            self.send_error(404)
            return
        self.path = self.path[len(self.base_path):]
        super().do_GET()

    def log_message(self, *args):
        pass


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-path", default="/last30days")
    parser.add_argument("--http-origin", action="store_true")
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    now = datetime.now(ZoneInfo("Asia/Shanghai"))
    with tempfile.TemporaryDirectory() as temporary, sync_playwright() as playwright:
        temporary = Path(temporary)
        counters = AnalyticsStore(temporary / "state/analytics.sqlite3")
        api = ArchiveServer(("127.0.0.1", 0), AnalyticsHandler)
        api_host = "analytics.test" if args.http_origin else "127.0.0.1"
        endpoint = f"http://{api_host}:{api.server_port}/api/analytics"
        output = temporary / "site"
        handler = type("QuietMountedHandler", (MountedHandler,), {"base_path": args.base_path})
        server = ArchiveServer(("127.0.0.1", 0), partial(handler, output=output, analytics_endpoint=endpoint))
        site_host = "archive.test" if args.http_origin else "127.0.0.1"
        origin = f"http://{site_host}:{server.server_port}"
        site_url = origin + args.base_path
        api.RequestHandlerClass = partial(AnalyticsHandler, store=counters, origin=origin)
        settings = Settings.load(root / "config.toml")
        settings = replace(settings, root=temporary, filters=TitleFilter([]),
                           site=replace(settings.site, page_size=1, analytics_endpoint=endpoint, analytics_origin=origin),
                           database=temporary / "archive.sqlite3", output=output)
        with Store(settings, now) as store:
            for number in range(101, 104):
                store.discover(Thread(str(number), settings.source.list_url, "统计测试", now.isoformat()), now)
                store.save_post(str(number), Post("统计测试", now.isoformat(), "<p>正常文字</p>" * 30, "摘要", 0), now, False)
        build(settings, now=now, base_path=args.base_path)
        threads = [ServerThread(target=item.serve_forever, daemon=True) for item in (api, server)]
        for thread in threads:
            thread.start()
        try:
            chrome = Path(r"C:\Program Files\Google\Chrome\Application\chrome.exe")
            browser = playwright.chromium.launch(headless=True, args=[
                "--no-proxy-server",
                "--host-resolver-rules=MAP archive.test 127.0.0.1, MAP analytics.test 127.0.0.1",
            ], **({"executable_path": str(chrome)} if chrome.is_file() else {}))
            context = browser.new_context(viewport={"width": 1280, "height": 800})
            page = context.new_page()
            errors = []
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.on("console", lambda message: errors.append(message.text) if "Content Security Policy" in message.text else None)

            def check(uv, pv):
                assert counters.counts()["total"] == {"uv": uv, "pv": pv}, counters.counts()
                assert page.locator('.sidebar [data-count="total-uv"]').inner_text() == str(uv)
                assert page.locator('.sidebar [data-count="total-pv"]').inner_text() == str(pv)

            page.goto(site_url + "/", wait_until="networkidle")
            if args.http_origin:
                assert not page.evaluate("isSecureContext")
            page.locator("[data-analytics-counts]").wait_for(state="visible")
            assert page.locator("footer [data-analytics-counts]").count() == 0
            check(1, 1)
            screenshots = root / ".cache/screenshots"
            screenshots.mkdir(parents=True, exist_ok=True)
            page.screenshot(path=str(screenshots / "analytics-sidebar.png"))
            page.set_viewport_size({"width": 1280, "height": 650})
            page.evaluate("window.scrollTo({top: 250, behavior: 'instant'})")
            page.wait_for_function("scrollY > 0")
            box = page.locator("[data-analytics-counts]").bounding_box()
            assert 0 <= box["y"] and box["y"] + box["height"] <= 650
            assert page.locator("[data-analytics-counts]").evaluate("node => !node.closest('footer')")
            visitor = page.evaluate("localStorage.getItem('forum-archive-visitor')")
            page.reload(wait_until="networkidle")
            check(1, 2)
            assert page.evaluate("localStorage.getItem('forum-archive-visitor')") == visitor
            page.goto(site_url + "/search.html?q=private-search#private-fragment", wait_until="networkidle")
            check(1, 3)
            page.evaluate("url => fetch(url).then(response => response.text())", args.base_path + "/page-2.html")
            check(1, 3)
            page.goto(site_url + "/posts/101.html", wait_until="networkidle")
            check(1, 4)
            tab = context.new_page()
            tab.goto(site_url + "/", wait_until="networkidle")
            assert counters.counts()["total"] == {"uv": 1, "pv": 5}
            tab.close()
            other = browser.new_context()
            other.new_page().goto(site_url + "/", wait_until="networkidle")
            assert counters.counts()["total"] == {"uv": 2, "pv": 6}
            other.close()
            page.evaluate("localStorage.clear()")
            page.reload(wait_until="networkidle")
            check(3, 7)
            blocked = browser.new_context()
            blocked.add_init_script("Object.defineProperty(window, 'localStorage', {get() {throw new Error('storage disabled');}})")
            blocked.new_page().goto(site_url + "/", wait_until="networkidle")
            assert counters.counts()["total"] == {"uv": 3, "pv": 8}
            blocked.close()
            page.goto(f"http://localhost:{server.server_port}" + args.base_path + "/", wait_until="networkidle")
            assert page.locator("[data-analytics-counts]").is_hidden()
            assert counters.counts()["total"] == {"uv": 3, "pv": 8}
            page.goto(site_url + "/", wait_until="networkidle")
            page.set_viewport_size({"width": 390, "height": 844})
            assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
            assert page.locator("[data-analytics-counts]").is_visible()
            assert page.locator("[data-analytics-counts]").bounding_box()["y"] < 844
            page.screenshot(path=str(screenshots / "analytics-mobile.png"))
            # Fail the optional endpoint while continuing to serve articles.
            context.route(endpoint, lambda route: route.abort())
            page.reload(wait_until="networkidle")
            assert page.locator("[data-analytics-counts]").is_hidden()
            assert page.locator(".article-row").count() > 0
            assert not errors, errors
            browser.close()
            print(f"Analytics browser checks passed: {site_url} (real CORS, UV/PV, storage, offline, mobile, preview exclusion)")
        finally:
            for item in (api, server):
                item.shutdown()
                item.server_close()
            for thread in threads:
                thread.join()


if __name__ == "__main__":
    main()
