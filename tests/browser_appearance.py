"""Check themes, hourly countdown and sidebar using temporary local articles only."""

import argparse
from dataclasses import replace
from datetime import datetime, timezone
from functools import partial
from pathlib import Path
import sys
import tempfile
from threading import Thread as ServerThread

from playwright.sync_api import sync_playwright

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

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
    parser.add_argument("--http-origin", action="store_true")
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    screenshot_dir = root / ".cache/screenshots"
    screenshot_dir.mkdir(parents=True, exist_ok=True)
    frozen = datetime(2026, 11, 3, 4, 31, 50, tzinfo=timezone.utc)
    with tempfile.TemporaryDirectory() as temporary, sync_playwright() as playwright:
        temporary = Path(temporary)
        settings = Settings.load(root / "config.toml")
        settings = replace(settings, root=temporary, filters=TitleFilter([]),
                           site=replace(settings.site, page_size=4, analytics_endpoint=""),
                           database=temporary / "archive.sqlite3", output=temporary / "site")
        with Store(settings, frozen) as store:
            for index in range(8):
                title = ("把旧电脑变成一台安静的家庭服务器", "最近读到的一本书，以及几个还没有想明白的问题",
                         "一个不用安装应用的简单记账办法", "你是怎样安排每周的阅读时间的？")[index % 4]
                body = '<p>从日常的阅读与讨论开始，记下值得留下的思考。</p>' * 10
                body += '<pre><code>记录 → 整理 → 回顾</code></pre><blockquote>留一点时间，慢慢读。</blockquote>'
                store.discover(Thread(str(index + 101), settings.source.list_url, title, frozen.isoformat()), frozen)
                store.save_post(str(index + 101), Post(title, frozen.isoformat(), body, "从日常的阅读与讨论开始，记下值得留下的思考。", 0), frozen, False)
            store.set("last_success_at", frozen.isoformat())
        build(settings, now=frozen, base_path="/last30days")
        handler = type("TestHandler", (MountedHandler,), {"base_path": "/last30days"})
        server = ArchiveServer(("127.0.0.1", 0), partial(handler, output=settings.output))
        thread = ServerThread(target=server.serve_forever, daemon=True)
        thread.start()
        actual_origin = f"http://127.0.0.1:{server.server_port}"
        origin = "http://archive.test" if args.http_origin else actual_origin
        site_url = origin + "/last30days"
        try:
            chrome = Path(r"C:\Program Files\Google\Chrome\Application\chrome.exe")
            browser = playwright.chromium.launch(headless=True, **({"executable_path": str(chrome)} if chrome.is_file() else {}))
            context = browser.new_context(viewport={"width": 1440, "height": 1000}, color_scheme="light", timezone_id="Asia/Kolkata")

            def route_request(route):
                if route.request.url.startswith(origin + "/"):
                    if args.http_origin:
                        route.fulfill(response=route.fetch(url=route.request.url.replace(origin, actual_origin, 1)))
                    else:
                        route.continue_()
                else:
                    route.abort()

            context.route("**/*", route_request)
            page = context.new_page()
            errors = []
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.on("console", lambda message: errors.append(message.text) if "Content Security Policy" in message.text else None)
            page.clock.install(time=frozen)
            page.clock.pause_at(frozen)
            page.goto(site_url + "/", wait_until="networkidle")
            if args.http_origin:
                assert not page.evaluate("isSecureContext")
            assert page.get_by_text("查看来源版块", exact=True).count() == 0
            countdown = page.locator("[data-update-countdown]")
            assert countdown.is_visible()
            assert countdown.locator("time").inner_text() == "00:10"
            assert "北京时间 12:32" in countdown.get_attribute("title")
            page.clock.fast_forward(10000)
            assert countdown.locator("time").inner_text() == "60:00"
            page.clock.fast_forward(1000)
            assert countdown.locator("time").inner_text() == "59:59"
            page.clock.set_fixed_time(datetime(2026, 11, 3, 15, 59, 59, tzinfo=timezone.utc))
            page.clock.fast_forward(1000)
            assert "北京时间 00:32" in countdown.get_attribute("title")

            root_node = page.locator("html")
            assert root_node.get_attribute("data-mode") == "light"
            assert root_node.get_attribute("data-skin") == "lilac"
            backgrounds = set()
            for skin in ("lilac", "sage", "sky", "sand"):
                button = page.locator(f'[data-skin-choice="{skin}"]')
                button.click()
                assert button.get_attribute("aria-pressed") == "true"
                assert root_node.get_attribute("data-skin") == skin
                backgrounds.add(page.evaluate("getComputedStyle(document.body).backgroundColor"))
            assert len(backgrounds) == 4
            page.locator('[data-skin-choice="sage"]').click()
            page.reload(wait_until="networkidle")
            assert root_node.get_attribute("data-skin") == "sage"
            assert page.locator('[data-skin-choice="sage"]').get_attribute("aria-pressed") == "true"
            page.screenshot(path=str(screenshot_dir / "appearance-sage.png"))
            page.get_by_role("button", name="深色", exact=True).click()
            assert root_node.get_attribute("data-mode") == "dark"
            assert page.locator(".skin-options").is_hidden()
            dark_background = page.evaluate("getComputedStyle(document.body).backgroundColor")
            page.screenshot(path=str(screenshot_dir / "appearance-dark.png"))
            page.goto(site_url + "/posts/101.html", wait_until="networkidle")
            assert root_node.get_attribute("data-mode") == "dark"
            assert page.evaluate("getComputedStyle(document.body).backgroundColor") == dark_background
            assert page.locator(".prose").is_visible()
            page.get_by_role("button", name="浅色", exact=True).click()
            assert root_node.get_attribute("data-skin") == "sage"
            assert page.locator(".skin-options").is_visible()
            page.goto(site_url + "/search.html?q=阅读", wait_until="networkidle")
            assert root_node.get_attribute("data-mode") == "light"
            assert root_node.get_attribute("data-skin") == "sage"
            assert page.locator("#search-results .article-row").count() > 0
            page.set_viewport_size({"width": 390, "height": 844})
            assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
            assert countdown.is_visible()
            assert page.get_by_role("button", name="深色", exact=True).is_visible()
            page.screenshot(path=str(screenshot_dir / "appearance-mobile.png"))
            page.get_by_role("button", name="深色", exact=True).click()
            assert root_node.get_attribute("data-mode") == "dark"
            page.get_by_role("button", name="浅色", exact=True).focus()
            page.keyboard.press("Enter")
            assert root_node.get_attribute("data-mode") == "light"
            page.get_by_role("button", name="杏纸", exact=True).focus()
            page.keyboard.press("Space")
            assert root_node.get_attribute("data-skin") == "sand"
            assert not errors, errors

            blocked = browser.new_context(color_scheme="light")
            blocked.add_init_script("Object.defineProperty(window, 'localStorage', {get() {throw new Error('storage disabled');}})")
            blocked.route("**/*", route_request)
            blocked_page = blocked.new_page()
            blocked_page.goto(site_url + "/", wait_until="networkidle")
            blocked_page.get_by_role("button", name="深色", exact=True).click()
            assert blocked_page.locator("html").get_attribute("data-mode") == "dark"
            assert blocked_page.locator("[data-update-countdown]").is_visible()
            blocked.close()
            system_dark = browser.new_context(color_scheme="dark")
            system_dark.route("**/*", route_request)
            system_page = system_dark.new_page()
            system_page.goto(site_url + "/", wait_until="networkidle")
            assert system_page.locator("html").get_attribute("data-mode") == "dark"
            system_page.get_by_role("button", name="浅色", exact=True).click()
            system_page.reload(wait_until="networkidle")
            assert system_page.locator("html").get_attribute("data-mode") == "light"
            system_dark.close()
            browser.close()
        finally:
            server.shutdown()
            server.server_close()
            thread.join()
    print("Appearance checks passed: countdown, midnight, light skins, dark mode, persistence, storage disabled, mobile and CSP")


if __name__ == "__main__":
    main()
