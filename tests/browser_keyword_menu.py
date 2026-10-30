"""Optional Playwright check against a running demo preview; never submits to GitHub."""

import argparse
import json
from pathlib import Path
import sys
from urllib.parse import parse_qs, quote, urlsplit

from playwright.sync_api import sync_playwright


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="http://127.0.0.1:8765/")
    parser.add_argument("--http-origin", action="store_true", help="以非 localhost 的普通 HTTP 来源测试，无 Web Crypto")
    args = parser.parse_args()
    origin = urlsplit(args.url)
    browser_url = "http://archive.test/" if args.http_origin else args.url
    browser_origin = urlsplit(browser_url)
    output = Path(".cache/screenshots")
    output.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as playwright:
        chrome = Path(r"C:\Program Files\Google\Chrome\Application\chrome.exe")
        options = {"executable_path": str(chrome)} if chrome.is_file() else {}
        browser = playwright.chromium.launch(headless=True, **options)
        context = browser.new_context(viewport={"width": 1440, "height": 1050})
        # A local check must not load third-party images or navigate to GitHub.
        def route_request(route):
            requested = urlsplit(route.request.url)
            if requested.netloc == browser_origin.netloc:
                if args.http_origin:
                    local_url = origin._replace(path=requested.path, query=requested.query, fragment="").geturl()
                    route.fulfill(response=route.fetch(url=local_url))
                else:
                    route.continue_()
            else:
                route.fulfill(status=200, content_type="text/html", body="External navigation intercepted by local test")

        context.route("**/*", route_request)
        page = context.new_page()
        errors = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.on("console", lambda message: errors.append(message.text) if "Content Security Policy" in message.text else None)
        page.goto(browser_url, wait_until="networkidle")
        if args.http_origin:
            assert page.evaluate("!window.isSecureContext && !window.crypto?.subtle")
        page.locator(".article-info h2 a").first.click()
        paragraph = page.locator(".prose p").first
        box = paragraph.bounding_box()
        page.mouse.move(box["x"] + 1, box["y"] + 12)
        page.mouse.down()
        page.mouse.move(box["x"] + 250, box["y"] + 12, steps=15)
        page.mouse.up()
        page.wait_for_selector("#keyword-menu .keyword-submit:not([hidden])", timeout=5000)
        menu = page.locator("#keyword-menu")
        word = page.evaluate("getSelection().toString().trim().replace(/\\s+/gu, ' ')")
        assert word

        def check_link(expected):
            link = urlsplit(menu.locator(".keyword-submit").get_attribute("href"))
            assert link.scheme == "https" and link.netloc == "github.com"
            assert link.path == f"/{menu.get_attribute('data-repository')}/new/{quote(menu.get_attribute('data-branch'), safe='')}"
            query = parse_qs(link.query)
            assert query['filename'][0].startswith('blacklist/block-') and query['filename'][0].endswith('.txt')
            assert query['value'] == [expected + '\n']
            assert query['message'] == ['Add blocked keyword']
            return menu.locator('.keyword-submit').get_attribute('href')

        def click_and_check_file(expected):
            expected_url = check_link(expected)
            with page.expect_popup() as popup:
                menu.locator(".keyword-submit").click()
            popup.value.wait_for_load_state()
            assert popup.value.url == expected_url
            popup.value.close()
            page.bring_to_front()

        check_link(word)
        assert menu.inner_text().strip() == "Block"
        page.screenshot(path=str(output / "keyword-menu-desktop.png"))
        click_and_check_file(word)
        page.keyboard.press("Escape")
        assert menu.is_hidden()

        def select_text(text):
            page.evaluate("""text => {
                let node = document.getElementById('selection-test');
                if (!node) {
                    node = document.createElement('p');
                    node.id = 'selection-test';
                    document.getElementById('main').prepend(node);
                }
                node.textContent = text;
                const range = document.createRange();
                range.selectNodeContents(node);
                getSelection().removeAllRanges();
                getSelection().addRange(range);
                node.dispatchEvent(new PointerEvent('pointerup', {bubbles: true, button: 0}));
            }""", text)

        select_text("色情")
        assert menu.is_hidden()
        assert menu.locator(".keyword-submit").is_hidden()
        assert menu.locator(".keyword-submit").get_attribute("href") is None
        select_text("长" * 81)
        assert menu.is_hidden()
        assert menu.locator(".keyword-submit").is_hidden()
        select_text('读者 "建议" <script>\n第二行')
        page.wait_for_selector("#keyword-menu .keyword-submit:not([hidden])")
        check_link('读者 "建议" <script> 第二行')
        assert page.locator("#keyword-menu script").count() == 0
        click_and_check_file('读者 "建议" <script> 第二行')
        filenames = set()
        for vector in ("abc", "x" * 80, "测" * 80, "😀" * 80, 'a&b=1?x#片段%$`'):
            select_text(vector)
            page.wait_for_selector("#keyword-menu .keyword-submit:not([hidden])", timeout=5000)
            filename = parse_qs(urlsplit(check_link(vector)).query)['filename'][0]
            assert filename not in filenames
            filenames.add(filename)
            click_and_check_file(vector)
        select_text("控制\u200b字符")
        assert menu.is_hidden()
        page.set_viewport_size({"width": 390, "height": 844})
        page.wait_for_timeout(150)  # Let resize/scroll events settle before a new selection.
        select_text("家庭服务器")
        page.wait_for_selector("#keyword-menu .keyword-submit:not([hidden])")
        bounds = menu.bounding_box()
        assert bounds["x"] >= 0 and bounds["x"] + bounds["width"] <= 390
        assert bounds["y"] >= 0 and bounds["y"] + bounds["height"] <= 844
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
        page.screenshot(path=str(output / "keyword-menu-mobile.png"))
        page.keyboard.press("Escape")
        assert menu.is_hidden()
        assert not errors, errors
        browser.close()
    print(json.dumps({"http_origin": args.http_origin, "mouse_selection": "pass", "github_new_file": "pass", "prefilled_keyword": "pass", "unique_filename": "pass", "duplicate_word": "pass", "length_limit": "pass", "mobile": "pass", "submitted_to_github": False}))


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    main()
