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
    parser.add_argument("--http-origin", action="store_true")
    args = parser.parse_args()
    base_path = args.base_path
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
        browser_origin = "http://archive.test" if args.http_origin else origin
        site_url = browser_origin + base_path
        try:
            chrome = Path(r"C:\Program Files\Google\Chrome\Application\chrome.exe")
            browser = playwright.chromium.launch(headless=True, **({"executable_path": str(chrome)} if chrome.is_file() else {}))
            context = browser.new_context(viewport={"width": 1440, "height": 400})

            def route_request(route):
                if route.request.url.startswith(browser_origin + "/"):
                    if args.http_origin:
                        route.fulfill(response=route.fetch(url=route.request.url.replace(browser_origin, origin, 1)))
                    else:
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
                with page.expect_navigation(wait_until="networkidle"):
                    page.locator('.site-search button').click()
                page.wait_for_function("() => document.getElementById('search-status').textContent !== '正在加载搜索…'")

            def no_page_controls():
                assert page.locator(".pagination, .page-jump, .page-count, .page-number, #search-page-count").count() == 0

            def scroll_next(count):
                page.locator(".feed-loader").scroll_into_view_if_needed()
                page.wait_for_function("count => document.querySelectorAll('.article-list > .article-row').length >= count", arg=count)

            page.goto(site_url + "/", wait_until="networkidle")
            if args.http_origin:
                assert not page.evaluate("isSecureContext")
            assert page.locator("body > .back-to-top").count() == 1
            search_box = page.locator(".site-search").bounding_box()
            topbar = page.locator(".topbar").bounding_box()
            assert search_box["width"] <= 240 and search_box["height"] <= 40
            assert abs(search_box["x"] + search_box["width"] - topbar["x"] - topbar["width"]) < 2
            assert topbar["y"] <= search_box["y"] < topbar["y"] + topbar["height"]
            no_page_controls()
            assert page.locator(".back-to-top").is_hidden()
            assert page.locator(".new-badge").count() == 1
            assert page.locator(".article-row").first.locator(".new-badge").inner_text() == "NEW"
            assert page.locator(".article-row").nth(1).locator(".new-badge").count() == 0
            page.evaluate("window.scrollTo({top: 120, behavior: 'instant'})")
            page.wait_for_function("() => scrollY > 0 && scrollY < 300")
            page.locator(".back-to-top").wait_for(state="visible", timeout=2000)
            button = page.locator(".back-to-top").bounding_box()
            assert 0 <= 1440 - button["x"] - button["width"] <= 40
            assert 0 <= 400 - button["y"] - button["height"] <= 40
            assert 0 <= button["y"] and button["y"] + button["height"] <= 400
            page.locator(".back-to-top").click()
            page.wait_for_function("() => scrollY === 0")
            assert page.locator(".back-to-top").is_hidden()
            page.goto(site_url + "/page-4.html", wait_until="networkidle")
            assert urlsplit(page.url).path == base_path + "/page-4.html"
            no_page_controls()
            assert page.locator(".article-row").count() == 1
            page.goto(site_url + "/", wait_until="networkidle")
            assert urlsplit(page.url).path == base_path + "/"
            page.locator(f'.month-link[href="{base_path}/archive/2026-10.html"]').click()
            assert page.locator(".new-badge").count() == 1
            page.goto(site_url + "/archive/2026-10-3.html", wait_until="networkidle")
            assert urlsplit(page.url).path == base_path + "/archive/2026-10-3.html"
            no_page_controls()
            archive_url = page.url
            with page.expect_popup() as opened:
                page.locator('.article-info h2 a').first.click()
            article = opened.value
            article.wait_for_load_state("networkidle")
            assert urlsplit(article.url).path == base_path + "/posts/105.html"
            assert page.url == archive_url
            assert article.evaluate("window.opener === null")
            article.close()
            search("标题专属")
            assert page.locator("#search-results .article-row").count() == 1
            assert "标题专属" in page.locator("#search-results h2").inner_text()
            assert page.locator("#search-results .new-badge").count() == 1
            search_url = page.url
            with page.expect_popup() as opened:
                page.locator("#search-results h2 a").click()
            article = opened.value
            article.wait_for_load_state("networkidle")
            assert urlsplit(article.url).path == base_path + "/posts/101.html"
            assert article.locator(".post-heading .new-badge").inner_text() == "NEW"
            assert page.url == search_url
            assert article.evaluate("window.opener === null")
            article.close()
            search("全文目标")
            assert "7 篇" in page.locator("#search-status").inner_text()
            assert page.locator("#search-results .article-row").count() == 2
            page.goto(site_url + "/search.html?q=全文目标&page=4", wait_until="networkidle")
            assert parse_qs(urlsplit(page.url).query) == {"q": ["全文目标"], "page": ["4"]}
            no_page_controls()
            assert page.locator("#search-results .article-row").count() == 1
            assert "<img" in page.locator("#search-results h2").inner_text()
            assert page.locator("#search-results .new-badge").count() == 1
            assert page.locator("#search-results img").count() == 0
            assert not page.evaluate("Boolean(window.badSearch)")
            page.go_back(wait_until="networkidle")
            assert parse_qs(urlsplit(page.url).query) == {"q": ["全文目标"]}
            assert page.locator("#search-results h2 a").first.get_attribute("href") == base_path + "/posts/101.html"
            for keyword in ("图片地址词", "属性词", "屏蔽词", "不存在的关键词"):
                search(keyword)
                assert page.locator("#search-empty").is_visible()
                no_page_controls()
                assert page.locator(".feed-loader").is_hidden()
            search(" ＳＥＡＲＣＨＴＡＧ ")
            assert "6 篇" in page.locator("#search-status").inner_text()
            scroll_next(4)
            assert parse_qs(urlsplit(page.url).query)["q"] == ["ＳＥＡＲＣＨＴＡＧ"]
            page.goto(site_url + "/search.html?q=全文目标&page=999", wait_until="networkidle")
            assert page.locator("#search-results h2 a").first.get_attribute("href") == base_path + "/posts/107.html"
            page.goto(site_url + "/search.html?q=全文目标&page=bad", wait_until="networkidle")
            assert page.locator("#search-results h2 a").first.get_attribute("href") == base_path + "/posts/101.html"

            attempts = []

            def fail_once(route):
                attempts.append(route.request.url)
                if len(attempts) == 1:
                    route.fulfill(status=503, body="Try again")
                else:
                    route.fallback()

            context.route("**/page-2.html", fail_once)
            page.goto(site_url + "/", wait_until="networkidle")
            page.locator(".feed-loader").scroll_into_view_if_needed()
            page.locator(".feed-retry").wait_for(state="visible")
            assert page.locator(".article-row").count() == 2
            assert page.locator(".feed-loader").get_attribute("data-next-page") == base_path + "/page-2.html"
            page.locator(".feed-retry").click()
            page.wait_for_function("() => document.querySelectorAll('.article-row').length >= 4")
            scroll_next(6)
            scroll_next(7)
            assert len(attempts) == 2
            assert page.locator(".feed-status").inner_text() == "到底啦，这是我的底线了。"
            links = page.locator(".article-info h2 a").evaluate_all("nodes => nodes.map(node => node.getAttribute('href'))")
            assert links == [f"{base_path}/posts/{number}.html" for number in range(101, 108)]
            assert page.locator(".new-badge").count() == 2
            assert page.locator(".feed-loader").get_attribute("data-next-page") == ""
            assert page.locator('.article-info h2 a:not([target="_blank"])').count() == 0
            no_page_controls()
            assert urlsplit(page.url).path == base_path + "/"
            assert page.locator(".back-to-top").is_visible()
            page.locator(".back-to-top").click()
            page.wait_for_function("() => scrollY === 0")
            assert page.locator(".back-to-top").is_hidden()
            context.unroute("**/page-2.html", fail_once)

            page.goto(site_url + "/archive/2026-10.html", wait_until="networkidle")
            scroll_next(4)
            assert page.locator(".feed-loader").get_attribute("data-next-page") == base_path + "/archive/2026-10-3.html"
            scroll_next(6)
            assert page.locator(".feed-status").inner_text() == "到底啦，这是我的底线了。"
            assert page.locator(".article-row").count() == 6
            assert urlsplit(page.url).path == base_path + "/archive/2026-10.html"
            no_page_controls()

            search("全文目标")
            scroll_next(4)
            scroll_next(6)
            scroll_next(7)
            assert page.locator(".feed-status").inner_text() == "到底啦，这是我的底线了。"
            assert page.locator("#search-results .new-badge").count() == 2
            assert page.locator("#search-results img").count() == 0
            assert page.locator('#search-results h2 a:not([target="_blank"])').count() == 0
            no_page_controls()
            page.goto(site_url + "/search.html?q=全文目标&page=2", wait_until="networkidle")
            assert parse_qs(urlsplit(page.url).query) == {"q": ["全文目标"], "page": ["2"]}
            assert page.locator("#search-results h2 a").first.get_attribute("href") == base_path + "/posts/103.html"
            page.goto(site_url + "/search.html?q=全文目标", wait_until="networkidle")
            output = root / ".cache/screenshots"
            output.mkdir(parents=True, exist_ok=True)
            page.screenshot(path=str(output / "search-desktop.png"), full_page=True)
            page.set_viewport_size({"width": 390, "height": 844})
            assert page.locator(".site-search").bounding_box()["width"] <= 210
            assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
            no_page_controls()
            assert page.locator(".new-badge").first.is_visible()
            scroll_next(4)
            scroll_next(6)
            scroll_next(7)
            page.locator(".feed-loader").scroll_into_view_if_needed()
            page.locator(".back-to-top").wait_for(state="visible")
            button = page.locator(".back-to-top").bounding_box()
            assert 0 <= button["x"] and button["x"] + button["width"] <= 390
            assert 0 <= button["y"] and button["y"] + button["height"] <= 844
            assert 0 <= 390 - button["x"] - button["width"] <= 24
            assert 0 <= 844 - button["y"] - button["height"] <= 24
            page.screenshot(path=str(output / "search-mobile.png"), full_page=True)
            search("   ")
            assert "请输入关键词" in page.locator("#search-status").inner_text()
            no_page_controls()
            assert page.locator(".feed-loader").is_hidden()
            context.route("**/assets/search-index.js*", lambda route: route.fulfill(content_type="text/javascript", body=""))
            page.goto(site_url + "/search.html?q=全文目标", wait_until="networkidle")
            assert "搜索数据加载失败" in page.locator("#search-status").inner_text()
            assert not errors, errors
            browser.close()
        finally:
            server.shutdown()
            server.server_close()
            thread.join()
    print("Infinite scroll: filters, order, retry, months, queries, new tabs, back-to-top, CSP and mobile passed")


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    main()
