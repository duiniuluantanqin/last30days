"""Optional Chrome integration check using temporary local articles only."""

import argparse
from dataclasses import replace
from datetime import datetime, timedelta
from functools import partial
from http.server import ThreadingHTTPServer
from pathlib import Path
import sys
import tempfile
from threading import Thread as ServerThread
from urllib.parse import parse_qs, urlsplit
from zoneinfo import ZoneInfo

from playwright.sync_api import sync_playwright

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from forum_archive.config import Settings, TitleFilter
from forum_archive.site import ArchiveHandler, build
from forum_archive.source import Post, Thread
from forum_archive.store import Store


class QuietHandler(ArchiveHandler):
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
    parser.add_argument("--base-path", default="")
    base_path = parser.parse_args().base_path
    root = Path(__file__).resolve().parents[1]
    now = datetime(2026, 10, 29, 12, tzinfo=ZoneInfo("Asia/Shanghai"))
    with tempfile.TemporaryDirectory() as temporary, sync_playwright() as playwright:
        settings = Settings.load(root / "config.toml")
        settings = replace(settings, root=Path(temporary), filters=TitleFilter(["屏蔽词"]),
                           site=replace(settings.site, page_size=2),
                           database=Path(temporary) / "archive.sqlite3", output=Path(temporary) / "site")
        with Store(settings, now) as store:
            for index in range(8):
                title = f"SearchTag 标题{index}"
                if index == 0:
                    title += " 标题专属"
                if index == 6:
                    title = '<img src=x onerror="window.badSearch=true"> & 特殊标题'
                if index == 7:
                    title = "屏蔽词"
                date = now - timedelta(days=29 if index == 6 else index)
                body = "<p>" + "普通文字" * 100 + '全文<span>目标</span></p><img src="https://cdn.example/图片地址词.jpg" alt="属性词">'
                store.discover(Thread(str(101 + index), settings.source.list_url, title, date.isoformat()), now)
                store.save_post(str(101 + index), Post(title, date.isoformat(), body, "简短摘要", 1), now, False)
            store.set("new_article_ids", '["101", "107"]')
        build(settings, now=now, base_path=base_path)
        handler = type("MountedHandler", (QuietHandler,), {"base_path": base_path})
        server = ThreadingHTTPServer(("127.0.0.1", 0), partial(handler, output=settings.output))
        thread = ServerThread(target=server.serve_forever, daemon=True)
        thread.start()
        origin = f"http://127.0.0.1:{server.server_port}"
        site_url = origin + base_path
        try:
            chrome = Path(r"C:\Program Files\Google\Chrome\Application\chrome.exe")
            browser = playwright.chromium.launch(headless=True, **({"executable_path": str(chrome)} if chrome.is_file() else {}))
            context = browser.new_context(viewport={"width": 1440, "height": 1050})

            def route_request(route):
                if route.request.url.startswith(origin + "/"):
                    route.continue_()
                else:
                    route.abort()

            context.route("**/*", route_request)
            page = context.new_page()
            errors = []
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.on("console", lambda message: errors.append(message.text) if "Content Security Policy" in message.text else None)

            def search(keyword):
                page.locator('.site-search input[name="q"]').fill(keyword)
                page.locator('.site-search button').click()
                page.wait_for_url("**/search.html*")
                page.wait_for_function("document.getElementById('search-status').textContent !== '正在加载搜索…'")

            def jump(number):
                page.locator('.page-jump input').fill(str(number))
                page.locator('.page-jump button').click()
                page.wait_for_load_state("networkidle")

            def current(number):
                assert page.locator('[aria-current="page"]').inner_text() == str(number)

            page.goto(site_url + "/", wait_until="networkidle")
            search_box = page.locator(".site-search").bounding_box()
            topbar = page.locator(".topbar").bounding_box()
            assert search_box["width"] <= 240 and search_box["height"] <= 40
            assert abs(search_box["x"] + search_box["width"] - topbar["x"] - topbar["width"]) < 2
            assert topbar["y"] <= search_box["y"] < topbar["y"] + topbar["height"]
            current(1)
            assert page.locator(".new-badge").count() == 1
            assert page.locator(".article-row").first.locator(".new-badge").inner_text() == "NEW"
            assert page.locator(".article-row").nth(1).locator(".new-badge").count() == 0
            jump(4)
            assert urlsplit(page.url).path == base_path + "/page-4.html"
            current(4)
            assert page.locator(".article-row").count() == 1
            jump(1)
            assert urlsplit(page.url).path == base_path + "/"
            page.locator(f'.month-link[href="{base_path}/archive/2026-10.html"]').click()
            assert page.locator(".new-badge").count() == 1
            jump(3)
            assert urlsplit(page.url).path == base_path + "/archive/2026-10-3.html"
            current(3)
            page.locator('.article-info h2 a').first.click()
            search("标题专属")
            assert page.locator("#search-results .article-row").count() == 1
            assert "标题专属" in page.locator("#search-results h2").inner_text()
            assert page.locator("#search-results .new-badge").count() == 1
            page.locator("#search-results h2 a").click()
            assert urlsplit(page.url).path == base_path + "/posts/101.html"
            assert page.locator(".post-heading .new-badge").inner_text() == "NEW"
            search("全文目标")
            assert "7 篇" in page.locator("#search-status").inner_text()
            assert page.locator("#search-results .article-row").count() == 2
            jump(4)
            assert parse_qs(urlsplit(page.url).query) == {"q": ["全文目标"], "page": ["4"]}
            current(4)
            assert page.locator("#search-results .article-row").count() == 1
            assert "<img" in page.locator("#search-results h2").inner_text()
            assert page.locator("#search-results .new-badge").count() == 1
            assert page.locator("#search-results img").count() == 0
            assert not page.evaluate("Boolean(window.badSearch)")
            assert page.locator('.page-jump input').get_attribute("max") == "4"
            page.locator('.page-jump input').fill("5")
            page.locator('.page-jump button').click()
            assert not page.locator('.page-jump input').evaluate("node => node.validity.valid")
            assert parse_qs(urlsplit(page.url).query)["page"] == ["4"]
            page.go_back(wait_until="networkidle")
            current(1)
            for keyword in ("图片地址词", "属性词", "屏蔽词", "不存在的关键词"):
                search(keyword)
                assert page.locator("#search-empty").is_visible()
                assert page.locator(".pagination").is_hidden()
            search(" ＳＥＡＲＣＨＴＡＧ ")
            assert "6 篇" in page.locator("#search-status").inner_text()
            page.locator('.pagination a.page-number', has_text="2").click()
            page.wait_for_load_state("networkidle")
            current(2)
            assert parse_qs(urlsplit(page.url).query)["q"] == ["ＳＥＡＲＣＨＴＡＧ"]
            page.goto(site_url + "/search.html?q=全文目标&page=999", wait_until="networkidle")
            current(4)
            page.goto(site_url + "/search.html?q=全文目标&page=bad", wait_until="networkidle")
            current(1)
            output = root / ".cache/screenshots"
            output.mkdir(parents=True, exist_ok=True)
            page.screenshot(path=str(output / "search-desktop.png"), full_page=True)
            page.set_viewport_size({"width": 390, "height": 844})
            assert page.locator(".site-search").bounding_box()["width"] <= 210
            assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
            assert page.locator(".new-badge").is_visible()
            page.screenshot(path=str(output / "search-mobile.png"), full_page=True)
            search("   ")
            assert "请输入关键词" in page.locator("#search-status").inner_text()
            assert page.locator(".pagination").is_hidden()
            context.route("**/assets/search-index.js*", lambda route: route.fulfill(content_type="text/javascript", body=""))
            page.goto(site_url + "/search.html?q=全文目标", wait_until="networkidle")
            assert "搜索数据加载失败" in page.locator("#search-status").inner_text()
            assert not errors, errors
            browser.close()
        finally:
            server.shutdown()
            server.server_close()
            thread.join()
    print("Search and pagination: title/body, filters, Unicode, escaping, links, jumps, history, CSP and mobile passed")


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    main()
