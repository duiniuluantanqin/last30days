from dataclasses import replace
from datetime import datetime, timedelta
import json
from pathlib import Path
import tempfile
import unittest
from zoneinfo import ZoneInfo

from bs4 import BeautifulSoup

from forum_archive.config import Settings, TitleFilter
from forum_archive.site import build
from forum_archive.source import Post, Thread
from forum_archive.store import Store


ROOT = Path(__file__).resolve().parents[1]
NOW = datetime(2026, 10, 29, 12, tzinfo=ZoneInfo("Asia/Shanghai"))


class SiteSearchTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        settings = Settings.load(ROOT / "config.toml")
        self.settings = replace(settings, root=root, filters=TitleFilter([]),
                                site=replace(settings.site, page_size=2),
                                database=root / "archive.sqlite3", output=root / "site")

    def save(self, thread_id, title, body, *, date=NOW):
        with Store(self.settings, NOW) as store:
            store.discover(Thread(thread_id, self.settings.source.list_url, title, date.isoformat()), NOW)
            store.save_post(thread_id, Post(title, date.isoformat(), body, "简短摘要", body.count("<img")), NOW, False)

    def index(self, release):
        script = (release / "assets/search-index.js").read_text(encoding="utf-8")
        return json.loads(script.removeprefix("window.archiveSearchIndex = ").removesuffix(";\n"))

    def test_search_data_contains_full_visible_text_without_html_attributes(self):
        body = '<p>' + "普通内容" * 100 + '全文<span>目标</span>&amp;文字</p>'
        body += '<p>防水图</p><img src="https://cdn.example/图片地址独有词.jpg" alt="属性独有词">'
        self.save("101", '标题独有词 "引号" <script>', body)
        release = build(self.settings, now=NOW)
        data = self.index(release)
        self.assertEqual(data["pageSize"], 2)
        self.assertEqual(len(data["articles"]), 1)
        article = data["articles"][0]
        self.assertEqual(article["id"], "101")
        self.assertIn("标题独有词", article["title"])
        self.assertIn("全文", article["text"])
        self.assertIn("目标 &文字", article["text"])
        self.assertGreater(len(article["text"]), 180)
        for excluded in ("防水图", "属性独有词", "图片地址独有词", "<span>", "cdn.example"):
            self.assertNotIn(excluded, article["text"])
        self.assertEqual(article["image_count"], 0)
        self.assertNotIn("body_html", article)
        self.assertNotIn("url", article)
        with Store(self.settings, NOW) as store:
            self.assertEqual(store.articles()[0]["body_html"], body)

    def test_search_excludes_removed_articles_and_invalidates_old_full_text_data(self):
        self.save("101", "保留标题", "<p>正常文字</p>")
        self.save("102", "需要下架", "<p>正文目标词</p>")
        self.save("103", "过期文章", "<p>旧正文</p>", date=NOW - timedelta(days=31))
        with Store(self.settings, NOW) as store:
            store.discover(Thread("104", self.settings.source.list_url, "未取得正文", NOW.isoformat()), NOW)
        first = build(self.settings, now=NOW)
        self.assertEqual({article["id"] for article in self.index(first)["articles"]}, {"101", "102"})
        self.settings.filters = TitleFilter(["正文目标词"])
        second = build(self.settings, now=NOW)
        self.assertEqual([article["id"] for article in self.index(second)["articles"]], ["101"])
        self.assertFalse((first / "assets/search-index.js").exists())
        self.assertTrue((second / "assets/search-index.js").is_file())

    def test_infinite_scroll_preserves_routes_without_page_controls(self):
        for number in range(25):
            self.save(str(100 + number), f"标题{number}", "<p>普通正文</p>")
        release = build(self.settings, now=NOW)
        for filename, next_page in (("page-7.html", "/page-8.html"),
                                    ("archive/2026-10-7.html", "/archive/2026-10-8.html")):
            page = BeautifulSoup((release / filename).read_text(encoding="utf-8"), "html.parser")
            self.assertFalse(page.select(".pagination, .page-count, .page-jump, .page-number"))
            self.assertEqual(page.select_one(".feed-loader")["data-next-page"], next_page)
            self.assertEqual(len(page.select(".article-row")), 2)
        last = BeautifulSoup((release / "page-13.html").read_text(encoding="utf-8"), "html.parser")
        self.assertEqual(len(last.select(".article-row")), 1)
        self.assertEqual(last.select_one(".feed-loader")["data-next-page"], "")

    def test_search_is_available_on_lists_and_posts_without_block_configuration(self):
        self.settings.site = replace(self.settings.site, github_repository="")
        self.save("101", "正常标题", "<p>正常文字</p>")
        release = build(self.settings, now=NOW)
        for filename in ("index.html", "posts/101.html", "archive/2026-10.html", "search.html"):
            page = BeautifulSoup((release / filename).read_text(encoding="utf-8"), "html.parser")
            self.assertIsNotNone(page.select_one('.site-search input[name="q"]'))
            self.assertIsNotNone(page.select_one('script[src="/assets/browse.js"]'))
            self.assertIsNone(page.select_one("#keyword-menu"))
            self.assertFalse(page.select(".pagination, .page-count, .page-jump, .page-number"))
        search = BeautifulSoup((release / "search.html").read_text(encoding="utf-8"), "html.parser")
        self.assertIsNotNone(search.select_one('script[src^="/assets/search-index.js?"]'))
        self.assertIsNotNone(search.select_one('script[src="/assets/search.js"]'))
        index = BeautifulSoup((release / "index.html").read_text(encoding="utf-8"), "html.parser")
        self.assertIsNone(index.select_one('script[src^="/assets/search-index.js"]'))

    def test_empty_archive_still_generates_search_and_one_page(self):
        release = build(self.settings, now=NOW)
        self.assertEqual(self.index(release)["articles"], [])
        self.assertTrue((release / "search.html").is_file())
        page = BeautifulSoup((release / "index.html").read_text(encoding="utf-8"), "html.parser")
        self.assertFalse(page.select(".pagination, .page-count, .page-jump, .page-number"))
        self.assertEqual(page.select_one(".feed-loader")["data-next-page"], "")

    def test_pages_prefix_covers_navigation_assets_and_pagination(self):
        for number in range(3):
            self.save(str(101 + number), f"标题{number}", '<p>普通正文</p><img src="https://cdn.example/original.jpg">')
        release = build(self.settings, now=NOW, base_path="/last30days")
        for filename in ("index.html", "page-2.html", "archive/2026-10.html", "posts/101.html", "search.html", "404.html"):
            page = BeautifulSoup((release / filename).read_text(encoding="utf-8"), "html.parser")
            self.assertEqual(page.html["data-base-path"], "/last30days")
            for node in page.select("[href], [src], [action]"):
                for attribute in ("href", "src", "action"):
                    url = node.get(attribute, "")
                    if url.startswith("/"):
                        self.assertTrue(url.startswith("/last30days/"), (filename, url))
                        self.assertTrue((release / url.removeprefix("/last30days/").split("?")[0]).exists())
            self.assertEqual(page.select_one(".site-search")["action"], "/last30days/search.html")
        for filename, next_page in (("index.html", "/last30days/page-2.html"),
                                    ("archive/2026-10.html", "/last30days/archive/2026-10-2.html")):
            page = BeautifulSoup((release / filename).read_text(encoding="utf-8"), "html.parser")
            self.assertEqual(page.select_one(".feed-loader")["data-next-page"], next_page)
        post = (release / "posts/101.html").read_text(encoding="utf-8")
        self.assertIn('src="https://cdn.example/original.jpg"', post)
        self.assertFalse(list(release.rglob("*.sqlite3")))
        default = build(self.settings, now=NOW)
        page = BeautifulSoup((default / "index.html").read_text(encoding="utf-8"), "html.parser")
        self.assertEqual(page.select_one(".brand")["href"], "/")
        self.assertIsNotNone(page.select_one('script[src="/assets/browse.js"]'))

    def test_invalid_pages_prefix_is_rejected_before_building(self):
        for path in ("last30days", "//example.com", "/../other", "/last30days?query", "/last30days/"):
            with self.subTest(path=path), self.assertRaises(ValueError):
                build(self.settings, now=NOW, base_path=path)
        self.assertFalse(self.settings.output.exists())


if __name__ == "__main__":
    unittest.main()
