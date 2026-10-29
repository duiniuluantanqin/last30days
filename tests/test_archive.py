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


def post(title="正常讨论", body="<p>正文内容</p>", created=NOW):
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
                                crawl=replace(original.crawl, recent_pages_per_run=1, backfill_pages_per_run=1),
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

    def test_extra_keyword_files_extend_config_blacklist(self):
        config = self.settings.root / "config.toml"
        config.write_text('[source]\nlist_url = "' + SOURCE + '"\n[filter]\nblocked_keywords = ["原有词"]\n', encoding="utf-8")
        directory = self.settings.root / "blacklist"
        directory.mkdir()
        (directory / "one.txt").write_text("新增词\n", encoding="utf-8")
        (directory / "two.txt").write_text("\n  另一个词  \n", encoding="utf-8")
        (directory / "README.md").write_text("不是黑名单词", encoding="utf-8")
        settings = Settings.load(config)
        self.assertTrue(settings.filters.blocks("包含原有词的标题"))
        self.assertTrue(settings.filters.blocks("包含新增词的标题"))
        self.assertTrue(settings.filters.blocks("包含另一个词的标题"))
        self.assertFalse(settings.filters.blocks("不是黑名单词"))

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
        self.assertEqual(len(scripts), 1)
        self.assertEqual(scripts[0]["src"], "/assets/keyword-menu.js")
        self.assertTrue((release / "assets/keyword-menu.js").is_file())
        self.assertFalse(scripts[0].get_text(strip=True))

    def test_merged_keyword_file_hides_previously_collected_article(self):
        with Store(self.settings, NOW) as store:
            store.discover(Thread("101", SOURCE, "新增关键词讨论", NOW.isoformat()), NOW)
            store.save_post("101", Post("新增关键词讨论", NOW.isoformat(), "<p>正文</p>", "正文", 0), NOW, False)
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
        self.assertEqual(page.select("script"), [])

    def test_real_layout_timestamp_and_forum_scope(self):
        html = listing([("123", "正常讨论", NOW, NOW)], 5)
        html += '<h3><a href="/htm_data/2610/9/999.html">其他版块</a></h3>'
        html += '<h3><a href="https://other.example/htm_data/2610/7/555.html">外部站点</a></h3>'
        result = ForumSource(self.settings.source).parse_listing(html, 1)
        self.assertEqual([t.id for t in result.threads], ["123"])
        self.assertEqual(result.threads[0].listed_at, NOW.isoformat())
        self.assertEqual(result.threads[0].last_reply_at, NOW.isoformat())
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
        markers = ["防水图", "防水<span>图</span>", "&#x9632;&#x6c34;&#x56fe;"]
        with Store(self.settings, NOW) as store:
            for index, marker in enumerate(markers, 201):
                thread_id = str(index)
                body = (f'<p>保留正文：{marker}</p><img src="https://cdn.example/first.jpg">'
                        '<p>后续文字也保留</p><img src="https://cdn.example/second.jpg">')
                store.discover(Thread(thread_id, SOURCE, "正常讨论", NOW.isoformat()), NOW)
                store.save_post(thread_id, Post("正常讨论", NOW.isoformat(), body, "正文摘要", 2), NOW, False)
        release = build(self.settings, now=NOW)
        for index in range(201, 204):
            with self.subTest(thread_id=index):
                page = BeautifulSoup((release / f"posts/{index}.html").read_text(encoding="utf-8"), "html.parser")
                self.assertEqual(page.select(".prose img"), [])
                self.assertIn("防水图", page.select_one(".prose").get_text())
                self.assertIn("后续文字也保留", page.select_one(".prose").get_text())
                self.assertEqual(page.select(".image-note"), [])
        index_html = (release / "index.html").read_text(encoding="utf-8")
        self.assertNotIn("张外链图片", index_html)
        with Store(self.settings, NOW) as store:
            self.assertTrue(all(article["image_count"] == 2 for article in store.articles()))

    def test_build_keeps_images_for_posts_without_marker(self):
        with Store(self.settings, NOW) as store:
            store.discover(Thread("201", SOURCE, "正常讨论", NOW.isoformat()), NOW)
            body = '<p>普通正文</p><img src="https://cdn.example/original.jpg">'
            store.save_post("201", Post("正常讨论", NOW.isoformat(), body, "普通正文", 1), NOW, False)
        release = build(self.settings, now=NOW)
        page = BeautifulSoup((release / "posts/201.html").read_text(encoding="utf-8"), "html.parser")
        self.assertEqual(page.select_one(".prose img")["src"], "https://cdn.example/original.jpg")
        self.assertIsNotNone(page.select_one(".image-note"))
        self.assertIn("1 张外链图片", (release / "index.html").read_text(encoding="utf-8"))

    def test_end_to_end_no_image_requests_and_idempotent_replay(self):
        html = listing([("101", "正常讨论", NOW, NOW), ("102", "色情帖子", NOW, NOW), ("103", "旧帖", NOW - timedelta(days=40), NOW)])
        routes = {"/thread0806.php?page=1": html,
                  "/htm_data/2610/7/101.html": post(body='<p>正常正文</p><img ess-data="https://cdn.example/original.jpg">')}
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
            self.assertEqual(store.get("backfill_page"), "1")
            self.assertEqual(store.get("last_scan_at"), "")

    def test_new_rule_removes_old_public_article_in_next_release(self):
        with Store(self.settings, NOW) as store:
            store.discover(Thread("101", SOURCE, "正常讨论", NOW.isoformat()), NOW)
            store.save_post("101", Post("正常讨论", NOW.isoformat(), "<p>正文</p>", "正文", 0), NOW, False)
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
                store.save_post(thread_id, Post(title, NOW.isoformat(), "<p>正文</p>", "正文", 0), NOW, False)
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
                store.save_post(thread_id, Post(title, date.isoformat(), "<p>保留正文</p>", "摘要", 2), earlier, False)
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

    def test_last_reply_not_creation_date_controls_backfill_boundary(self):
        self.settings.crawl.backfill_pages_per_run = 2
        old = NOW - timedelta(days=40)
        routes = {"/thread0806.php?page=1": listing([("101", "旧帖被顶起", old, NOW)], 4),
                  "/thread0806.php?page=2": listing([("102", "旧帖仍被回复", old, NOW)], 4)}
        result, _ = self.run_crawl(routes)
        self.assertFalse(result["backfill_complete"])
        self.assertEqual(result["backfill_page"], 3)

    def test_old_last_reply_ends_backfill(self):
        old = NOW - timedelta(days=40)
        result, _ = self.run_crawl({"/thread0806.php?page=1": listing([("101", "旧讨论", old, old)], 10)})
        self.assertTrue(result["backfill_complete"])
        self.assertEqual(result["collected"], 0)

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
