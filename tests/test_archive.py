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

    def test_body_rules_match_full_text_without_html_attributes(self):
        rules = self.settings.filters
        for body in ("<p>普通文字</p>" * 100 + "<p>色情</p>",
                     "<p>色<span>情</span></p>", "<p>&#x8272;&#x60c5;</p>",
                     "<p>ＡＶ\u200b 女优</p>", "<p>广告 123</p>"):
            with self.subTest(body=body):
                self.assertTrue(rules.blocks("正常讨论", body))
        self.assertFalse(rules.blocks("正常讨论", '<p>普通正文</p><img src="https://cdn.example/色情.jpg" alt="色情">'
                                      '<a href="https://example.com/色情">普通链接</a>'))
        self.assertFalse(rules.blocks("色", "<p>情</p>"))

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
                                    ("102", "<p>保留正文</p>")):
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
            store.save_post("101", Post("新增广告词讨论", NOW.isoformat(), "<p>正文</p>", "正文", 0), NOW, False)
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
        markers = ["防水图", "防水<span>图</span>", "&#x9632;&#x6c34;&#x56fe;",
                   "防<strong>水</strong>图", "防水<span>图以及防</span>水图"]
        with Store(self.settings, NOW) as store:
            for index, marker in enumerate(markers, 201):
                thread_id = str(index)
                body = (f'<p>保留正文：{marker}</p><img src="https://cdn.example/first.jpg">'
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
            body = '<p>普通正文</p><img src="https://cdn.example/original.jpg">'
            store.save_post("201", Post("正常讨论", NOW.isoformat(), body, "普通正文", 1), NOW, False)
        release = build(self.settings, now=NOW)
        page = BeautifulSoup((release / "posts/201.html").read_text(encoding="utf-8"), "html.parser")
        self.assertEqual(page.select_one(".prose img")["src"], "https://cdn.example/original.jpg")
        self.assertIsNotNone(page.select_one(".image-note"))
        self.assertIn("1 张外链图片", (release / "index.html").read_text(encoding="utf-8"))

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
                self.assertEqual(bool(page.select_one(".image-note")), bool(remaining))
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
