from dataclasses import replace
from datetime import datetime, timedelta
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from urllib.parse import parse_qs
from zoneinfo import ZoneInfo

from bs4 import BeautifulSoup
import httpx

from forum_archive.__main__ import main
from forum_archive.config import Settings, TitleFilter
from forum_archive.crawler import Fetcher, crawl
from forum_archive.site import build, current_release
from forum_archive.source import ForumSource, ParseError, Post, Thread
from forum_archive.store import Store


ROOT = Path(__file__).resolve().parents[1]
NOW = datetime(2026, 10, 29, 12, tzinfo=ZoneInfo("Asia/Shanghai"))
SOURCE = "https://forum.example/thread0806.php?fid=7"


def listing(rows, last_page=1):
    cells = []
    for thread_id, title, created, replied in rows:
        cells.append(f'<tr><td><h3><a href="/htm_data/2610/7/{thread_id}.html">{title}</a></h3></td>'
                     f'<td><div class="f12"><span data-timestamp="{int(created.timestamp())}s">相对时间</span></div></td>'
                     f'<td><a class="f10" data-timestamp="{int(replied.timestamp())}">10-29 12:00</a></td></tr>')
    return '<html><meta charset="utf-8"><table>' + ''.join(cells) + f'</table><a href="thread0806.php?fid=7&amp;page={last_page}">末页</a></html>'


def post(title="正常讨论", body="<p>" + "正文内容" * 25 + "</p>", created=NOW):
    return (f'<html><meta charset="utf-8"><div>注册：2001-01-01</div><table><tr><td><h4 class="f16">{title}</h4>'
            f'<div id="conttpc" class="tpc_content">{body}</div></td></tr></table>'
            f'<div class="tipad">Posted: <span data-timestamp="{int(created.timestamp())}">日期</span></div>'
            '<div class="tpc_content">这是回复，不应收录</div></html>')


class ArchiveTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        original = Settings.load(ROOT / "config.toml")
        self.settings = replace(original, root=root, source=replace(original.source, list_url=SOURCE, request_interval_seconds=0, retries=0),
                                crawl=replace(original.crawl, recent_pages_per_run=1),
                                filters=TitleFilter(["色情", "av女优"], [r"广告\d+"]),
                                database=root / "data/archive.sqlite3", output=root / "site")

    def fetcher(self, routes):
        requested = []

        def handler(request):
            requested.append(str(request.url))
            target = request.url.path
            if target == "/robots.txt":
                return httpx.Response(200, text="User-agent: *\nRequest-rate: 1/10\nDisallow: /admin/", headers={"Content-Type": "text/plain"})
            if target == "/thread0806.php":
                target += "?page=" + parse_qs(request.url.query.decode()).get("page", ["1"])[0]
            result = routes.get(target)
            if result is None:
                return httpx.Response(404, text="missing")
            if isinstance(result, int):
                return httpx.Response(result, text="failed")
            return httpx.Response(200, text=result, headers={"Content-Type": "text/html; charset=utf-8"})

        client = Fetcher(self.settings)
        client.client.close()
        client.client = httpx.Client(transport=httpx.MockTransport(handler))
        return client, requested

    def run_crawl(self, routes):
        client, requested = self.fetcher(routes)
        with patch("forum_archive.crawler.time.sleep"):
            result = crawl(self.settings, fetcher=client, now=NOW)
        return result, requested

    def test_unicode_title_rules(self):
        self.assertTrue(self.settings.filters.blocks("色\u200b 情内容"))
        self.assertTrue(self.settings.filters.blocks("ＡＶ女优"))
        self.assertTrue(self.settings.filters.blocks("广告 123"))
        self.assertFalse(self.settings.filters.blocks("普通技术讨论"))

    def test_today_source_keeps_search_parameter_on_all_pages(self):
        source = ForumSource(replace(self.settings.source, list_url=SOURCE + "&search=today"))
        self.assertEqual(parse_qs(source.page_url(2).split("?", 1)[1]), {"fid": ["7"], "search": ["today"], "page": ["2"]})
        html = listing([("101", "今日主题", NOW, NOW)])
        html += '<a href="?fid=7&search=today&page=3">3</a><a href="?fid=7&page=999">普通列表</a>'
        self.assertEqual(source.parse_listing(html, 1).last_page, 3)

    def test_plain_posted_time_uses_original_post_and_handles_new_year(self):
        source = ForumSource(self.settings.source)
        for clock, reference, expected in (("10-28 19:17", NOW, "2026-10-28T19:17:00+08:00"),
                                           ("12-31 23:59", NOW.replace(year=2027, month=1, day=1), "2026-12-31T23:59:00+08:00")):
            html = post().replace(f'<span data-timestamp="{int(NOW.timestamp())}">日期</span>', clock + " 樓主")
            html += '<div class="tipad">Posted: 10-29 23:59 1樓</div>'
            result = source.parse_post(html, SOURCE, None, now=reference)
            self.assertEqual(result.published_at, expected)
        html = post().replace(f'<span data-timestamp="{int(NOW.timestamp())}">日期</span>', '10-28 19:17 樓主')
        result = source.parse_post(html, SOURCE, NOW.isoformat(), now=NOW)
        self.assertEqual(result.published_at, "2026-10-28T19:17:00+08:00")
        html = html.replace('10-28 19:17', '02-30 19:17')
        with self.assertRaises(ParseError):
            source.parse_post(html, SOURCE, None, now=NOW)
        html = post().replace(f'<span data-timestamp="{int(NOW.timestamp())}">日期</span>', '无日期')
        html += '<div class="tipad">Posted: 10-29 23:59 1樓</div>'
        with self.assertRaises(ParseError):
            source.parse_post(html, SOURCE, None, now=NOW)

    def test_today_source_migrates_same_forum_without_losing_archive(self):
        with Store(self.settings, NOW) as store:
            store.discover(Thread("100", SOURCE, "历史文章", NOW.isoformat()), NOW)
            store.save_post("100", Post("历史文章", NOW.isoformat(), "<p>正文</p>" * 50, "摘要", 0), NOW, False)
            store.set("recent_page", "12")
            store.set("backfill_page", "34")
        settings = replace(self.settings, source=replace(self.settings.source, list_url=SOURCE + "&search=today"))
        with Store(settings, NOW) as store:
            self.assertEqual(store.get("source_url"), settings.source.list_url)
            self.assertEqual(store.get("recent_page"), "2")
            self.assertEqual(store.get("backfill_page"), "34")
            self.assertEqual([row["id"] for row in store.articles()], ["100"])
        with self.assertRaises(ValueError):
            with Store(replace(settings, source=replace(settings.source, list_url=SOURCE.replace("fid=7", "fid=8") + "&search=today")), NOW):
                pass

    def test_today_crawl_skips_old_queue_and_checks_posted_time_and_sort_order(self):
        yesterday = NOW - timedelta(days=1)
        with Store(self.settings, yesterday) as store:
            store.discover(Thread("100", SOURCE, "旧待采任务", yesterday.isoformat()), yesterday)
        self.settings.source = replace(self.settings.source, list_url=SOURCE + "&search=today")
        self.settings.crawl.recent_pages_per_run = 2
        html = listing([("101", "较早发布", NOW, NOW), ("102", "较晚发布", NOW, NOW), ("103", "昨日置顶", yesterday, NOW)], 2)
        html = html.replace('&amp;page=2', '&amp;search=today&amp;page=2')
        body = lambda clock: post().replace(f'<span data-timestamp="{int(NOW.timestamp())}">日期</span>', clock + " 樓主")
        routes = {"/thread0806.php?page=1": html,
                  "/thread0806.php?page=2": listing([("104", "实际昨日发布", NOW, NOW)]),
                  "/htm_data/2610/7/101.html": body("10-29 08:17"),
                  "/htm_data/2610/7/102.html": body("10-29 11:17"),
                  "/htm_data/2610/7/104.html": body("10-28 19:17")}
        stats, requested = self.run_crawl(routes)
        self.assertEqual(stats["collected"], 2)
        self.assertEqual(stats["skipped"], 1)
        self.assertEqual(stats["list_pages"], 2)
        self.assertFalse(any("100.html" in url or "103.html" in url for url in requested))
        self.assertTrue(all(parse_qs(url.split("?", 1)[1])["search"] == ["today"] for url in requested if "thread0806" in url))
        with Store(self.settings, NOW) as store:
            self.assertEqual([row["id"] for row in store.articles()], ["102", "101"])
            self.assertEqual(json.loads(store.get("new_article_ids")), ["102", "101"])
            self.assertIsNone(store.db.execute("SELECT body_html FROM threads WHERE id='104'").fetchone()[0])
        self.settings.crawl.recent_pages_per_run = 1
        routes["/thread0806.php?page=1"] = listing([("105", "次日主题", (NOW + timedelta(days=1)), (NOW + timedelta(days=1)))])
        routes["/htm_data/2610/7/105.html"] = post(created=NOW + timedelta(days=1))
        client, requested = self.fetcher(routes)
        with patch("forum_archive.crawler.time.sleep"):
            stats = crawl(self.settings, fetcher=client, now=NOW + timedelta(days=1))
        self.assertEqual(stats["collected"], 1)
        self.assertFalse(any("100.html" in url for url in requested))
        with Store(self.settings, NOW + timedelta(days=1)) as store:
            self.assertEqual([row["id"] for row in store.articles()], ["105", "102", "101"])

    def test_today_pagination_cursor_resets_each_day_and_today_failures_retry(self):
        self.settings.source = replace(self.settings.source, list_url=SOURCE + "&search=today")
        self.settings.crawl.recent_pages_per_run = 2
        html = listing([("101", "今日主题", NOW, NOW)], 4).replace('&amp;page=4', '&amp;search=today&amp;page=4')
        routes = {f"/thread0806.php?page={page}": html for page in (1, 2, 3)}
        routes["/htm_data/2610/7/101.html"] = 503
        stats, _ = self.run_crawl(routes)
        self.assertEqual(stats["errors"], 1)
        with Store(self.settings, NOW) as store:
            self.assertEqual(store.get("recent_page"), "3")
        routes["/htm_data/2610/7/101.html"] = post()
        stats, requested = self.run_crawl(routes)
        self.assertEqual(stats["collected"], 1)
        self.assertTrue(any("page=3" in url for url in requested))
        client, requested = self.fetcher(routes)
        with patch("forum_archive.crawler.time.sleep"):
            stats = crawl(self.settings, fetcher=client, now=NOW + timedelta(days=1))
        self.assertEqual(stats["collected"], 0)
        self.assertTrue(any("page=2" in url for url in requested))
        self.assertFalse(any("page=3" in url or ".html" in url for url in requested))

    def test_configured_number_v_marker_matches_titles_and_body_text_only(self):
        filters = TitleFilter(blocked_patterns=Settings.load(ROOT / "config.toml").filters.blocked_patterns)
        for marker in ("[24V]", "[1v]", "[123V]", "[ 24 V ]", "［２４Ｖ］", "[2\u200b4V]",
                       "[24v+12p]", "[5P+4V]", "[abc24Vxyz]", "[24VA]", "［５Ｐ＋４Ｖ］"):
            with self.subTest(marker=marker):
                self.assertTrue(filters.blocks("普通讨论" + marker))
                self.assertTrue(filters.blocks("普通讨论", "<p>" + "正文" * 100 + marker + "</p>"))
        self.assertTrue(filters.blocks("普通讨论", "<p>正文</p>" * 50 + "<p>&#91;24<span>V</span>&#93;</p>"))
        self.assertTrue(filters.blocks("普通讨论", "<p>正文</p>" * 50 + "<p>&#91;5P+4<span>V</span>&#93;</p>"))
        for title in ("24V 电源", "[V]", "[24]", "[V24]", "(24V)", "[5P+4P]", "[24P+V]",
                      "[24]v", "[前缀]24v[后缀]", "[24v", "24v]"):
            with self.subTest(title=title):
                self.assertFalse(filters.blocks(title))
                self.assertFalse(filters.blocks("普通讨论", "<p>正文</p>" * 50 + "<p>" + title + "</p>"))
        self.assertFalse(filters.blocks("普通讨论", "<p>正文</p>" * 50 + '<p title="[24v+12p]">正文</p><img src="https://cdn.example/[5P+4V].jpg"><a href="https://example.com/[abc24Vxyz]">链接</a>'))

    def test_number_v_marker_removes_existing_title_and_body_matches(self):
        settings = replace(self.settings, filters=TitleFilter(blocked_patterns=Settings.load(ROOT / "config.toml").filters.blocked_patterns))
        with Store(settings, NOW) as store:
            for thread_id, title, body in (("101", "普通讨论[24v+12p]", "<p>正文</p>" * 50),
                                          ("102", "普通讨论", "<p>" + "正文" * 100 + "[5P+4<span>V]</span></p>"),
                                          ("103", "24V 电源", "<p>普通正文</p>" * 25 + "<p>[5P+4P]</p>")):
                store.discover(Thread(thread_id, SOURCE, title, NOW.isoformat()), NOW)
                store.save_post(thread_id, Post(title, NOW.isoformat(), body, "摘要", 0), NOW, False)
        old = settings.output / "release-old/posts"
        old.mkdir(parents=True)
        for thread_id in ("101", "102", "103"):
            (old / f"{thread_id}.html").write_text("旧文章", encoding="utf-8")
        release = build(settings, now=NOW)
        for thread_id in ("101", "102"):
            self.assertFalse((release / f"posts/{thread_id}.html").exists())
            self.assertFalse((old / f"{thread_id}.html").exists())
        self.assertTrue((release / "posts/103.html").is_file())
        self.assertTrue((old / "103.html").is_file())
        data = json.loads((release / "assets/search-index.js").read_text(encoding="utf-8")
                          .removeprefix("window.archiveSearchIndex = ").removesuffix(";\n"))
        self.assertEqual([article["id"] for article in data["articles"]], ["103"])
        with Store(settings, NOW) as store:
            self.assertEqual(store.removed_ids(), {"101", "102"})

    def test_short_body_filter_counts_visible_text_after_display_cleanup(self):
        rules = TitleFilter([])
        self.assertFalse(rules.blocks("只有标题，尚未获取正文"))
        self.assertFalse(rules.blocks("只有标题，尚未获取正文", None))
        for count in (0, 1, 99, 100, 101):
            body = '<p title="' + "属" * 100 + '">' + " 文\n\t" * count + '</p><img src="https://cdn.example/a.jpg" alt="' + "图" * 100 + '">'
            with self.subTest(count=count):
                self.assertEqual(rules.blocks("长标题" * 100, body), count < 100)
        for count in (99, 100):
            for suffix in ("<p>赞(88)</p>", "<p>防水<span>图</span></p>",
                           "<p>&#x9632;&#x6c34;&#x56fe;</p><p>赞（88）</p>"):
                with self.subTest(count=count, suffix=suffix):
                    self.assertEqual(rules.blocks("普通讨论", "<p>" + "&#25991;" * count + "</p>" + suffix), count < 100)
        self.assertTrue(rules.blocks("普通讨论", ""))
        self.assertTrue(rules.blocks("普通讨论", "<p>" + "文" * 95 + "</p><div>赞<span>(8</span>8)</div>"))

    def test_short_body_filter_applies_during_crawl_and_preserves_blocked_raw_body(self):
        bodies = {"101": "<p>" + "文" * 99 + "</p>",
                  "102": "<p>" + "文" * 100 + "</p>",
                  "103": "<p>" + "文" * 99 + '防水图</p><img src="https://cdn.example/a.jpg"><p>赞(88)</p>',
                  "104": "<p>" + "文" * 100 + "防水图</p><p>赞(88)</p>"}
        routes = {"/thread0806.php?page=1": listing([(key, "普通讨论", NOW, NOW) for key in bodies])}
        routes.update({f"/htm_data/2610/7/{key}.html": post(body=body) for key, body in bodies.items()})
        stats, requested = self.run_crawl(routes)
        self.assertEqual(stats["collected"], 2)
        self.assertEqual(stats["blocked"], 2)
        self.assertFalse(any("cdn.example" in url for url in requested))
        with Store(self.settings, NOW) as store:
            self.assertEqual({row["id"] for row in store.articles()}, {"102", "104"})
            self.assertEqual(set(json.loads(store.get("new_article_ids"))), {"102", "104"})
            self.assertEqual(store.removed_ids(), {"101", "103"})
            raw = store.db.execute("SELECT body_html FROM threads WHERE id='103'").fetchone()[0]
            self.assertIn("防水图", raw)
            self.assertIn("赞(88)", raw)
            self.assertIn("<img", raw)
        stats, requested = self.run_crawl(routes)
        self.assertEqual(stats["collected"], 0)
        self.assertEqual(stats["totals"], {"blocked": 2, "collected": 2})
        self.assertFalse(any(".html" in url for url in requested))

    def test_short_body_filter_removes_existing_pages_and_old_search_data(self):
        bodies = {"101": "<p>" + "文" * 99 + "防水<span>图</span></p><p>赞(88)</p>",
                  "102": "<p>" + "文" * 100 + "</p>"}
        with Store(self.settings, NOW) as store:
            for thread_id, body in bodies.items():
                store.discover(Thread(thread_id, SOURCE, "普通讨论", NOW.isoformat()), NOW)
                store.save_post(thread_id, Post("普通讨论", NOW.isoformat(), body, "摘要", 0), NOW, False)
        old = self.settings.output / "release-old"
        (old / "posts").mkdir(parents=True)
        (old / "assets").mkdir()
        (old / "assets/search-index.js").write_text("old full text", encoding="utf-8")
        for thread_id in bodies:
            (old / f"posts/{thread_id}.html").write_text("旧文章", encoding="utf-8")
        release = build(self.settings, now=NOW)
        self.assertFalse((release / "posts/101.html").exists())
        self.assertFalse((old / "posts/101.html").exists())
        self.assertFalse((old / "assets/search-index.js").exists())
        self.assertTrue((release / "posts/102.html").is_file())
        self.assertTrue((old / "posts/102.html").is_file())
        data = json.loads((release / "assets/search-index.js").read_text(encoding="utf-8")
                          .removeprefix("window.archiveSearchIndex = ").removesuffix(";\n"))
        self.assertEqual([article["id"] for article in data["articles"]], ["102"])
        with Store(self.settings, NOW) as store:
            self.assertEqual(store.removed_ids(), {"101"})
            self.assertEqual(store.db.execute("SELECT body_html FROM threads WHERE id='101'").fetchone()[0], bodies["101"])

    def test_image_count_filter_keeps_twenty_and_blocks_twenty_one_before_display_rules(self):
        image = '<img src="https://cdn.example/original.jpg">'
        for count in (0, 1, 20, 21, 30):
            with self.subTest(count=count):
                self.assertEqual(self.settings.filters.blocks("普通讨论", "<p>正文</p>" * 50 + image * count), count > 20)
        for body in ("<p>防水图</p>" + image * 21, "<p>" + "文字" * 200 + "</p>" + image * 21):
            self.assertTrue(self.settings.filters.blocks("普通讨论", body))
        self.assertFalse(self.settings.filters.blocks("普通讨论", "<p>文字 img img</p>" * 20))

    def test_image_count_filter_applies_during_crawl_without_image_downloads(self):
        image = '<img src="https://cdn.example/original.jpg">'
        routes = {"/thread0806.php?page=1": listing([("101", "二十张图片", NOW, NOW), ("102", "二十一张图片", NOW, NOW)]),
                  "/htm_data/2610/7/101.html": post(title="二十张图片", body="<p>正文</p>" * 50 + image * 20),
                  "/htm_data/2610/7/102.html": post(title="二十一张图片", body="<p>正文</p>" * 50 + image * 21)}
        stats, requests = self.run_crawl(routes)
        self.assertEqual(stats["collected"], 1)
        self.assertEqual(stats["blocked"], 1)
        self.assertFalse(any("cdn.example" in url for url in requests))
        with Store(self.settings, NOW) as store:
            self.assertEqual([article["id"] for article in store.articles()], ["101"])
            self.assertIn("102", store.removed_ids())
        stats, requests = self.run_crawl(routes)
        self.assertEqual(stats["collected"], 0)
        self.assertFalse(any(".html" in url for url in requests))

    def test_image_count_filter_removes_historical_posts_and_search_data(self):
        image = '<img src="https://cdn.example/original.jpg">'
        with Store(self.settings, NOW) as store:
            for thread_id, count in (("101", 20), ("102", 21)):
                store.discover(Thread(thread_id, SOURCE, "正常讨论", NOW.isoformat()), NOW)
                # Simulate an older database with inaccurate image_count metadata.
                store.save_post(thread_id, Post("正常讨论", NOW.isoformat(), "<p>正文</p>" * 50 + "<p>防水图</p>" + image * count, "摘要", 0), NOW, False)
        old = self.settings.output / "release-old/posts"
        old.mkdir(parents=True)
        (old / "102.html").write_text("旧文章", encoding="utf-8")
        release = build(self.settings, now=NOW)
        self.assertTrue((release / "posts/101.html").is_file())
        self.assertFalse((release / "posts/102.html").exists())
        self.assertFalse((old / "102.html").exists())
        data = json.loads((release / "assets/search-index.js").read_text(encoding="utf-8")
                          .removeprefix("window.archiveSearchIndex = ").removesuffix(";\n"))
        self.assertEqual([article["id"] for article in data["articles"]], ["101"])
        with Store(self.settings, NOW) as store:
            self.assertIn("102", store.removed_ids())
            row = store.db.execute("SELECT body_html FROM threads WHERE id='102'").fetchone()
            self.assertEqual(row["body_html"].count("<img"), 21)

    def new_ids(self, release):
        page = BeautifulSoup((release / "index.html").read_text(encoding="utf-8"), "html.parser")
        return {row.select_one("h2 a")["href"].split("/")[-1].removesuffix(".html")
                for row in page.select(".article-row") if row.select_one(".new-badge")}

    def test_new_badges_rotate_by_crawl_not_timestamp_or_build(self):
        with Store(self.settings, NOW) as store:
            store.discover(Thread("100", SOURCE, "已有文章", NOW.isoformat()), NOW)
            store.save_post("100", Post("已有文章", NOW.isoformat(), "<p>原有正文</p>" * 25, "摘要", 0), NOW, False)
        self.assertEqual(self.new_ids(build(self.settings, now=NOW)), set())
        first_routes = {"/thread0806.php?page=1": listing([("101", "本次文章", NOW, NOW)]),
                        "/htm_data/2610/7/101.html": post(title="本次文章")}
        self.run_crawl(first_routes)
        first = build(self.settings, now=NOW)
        self.assertEqual(self.new_ids(first), {"101"})
        self.assertEqual(self.new_ids(build(self.settings, now=NOW + timedelta(minutes=5))), {"101"})
        for filename in ("archive/2026-10.html", "posts/101.html"):
            page = BeautifulSoup((first / filename).read_text(encoding="utf-8"), "html.parser")
            self.assertEqual(page.select_one(".new-badge").get_text(), "NEW")
        self.assertNotIn("new-badge", (first / "posts/100.html").read_text(encoding="utf-8"))
        index = (first / "assets/search-index.js").read_text(encoding="utf-8")
        data = json.loads(index.removeprefix("window.archiveSearchIndex = ").removesuffix(";\n"))
        self.assertEqual({row["id"] for row in data["articles"] if row["is_new"]}, {"101"})
        second_routes = {"/thread0806.php?page=1": listing([("102", "下一批文章", NOW, NOW), ("101", "本次文章", NOW, NOW)]),
                         "/htm_data/2610/7/102.html": post(title="下一批文章")}
        self.run_crawl(second_routes)
        second = build(self.settings, now=NOW)
        self.assertEqual(self.new_ids(second), {"102"})
        self.assertNotIn("new-badge", (second / "posts/101.html").read_text(encoding="utf-8"))
        self.run_crawl(second_routes)
        self.assertEqual(self.new_ids(build(self.settings, now=NOW)), set())

    def test_partial_crawl_marks_only_collected_articles_as_new(self):
        old = NOW - timedelta(days=40)
        routes = {"/thread0806.php?page=1": listing([("101", "正常讨论", NOW, NOW), ("102", "失败文章", NOW, NOW),
                                                     ("103", "色情文章", NOW, NOW), ("104", "过期文章", old, NOW)]),
                  "/htm_data/2610/7/101.html": post(), "/htm_data/2610/7/102.html": 503}
        stats, _ = self.run_crawl(routes)
        self.assertEqual(stats["errors"], 1)
        self.assertEqual(self.new_ids(build(self.settings, now=NOW)), {"101"})
        routes["/htm_data/2610/7/102.html"] = post(title="重试成功")
        self.run_crawl(routes)
        self.assertEqual(self.new_ids(build(self.settings, now=NOW)), {"102"})

    def test_partial_crawl_refreshes_update_time_when_articles_are_collected(self):
        routes = {"/thread0806.php?page=1": listing([("101", "正常讨论", NOW, NOW), ("102", "失败文章", NOW, NOW)]),
                  "/htm_data/2610/7/101.html": post(), "/htm_data/2610/7/102.html": 503}
        stats, _ = self.run_crawl(routes)
        self.assertEqual(stats["collected"], 1)
        self.assertEqual(stats["errors"], 1)
        with Store(self.settings, NOW) as store:
            self.assertEqual(store.get("last_success_at"), NOW.isoformat())
        release = build(self.settings, now=NOW + timedelta(minutes=5))
        for filename in ("index.html", "archive/2026-10.html", "posts/101.html", "search.html"):
            page = BeautifulSoup((release / filename).read_text(encoding="utf-8"), "html.parser")
            self.assertEqual(page.select_one(".update-time").get_text(), "更新于 10-29 12:00")

    def test_crawl_without_successful_articles_keeps_previous_update_time_on_errors(self):
        previous = NOW - timedelta(hours=1)
        with Store(self.settings, NOW) as store:
            store.set("last_success_at", previous.isoformat())
        routes = {"/thread0806.php?page=1": listing([("101", "失败文章", NOW, NOW), ("102", "普通讨论", NOW, NOW)]),
                  "/htm_data/2610/7/101.html": 503, "/htm_data/2610/7/102.html": post(body="<p>短正文</p>")}
        stats, _ = self.run_crawl(routes)
        self.assertEqual(stats["collected"], 0)
        self.assertEqual(stats["blocked"], 1)
        self.assertEqual(stats["errors"], 1)
        with Store(self.settings, NOW) as store:
            self.assertEqual(store.get("last_success_at"), previous.isoformat())
            self.assertEqual(store.get("last_scan_at"), NOW.isoformat())
        page = BeautifulSoup((build(self.settings, now=NOW) / "index.html").read_text(encoding="utf-8"), "html.parser")
        self.assertEqual(page.select_one(".update-time").get_text(), "更新于 10-29 11:00")

    def test_error_free_crawl_without_new_articles_still_refreshes_update_time(self):
        routes = {"/thread0806.php?page=1": listing([("101", "正常讨论", NOW, NOW)]),
                  "/htm_data/2610/7/101.html": post()}
        self.run_crawl(routes)
        later = NOW + timedelta(hours=1)
        client, _ = self.fetcher(routes)
        with patch("forum_archive.crawler.time.sleep"):
            stats = crawl(self.settings, fetcher=client, now=later)
        self.assertEqual(stats["collected"], 0)
        self.assertEqual(stats["errors"], 0)
        with Store(self.settings, later) as store:
            self.assertEqual(store.get("last_success_at"), later.isoformat())

    def test_fatal_listing_failure_keeps_last_completed_new_batch(self):
        self.run_crawl({"/thread0806.php?page=1": listing([("101", "正常讨论", NOW, NOW)]),
                        "/htm_data/2610/7/101.html": post()})
        with self.assertRaises(ParseError):
            self.run_crawl({"/thread0806.php?page=1": "<html>Access denied</html>"})
        self.assertEqual(self.new_ids(build(self.settings, now=NOW)), {"101"})

    def test_body_rules_match_full_text_without_html_attributes(self):
        rules = self.settings.filters
        for body in ("<p>普通文字</p>" * 100 + "<p>色情</p>",
                     "<p>色<span>情</span></p>", "<p>&#x8272;&#x60c5;</p>",
                     "<p>ＡＶ\u200b 女优</p>", "<p>广告 123</p>"):
            with self.subTest(body=body):
                self.assertTrue(rules.blocks("正常讨论", "<p>正文</p>" * 50 + body))
        self.assertFalse(rules.blocks("正常讨论", '<p>普通正文</p>' * 25 + '<img src="https://cdn.example/色情.jpg" alt="色情">'
                                      '<a href="https://example.com/色情">普通链接</a>'))
        self.assertFalse(rules.blocks("色", "<p>情</p>" + "<p>正文</p>" * 50))

    def test_body_match_stays_blocked_between_runs(self):
        routes = {"/thread0806.php?page=1": listing([("101", "正常讨论", NOW, NOW)]),
                  "/htm_data/2610/7/101.html": post(body="<p>普通文字</p>" * 100 + "<p>色情</p>")}
        first, _ = self.run_crawl(routes)
        self.assertEqual(first["blocked"], 1)
        self.assertEqual(first["collected"], 0)
        second, requested = self.run_crawl(routes)
        self.assertEqual(second["totals"], {"blocked": 1})
        self.assertFalse(any(".html" in url for url in requested))
        release = build(self.settings, now=NOW)
        self.assertFalse((release / "posts/101.html").exists())
        self.settings.filters = TitleFilter([])
        restored = build(self.settings, now=NOW)
        self.assertTrue((restored / "posts/101.html").is_file())

    def test_new_body_rule_removes_historical_pages(self):
        with Store(self.settings, NOW) as store:
            for thread_id, body in (("101", "<p>普通文字</p>" * 100 + "<p>新增<span>词</span></p>"),
                                    ("102", "<p>保留正文</p>" * 25)):
                store.discover(Thread(thread_id, SOURCE, "正常讨论", NOW.isoformat()), NOW)
                store.save_post(thread_id, Post("正常讨论", NOW.isoformat(), body, "普通摘要", 0), NOW, False)
        previous = build(self.settings, now=NOW)
        self.settings.filters = TitleFilter(["新增词"])
        latest = build(self.settings, now=NOW)
        build(self.settings, now=NOW)
        for release in (previous, latest):
            self.assertFalse((release / "posts/101.html").exists())
            self.assertTrue((release / "posts/102.html").is_file())
        with Store(self.settings, NOW) as store:
            self.assertEqual(store.removed_ids(), {"101"})
            self.assertEqual(store.counts(), {"blocked": 1, "collected": 1})

    def test_extra_keyword_files_extend_config_blacklist(self):
        config = self.settings.root / "config.toml"
        config.write_text('[source]\nlist_url = "' + SOURCE + '"\n[filter]\nblocked_keywords = ["原有词"]\n', encoding="utf-8")
        directory = self.settings.root / "blacklist"
        directory.mkdir()
        (directory / "keywords.txt").write_text("新增词\n另一个集中词\n", encoding="utf-8")
        (directory / "two.txt").write_text("\n  另一个词  \n", encoding="utf-8")
        (directory / "README.md").write_text("不是黑名单词", encoding="utf-8")
        settings = Settings.load(config)
        self.assertTrue(settings.filters.blocks("包含原有词的标题"))
        self.assertTrue(settings.filters.blocks("包含新增词的标题"))
        self.assertTrue(settings.filters.blocks("包含另一个集中词的标题"))
        self.assertTrue(settings.filters.blocks("包含另一个词的标题"))
        self.assertFalse(settings.filters.blocks("不是黑名单词"))

    def test_selected_external_word_lists_merge_with_local_keywords(self):
        root = self.settings.root
        upstream = root / "wordlists"
        upstream.mkdir(parents=True)
        (upstream / "sw_色情类.txt").write_text("\ufeff色情测试\nDUPLICATE\n\n", encoding="utf-8")
        (upstream / "sw_广告.txt").write_text("  广告测试  \n", encoding="utf-8")
        (upstream / "sw_政治类.txt").write_text("其他分类测试\n", encoding="utf-8")
        local = root / "blacklist"
        local.mkdir()
        (local / "custom.txt").write_text("自定义词\nduplicate\n", encoding="utf-8")
        config = root / "config.toml"
        config.write_text('[source]\nlist_url = "' + SOURCE + '"\n[filter]\n'
                          'blocked_keywords = ["本地词", "ＤＵＰＬＩＣＡＴＥ"]\n'
                          'keyword_files = ["wordlists/sw_色情类.txt", '
                          '"wordlists/sw_广告.txt"]\n', encoding="utf-8")
        settings = Settings.load(config)
        for title in ("本地词", "色情测试", "广告测试", "自定义词", "duplicate"):
            self.assertTrue(settings.filters.blocks("包含" + title + "的标题"))
        self.assertFalse(settings.filters.blocks("其他分类测试"))
        self.assertEqual(settings.filters.keywords.count("duplicate"), 1)
        self.assertNotIn("", settings.filters.keywords)

    def test_missing_keyword_file_reports_its_path(self):
        config = self.settings.root / "config.toml"
        config.write_text('[source]\nlist_url = "' + SOURCE + '"\n[filter]\n'
                          'keyword_files = ["wordlists/missing.txt"]\n', encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "词库文件不存在：wordlists/missing.txt"):
            Settings.load(config)

    def test_updated_external_keywords_remove_previously_collected_article(self):
        root = self.settings.root
        config = root / "config.toml"
        config.write_text('[source]\nlist_url = "' + SOURCE + '"\n[filter]\n'
                          'keyword_files = ["upstream.txt"]\n', encoding="utf-8")
        word_file = root / "upstream.txt"
        word_file.write_text("其他词\n", encoding="utf-8")
        before = Settings.load(config)
        with Store(before, NOW) as store:
            store.discover(Thread("101", SOURCE, "新增广告词讨论", NOW.isoformat()), NOW)
            store.save_post("101", Post("新增广告词讨论", NOW.isoformat(), "<p>正文</p>" * 50, "正文", 0), NOW, False)
        first = build(before, now=NOW)
        self.assertTrue((first / "posts/101.html").is_file())
        word_file.write_text("其他词\n新增广告词\n", encoding="utf-8")
        second = build(Settings.load(config), now=NOW)
        self.assertFalse((first / "posts/101.html").exists())
        self.assertFalse((second / "posts/101.html").exists())
        self.assertNotIn("新增广告词讨论", (second / "index.html").read_text(encoding="utf-8"))

    def test_keyword_menu_has_safe_repository_configuration(self):
        self.settings.site = replace(self.settings.site, github_repository="example/archive", github_branch="feature/rules")
        self.settings.filters = TitleFilter(["原有词", "</div><script>bad()</script>"])
        release = build(self.settings, now=NOW)
        page = BeautifulSoup((release / "index.html").read_text(encoding="utf-8"), "html.parser")
        menu = page.select_one("#keyword-menu")
        self.assertIsNotNone(menu)
        self.assertEqual(menu["data-repository"], "example/archive")
        self.assertEqual(menu["data-branch"], "feature/rules")
        self.assertEqual(json.loads(menu["data-keywords"]), self.settings.filters.keywords)
        scripts = page.select("script")
        self.assertEqual([script["src"] for script in scripts], ["/assets/browse.js", "/assets/keyword-menu.js"])
        self.assertTrue((release / "assets/keyword-menu.js").is_file())
        self.assertTrue(all(not script.get_text(strip=True) for script in scripts))

    def test_merged_keyword_file_hides_previously_collected_article(self):
        with Store(self.settings, NOW) as store:
            store.discover(Thread("101", SOURCE, "新增关键词讨论", NOW.isoformat()), NOW)
            store.save_post("101", Post("新增关键词讨论", NOW.isoformat(), "<p>正文</p>" * 50, "正文", 0), NOW, False)
        first = build(self.settings, now=NOW)
        self.assertTrue((first / "posts/101.html").is_file())
        config = self.settings.root / "config.toml"
        config.write_text('[source]\nlist_url = "' + SOURCE + '"\n', encoding="utf-8")
        directory = self.settings.root / "blacklist"
        directory.mkdir()
        (directory / "new.txt").write_text("新增关键词\n", encoding="utf-8")
        updated = Settings.load(config)
        second = build(updated, now=NOW)
        self.assertFalse((second / "posts/101.html").exists())
        self.assertNotIn("新增关键词讨论", (second / "index.html").read_text(encoding="utf-8"))
        self.assertFalse((first / "posts/101.html").exists())

    def test_keyword_menu_is_absent_without_repository(self):
        self.settings.site = replace(self.settings.site, github_repository="")
        release = build(self.settings, now=NOW)
        page = BeautifulSoup((release / "index.html").read_text(encoding="utf-8"), "html.parser")
        self.assertIsNone(page.select_one("#keyword-menu"))
        self.assertEqual([script["src"] for script in page.select("script")], ["/assets/browse.js"])

    def test_real_layout_timestamp_and_forum_scope(self):
        html = listing([("123", "正常讨论", NOW, NOW)], 5)
        html += '<h3><a href="/htm_data/2610/9/999.html">其他版块</a></h3>'
        html += '<h3><a href="https://other.example/htm_data/2610/7/555.html">外部站点</a></h3>'
        result = ForumSource(self.settings.source).parse_listing(html, 1)
        self.assertEqual([t.id for t in result.threads], ["123"])
        self.assertEqual(result.threads[0].listed_at, NOW.isoformat())
        self.assertEqual(result.last_page, 5)

    def test_original_images_sanitized_without_reply_content(self):
        body = ('<p onclick="bad()">安全正文</p><script>unsafe()</script>'
                '<img iyl-data="http://a.d/placeholder.jpg" ess-data="https://cdn.example/original.jpg" onerror="bad()">'
                '<input type="image" src="//cdn.example/second.png"><img src="javascript:bad()">'
                '<a href="javascript:bad()">坏链接</a><iframe src="https://bad.example"></iframe>'
                '<video src="https://cdn.example/movie.mp4"></video>')
        result = ForumSource(self.settings.source).parse_post(post(body=body), SOURCE, None)
        clean = BeautifulSoup(result.body_html, "html.parser")
        self.assertEqual([x["src"] for x in clean.select("img")], ["https://cdn.example/original.jpg", "https://cdn.example/second.png"])
        self.assertEqual(result.image_count, 2)
        self.assertNotIn("unsafe", result.body_html)
        self.assertNotIn("onerror", result.body_html)
        self.assertNotIn("onclick", result.body_html)
        self.assertNotIn("javascript:", result.body_html)
        self.assertNotIn("这是回复", result.body_html)
        self.assertEqual(result.published_at, NOW.isoformat())

    def test_build_hides_all_images_in_existing_posts_with_marker(self):
        markers = ["防水图", "防水<span>图</span>", "&#x9632;&#x6c34;&#x56fe;",
                   "防<strong>水</strong>图", "防水<span>图以及防</span>水图"]
        with Store(self.settings, NOW) as store:
            for index, marker in enumerate(markers, 201):
                thread_id = str(index)
                body = ('<p>正文</p>' * 50 + f'<p>保留正文：{marker}</p><img src="https://cdn.example/first.jpg">'
                        '<p>后续文字也保留</p><img src="https://cdn.example/second.jpg">')
                store.discover(Thread(thread_id, SOURCE, "正常讨论", NOW.isoformat()), NOW)
                store.save_post(thread_id, Post("正常讨论", NOW.isoformat(), body, "正文摘要防水图", 2), NOW, False)
        release = build(self.settings, now=NOW)
        for index in range(201, 201 + len(markers)):
            with self.subTest(thread_id=index):
                page = BeautifulSoup((release / f"posts/{index}.html").read_text(encoding="utf-8"), "html.parser")
                self.assertEqual(page.select(".prose img"), [])
                self.assertNotIn("防水图", page.select_one(".prose").get_text())
                self.assertIn("保留正文：", page.select_one(".prose").get_text())
                self.assertIn("后续文字也保留", page.select_one(".prose").get_text())
                self.assertEqual(page.select(".image-note"), [])
        index_html = (release / "index.html").read_text(encoding="utf-8")
        self.assertNotIn("张外链图片", index_html)
        self.assertNotIn("防水图", index_html)
        with Store(self.settings, NOW) as store:
            self.assertTrue(all(article["image_count"] == 2 for article in store.articles()))
            self.assertTrue(all("防水图" in article["summary"] for article in store.articles()))

    def test_build_keeps_images_for_posts_without_marker(self):
        with Store(self.settings, NOW) as store:
            store.discover(Thread("201", SOURCE, "正常讨论", NOW.isoformat()), NOW)
            body = '<p>普通正文</p>' * 25 + '<img src="https://cdn.example/original.jpg">'
            store.save_post("201", Post("正常讨论", NOW.isoformat(), body, "普通正文", 1), NOW, False)
        release = build(self.settings, now=NOW)
        page = BeautifulSoup((release / "posts/201.html").read_text(encoding="utf-8"), "html.parser")
        self.assertEqual(page.select_one(".prose img")["src"], "https://cdn.example/original.jpg")
        self.assertIsNone(page.select_one(".image-note"))
        self.assertNotIn("图片直接从原地址加载，可能因原站限制或链接失效而无法显示。", page.get_text())
        self.assertIn("1 张外链图片", (release / "index.html").read_text(encoding="utf-8"))

    def test_build_removes_trailing_like_counts_from_existing_posts(self):
        image = '<img src="https://cdn.example/original.jpg">'
        cases = [
            ("<p>" + "保留正文" * 25 + "</p><div>赞(88)</div>", "保留正文" * 25, 0),
            ("<p>" + "保留正文" * 25 + "</p><div>赞<span>(</span><b>123</b>)</div><br> \n", "保留正文" * 25, 0),
            ("<p>" + "保留正文" * 25 + "</p><div>赞（ 0 ）</div>", "保留正文" * 25, 0),
            ("<p>" + "文" * 100 + "正文提到赞(88)，仍需保留。</p>", "文" * 100 + "正文提到赞(88)，仍需保留。", 0),
            ("<p>" + "文" * 200 + "</p>" + image + "<div>赞(88)</div>", "文" * 200, 0),
            ("<p>" + "保留正文" * 25 + "</p>" + image + "<div>赞(88)</div>", "保留正文" * 25, 1),
        ]
        with Store(self.settings, NOW) as store:
            for index, (body, _, _) in enumerate(cases, 401):
                thread_id = str(index)
                store.discover(Thread(thread_id, SOURCE, "正常讨论", NOW.isoformat()), NOW)
                summary = BeautifulSoup(body, "html.parser").get_text(" ", strip=True)[:180]
                store.save_post(thread_id, Post("正常讨论", NOW.isoformat(), body, summary, body.count("<img")), NOW, False)
        release = build(self.settings, now=NOW)
        data = json.loads((release / "assets/search-index.js").read_text(encoding="utf-8")
                          .removeprefix("window.archiveSearchIndex = ").removesuffix(";\n"))
        for index, (body, expected, remaining) in enumerate(cases, 401):
            with self.subTest(thread_id=index):
                page = BeautifulSoup((release / f"posts/{index}.html").read_text(encoding="utf-8"), "html.parser")
                self.assertEqual(page.select_one(".prose").get_text(strip=True), expected)
                self.assertEqual(len(page.select(".prose img")), remaining)
                entry = next(article for article in data["articles"] if article["id"] == str(index))
                self.assertEqual(entry["text"], expected)
                self.assertEqual(entry["summary"], expected[:180])
        with Store(self.settings, NOW) as store:
            for article in store.articles():
                self.assertEqual(article["body_html"], cases[int(article["id"]) - 401][0])

    def test_long_articles_hide_images_only_when_all_images_are_at_the_end(self):
        image = '<a href="https://cdn.example/original.jpg"><img src="https://cdn.example/original.jpg"></a>'
        cases = [
            ("<p>" + "文" * 200 + "</p>" + image, 0),
            ("<p>" + "文" * 200 + "</p><div>" + (image + "<br> \n") * 5 + "</div>", 0),
            ("<p>" + "文" * 199 + "</p>" + image, 1),
            ("<p>" + "文 " * 199 + "</p>" + image, 1),
            ("<p>" + "文" * 200 + "</p>" + image * 6, 0),
            ("<p>" + "文" * 200 + "</p>" + image * 2 + "<p>图片说明</p>", 2),
            (image + "<p>" + "文" * 200 + "</p>" + image * 2, 3),
            ("<p>" + "文" * 200 + "</p>", 0),
            ("<p>" + "文" * 100 + "</p>" + image + "<p>" + "文" * 100 + "</p>" + image, 2),
        ]
        with Store(self.settings, NOW) as store:
            for index, (body, _) in enumerate(cases, 301):
                thread_id = str(index)
                store.discover(Thread(thread_id, SOURCE, "正常讨论", NOW.isoformat()), NOW)
                store.save_post(thread_id, Post("正常讨论", NOW.isoformat(), body, "正文摘要", body.count("<img")), NOW, False)
        release = build(self.settings, now=NOW)
        listing = BeautifulSoup((release / "index.html").read_text(encoding="utf-8"), "html.parser")
        for index, (body, remaining) in enumerate(cases, 301):
            with self.subTest(thread_id=index):
                page = BeautifulSoup((release / f"posts/{index}.html").read_text(encoding="utf-8"), "html.parser")
                self.assertEqual(len(page.select(".prose img")), remaining)
                self.assertEqual(page.select_one(".prose").get_text(), BeautifulSoup(body, "html.parser").get_text())
                self.assertIsNone(page.select_one(".image-note"))
                row = listing.select_one(f'.article-info a[href="/posts/{index}.html"]').find_parent("article")
                self.assertEqual("张外链图片" in row.get_text(), bool(remaining))
                if remaining:
                    self.assertIn(f"{remaining} 张外链图片", row.get_text())
        with Store(self.settings, NOW) as store:
            for article in store.articles():
                body, _ = cases[int(article["id"]) - 301]
                self.assertEqual(article["body_html"], body)
                self.assertEqual(article["image_count"], body.count("<img"))

    def test_end_to_end_no_image_requests_and_idempotent_replay(self):
        html = listing([("101", "正常讨论", NOW, NOW), ("102", "色情帖子", NOW, NOW), ("103", "旧帖", NOW - timedelta(days=40), NOW)])
        routes = {"/thread0806.php?page=1": html,
                  "/htm_data/2610/7/101.html": post(body='<p>正常正文</p>' * 25 + '<img ess-data="https://cdn.example/original.jpg">')}
        first, requested = self.run_crawl(routes)
        self.assertEqual(first["collected"], 1)
        self.assertEqual(first["totals"], {"blocked": 1, "collected": 1, "expired": 1})
        self.assertFalse(any("cdn.example" in url for url in requested))
        self.assertEqual(first["list_pages"], 1)
        second, requested_again = self.run_crawl(routes)
        self.assertEqual(second["collected"], 0)
        self.assertFalse(any(".html" in url for url in requested_again))
        release = build(self.settings, now=NOW)
        html = (release / "posts/101.html").read_text(encoding="utf-8")
        self.assertIn('src="https://cdn.example/original.jpg"', html)
        self.assertFalse(any(p.suffix in {".jpg", ".png", ".sqlite3"} for p in release.rglob("*")))

    def test_failed_detail_retries_without_losing_discovered_task(self):
        routes = {"/thread0806.php?page=1": listing([("101", "正常讨论", NOW, NOW)]), "/htm_data/2610/7/101.html": 503}
        first, _ = self.run_crawl(routes)
        self.assertEqual(first["errors"], 1)
        with Store(self.settings, NOW) as store:
            self.assertEqual(store.get("last_success_at"), "")
            self.assertEqual(store.pending(10, 3)[0]["attempts"], 1)
        routes["/htm_data/2610/7/101.html"] = post()
        second, _ = self.run_crawl(routes)
        self.assertEqual(second["collected"], 1)

    def test_missing_posts_are_skipped_without_blocking_collection_or_retrying(self):
        self.settings.source = replace(self.settings.source, list_url=SOURCE + "&search=today")
        routes = {"/thread0806.php?page=1": listing([("103", "失效帖子", NOW, NOW), ("102", "已删除帖子", NOW, NOW),
                                                    ("101", "正常讨论", NOW, NOW)]),
                  "/htm_data/2610/7/103.html": 404, "/htm_data/2610/7/102.html": 410,
                  "/htm_data/2610/7/101.html": post()}
        stats, requested = self.run_crawl(routes)
        self.assertEqual([url.rsplit("/", 1)[-1] for url in requested if url.endswith(".html")],
                         ["103.html", "102.html", "101.html"])
        self.assertEqual(stats["errors"], 0)
        self.assertEqual(stats["skipped"], 2)
        self.assertEqual(stats["collected"], 1)
        with Store(self.settings, NOW) as store:
            for thread_id, code in (("103", 404), ("102", 410)):
                row = store.db.execute("SELECT * FROM threads WHERE id=?", (thread_id,)).fetchone()
                self.assertEqual(row["status"], "skipped")
                self.assertIn(str(code), row["last_error"])
                self.assertIsNone(row["body_html"])
            self.assertEqual(store.get("last_success_at"), NOW.isoformat())
            self.assertEqual(json.loads(store.get("new_article_ids")), ["101"])
        release = build(self.settings, now=NOW)
        self.assertTrue((release / "posts/101.html").is_file())
        for thread_id in ("102", "103"):
            self.assertFalse((release / f"posts/{thread_id}.html").exists())
        for retry_failed in (False, True):
            client, requested = self.fetcher(routes)
            with patch("forum_archive.crawler.time.sleep"):
                stats = crawl(self.settings, fetcher=client, now=NOW, retry_failed=retry_failed)
            self.assertEqual(stats["errors"], 0)
            self.assertFalse(any(url.endswith(".html") for url in requested))

    def test_missing_post_does_not_fail_crawl_command(self):
        self.settings.source = replace(self.settings.source, list_url=SOURCE + "&search=today")
        routes = {"/thread0806.php?page=1": listing([("102", "失效帖子", NOW, NOW), ("101", "正常讨论", NOW, NOW)]),
                  "/htm_data/2610/7/102.html": 404, "/htm_data/2610/7/101.html": post()}
        client, _ = self.fetcher(routes)
        with patch("forum_archive.__main__.Settings.load", return_value=self.settings), \
             patch("forum_archive.crawler.Fetcher", return_value=client), \
             patch("forum_archive.crawler.datetime", wraps=datetime) as clock, \
             patch("forum_archive.crawler.time.sleep"), patch("forum_archive.__main__.logging.basicConfig"), \
             patch("builtins.print"):
            clock.now.return_value = NOW
            self.assertEqual(main(["crawl"]), 0)

    def test_full_detail_title_is_filtered_again(self):
        routes = {"/thread0806.php?page=1": listing([("101", "截断标题", NOW, NOW)]),
                  "/htm_data/2610/7/101.html": post(title="完整标题含色情")}
        result, _ = self.run_crawl(routes)
        self.assertEqual(result["blocked"], 1)
        with Store(self.settings, NOW) as store:
            self.assertEqual(store.articles(), [])

    def test_bad_listing_preserves_cursor(self):
        routes = {"/thread0806.php?page=1": "<html>Access denied</html>"}
        with self.assertRaises(ParseError):
            self.run_crawl(routes)
        with Store(self.settings, NOW) as store:
            self.assertEqual(store.get("recent_page"), "1")
            self.assertEqual(store.get("last_scan_at"), "")

    def test_new_rule_removes_old_public_article_in_next_release(self):
        with Store(self.settings, NOW) as store:
            store.discover(Thread("101", SOURCE, "正常讨论", NOW.isoformat()), NOW)
            store.save_post("101", Post("正常讨论", NOW.isoformat(), "<p>正文</p>" * 50, "正文", 0), NOW, False)
        first = build(self.settings, now=NOW)
        self.assertTrue((first / "posts/101.html").exists())
        self.settings.filters = TitleFilter(["正常"])
        second = build(self.settings, now=NOW)
        self.assertNotEqual(first, second)
        self.assertFalse((second / "posts/101.html").exists())
        self.assertEqual(current_release(self.settings.output), second.resolve())
        self.assertFalse((first / "posts/101.html").exists())

    def test_blocked_page_cleanup_keeps_other_articles_and_state(self):
        with Store(self.settings, NOW) as store:
            for thread_id, title in (("101", "需要屏蔽的讨论"), ("102", "保留的讨论")):
                store.discover(Thread(thread_id, SOURCE, title, NOW.isoformat()), NOW)
                store.save_post(thread_id, Post(title, NOW.isoformat(), "<p>正文</p>" * 50, "正文", 0), NOW, False)
        first = build(self.settings, now=NOW)
        second = build(self.settings, now=NOW)
        unrelated = self.settings.output / "unrelated/posts/101.html"
        unrelated.parent.mkdir(parents=True)
        unrelated.write_text("不是生成的文章页", encoding="utf-8")
        self.settings.filters = TitleFilter(["需要屏蔽"])
        latest = build(self.settings, now=NOW)
        for release in (first, second, latest):
            self.assertFalse((release / "posts/101.html").exists())
            self.assertTrue((release / "posts/102.html").exists())
        self.assertTrue(unrelated.exists())
        self.assertEqual(current_release(self.settings.output), latest.resolve())
        with Store(self.settings, NOW) as store:
            self.assertEqual(store.counts(), {"blocked": 1, "collected": 1})
        # Repeated cleanup and later discovery must not restore the blocked post.
        with Store(self.settings, NOW) as store:
            store.discover(Thread("101", SOURCE, "需要屏蔽的讨论", NOW.isoformat()), NOW)
            self.assertEqual(store.pending(10, 3), [])
        build(self.settings, now=NOW)
        self.assertFalse((latest / "posts/101.html").exists())

    def test_rolling_retention_deletes_old_body_and_generated_pages(self):
        earlier = NOW - timedelta(days=10)
        dates = {"101": NOW - timedelta(days=30, seconds=1),
                 "102": NOW - timedelta(days=30),
                 "103": NOW - timedelta(days=29)}
        with Store(self.settings, earlier) as store:
            for thread_id, date in dates.items():
                title = "文章" + thread_id
                store.discover(Thread(thread_id, SOURCE, title, date.isoformat()), earlier)
                store.save_post(thread_id, Post(title, date.isoformat(), "<p>保留正文</p>" * 25, "摘要", 2), earlier, False)
        old_release = build(self.settings, now=earlier)
        self.assertTrue((old_release / "posts/101.html").is_file())
        latest = build(self.settings, now=NOW)
        for release in (old_release, latest):
            self.assertFalse((release / "posts/101.html").exists())
            self.assertTrue((release / "posts/102.html").is_file())
            self.assertTrue((release / "posts/103.html").is_file())
        self.assertNotIn("文章101", (latest / "index.html").read_text(encoding="utf-8"))
        with Store(self.settings, NOW) as store:
            row = store.db.execute("SELECT * FROM threads WHERE id='101'").fetchone()
            self.assertEqual(row["status"], "expired")
            self.assertIsNone(row["body_html"])
            self.assertIsNone(row["summary"])
            self.assertEqual(row["image_count"], 0)
            self.assertIn("101", store.removed_ids())
            self.assertEqual(store.cutoff, NOW - timedelta(days=30))

    def test_expired_posts_cannot_be_restored_by_removing_blacklist(self):
        earlier = NOW - timedelta(days=10)
        date = NOW - timedelta(days=31)
        with Store(self.settings, earlier) as store:
            store.discover(Thread("101", SOURCE, "测试文章", date.isoformat()), earlier)
            store.save_post("101", Post("测试文章", date.isoformat(), "<p>旧正文</p>", "摘要", 0), earlier, False)
        self.settings.filters = TitleFilter(["测试"])
        build(self.settings, now=NOW)
        self.settings.filters = TitleFilter([])
        build(self.settings, now=NOW)
        with Store(self.settings, NOW) as store:
            self.assertEqual(store.articles(), [])
            self.assertEqual(store.pending(10, 3), [])
            self.assertIn("101", store.removed_ids())

    def test_old_backfill_metadata_does_not_trigger_history_requests(self):
        with Store(self.settings, NOW) as store:
            store.set("backfill_page", "7")
            store.set("backfill_complete", "false")
        old = NOW - timedelta(days=40)
        result, requested = self.run_crawl({"/thread0806.php?page=1": listing([("101", "旧讨论", old, old)], 10)})
        self.assertEqual(result["list_pages"], 1)
        self.assertEqual(result["collected"], 0)
        self.assertFalse(any("page=7" in url for url in requested))

    def test_recent_scan_does_not_stop_on_bumped_old_creation_dates(self):
        self.settings.crawl.recent_pages_per_run = 3
        old = NOW - timedelta(days=40)
        routes = {"/thread0806.php?page=1": listing([("101", "第一页旧帖", old, NOW)], 4),
                  "/thread0806.php?page=2": listing([("102", "第二页旧帖", old, NOW)], 4),
                  "/thread0806.php?page=3": listing([("103", "新讨论", NOW, NOW)], 4),
                  "/htm_data/2610/7/103.html": post()}
        result, _ = self.run_crawl(routes)
        self.assertEqual(result["list_pages"], 3)
        self.assertEqual(result["collected"], 1)

    def test_malformed_image_url_does_not_discard_valid_body(self):
        result = ForumSource(self.settings.source).parse_post(post(body='<p>有效正文</p><img src="https://[invalid"><img src="http://127.0.0.1/private.png">'), SOURCE, None)
        self.assertIn("有效正文", result.body_html)
        self.assertEqual(result.image_count, 0)

    def test_request_rate_and_off_origin_restrictions(self):
        client, _ = self.fetcher({"/thread0806.php?page=1": listing([("101", "正常讨论", NOW, NOW)])})
        with client, patch("forum_archive.crawler.time.sleep") as sleeping, patch("forum_archive.crawler.time.monotonic", return_value=100):
            client.check_robots()
            client.get(ForumSource(self.settings.source).page_url(1))
            self.assertEqual(sleeping.call_args.args[0], 10)
            with self.assertRaises(ValueError):
                client.get("https://cdn.example/image.jpg")
            with self.assertRaises(RuntimeError):
                client.get("https://forum.example/admin/")

    def test_missing_post_timestamp_does_not_use_registration_date(self):
        with self.assertRaises(ParseError):
            ForumSource(self.settings.source).parse_post('<div>注册：2001-01-01</div><td><h4 class="f16">标题</h4><div id="conttpc">正文</div></td>', SOURCE, None)

    def test_manifest_cannot_escape_output_directory(self):
        self.settings.output.mkdir()
        (self.settings.output / "current.json").write_text(json.dumps({"release": ".."}), encoding="utf-8")
        with self.assertRaises(ValueError):
            current_release(self.settings.output)


if __name__ == "__main__":
    unittest.main()
