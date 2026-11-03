from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import datetime, timedelta
from functools import partial
import json
from pathlib import Path
import tempfile
from threading import Thread
import unittest
from zoneinfo import ZoneInfo

from bs4 import BeautifulSoup
import httpx

from forum_archive.analytics import AnalyticsHandler, AnalyticsStore
from forum_archive.config import Settings, TitleFilter
from forum_archive.site import ArchiveServer, build


ROOT = Path(__file__).resolve().parents[1]
NOW = datetime(2026, 11, 3, 23, 59, tzinfo=ZoneInfo("Asia/Shanghai"))
ORIGIN = "https://duiniuluantanqin.github.io"
VISITOR = "a" * 32


class AnalyticsTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.store = AnalyticsStore(self.root / "state/analytics.sqlite3")

    def test_uv_deduplicates_browsers_and_pv_counts_every_page(self):
        self.assertEqual(self.store.counts(now=NOW)["total"], {"uv": 0, "pv": 0})
        self.store.record(VISITOR, now=NOW)
        self.store.record(VISITOR, now=NOW)
        counts = self.store.record("b" * 32, now=NOW)
        self.assertEqual(counts["today"], {"date": "2026-11-03", "uv": 2, "pv": 3})
        self.assertEqual(counts["total"], {"uv": 2, "pv": 3})
        self.assertEqual(AnalyticsStore(self.store.database).counts(now=NOW), counts)

    def test_days_use_beijing_time_and_total_uv_is_not_a_sum_of_daily_uv(self):
        self.store.record(VISITOR, now=NOW)
        tomorrow = (NOW + timedelta(minutes=2)).astimezone(ZoneInfo("UTC"))
        counts = self.store.record(VISITOR, now=tomorrow)
        self.assertEqual(counts["today"], {"date": "2026-11-04", "uv": 1, "pv": 1})
        self.assertEqual(counts["total"], {"uv": 1, "pv": 2})
        self.assertEqual(self.store.counts(now=NOW + timedelta(days=2))["today"]["pv"], 0)

    def test_visitors_without_storage_still_count_pv_without_inventing_uv(self):
        self.store.record(None, now=NOW)
        counts = self.store.record(None, now=NOW)
        self.assertEqual(counts["total"], {"uv": 0, "pv": 2})

    def test_concurrent_visits_do_not_lose_counts(self):
        with ThreadPoolExecutor(max_workers=8) as executor:
            list(executor.map(lambda _: self.store.record(VISITOR, now=NOW), range(40)))
        self.assertEqual(self.store.counts(now=NOW)["total"], {"uv": 1, "pv": 40})

    def test_api_checks_origin_payload_and_does_not_count_reads(self):
        server = ArchiveServer(("127.0.0.1", 0), partial(AnalyticsHandler, store=self.store, origin=ORIGIN))
        thread = Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            with httpx.Client(base_url=f"http://127.0.0.1:{server.server_port}", headers={"Origin": ORIGIN}) as client:
                response = client.options("/api/analytics", headers={"Access-Control-Request-Method": "POST",
                                                                     "Access-Control-Request-Headers": "content-type"})
                self.assertEqual(response.status_code, 204)
                self.assertEqual(response.headers["access-control-allow-origin"], ORIGIN)
                self.assertIn("POST", response.headers["access-control-allow-methods"])
                self.assertEqual(client.get("/api/analytics").json()["total"], {"uv": 0, "pv": 0})
                response = client.post("/api/analytics", json={"visitor": VISITOR})
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.headers["cache-control"], "no-store")
                self.assertEqual(response.json()["total"], {"uv": 1, "pv": 1})
                self.assertEqual(client.get("/api/analytics").json()["total"], {"uv": 1, "pv": 1})
                for origin in ("https://other.example", "null", ORIGIN + ".evil.example"):
                    response = client.post("/api/analytics", json={"visitor": VISITOR}, headers={"Origin": origin})
                    self.assertEqual(response.status_code, 403)
                    self.assertNotIn("access-control-allow-origin", response.headers)
                with httpx.Client(base_url=str(client.base_url)) as anonymous:
                    self.assertEqual(anonymous.post("/api/analytics", json={"visitor": VISITOR}).status_code, 403)
                for payload in ([], {"visitor": 123}, {"visitor": "bad"}, {"visitor": "x" * 2000}):
                    with self.subTest(payload=payload):
                        self.assertIn(client.post("/api/analytics", json=payload).status_code, (400, 413))
                self.assertEqual(client.post("/api/analytics", content=b"{", headers={"Content-Type": "application/json"}).status_code, 400)
                self.assertEqual(client.post("/api/analytics", content=b"{}").status_code, 415)
                self.assertEqual(client.get("/state/analytics.sqlite3").status_code, 404)
                self.assertEqual(client.post("/unknown", json={"visitor": VISITOR}).status_code, 404)
                self.assertEqual(client.get("/api/analytics").json()["total"], {"uv": 1, "pv": 1})
        finally:
            server.shutdown()
            server.server_close()
            thread.join()

    def test_build_adds_own_script_only_when_configured_and_never_publishes_database(self):
        settings = Settings.load(ROOT / "config.toml")
        settings = replace(settings, root=self.root, filters=TitleFilter([]),
                           site=replace(settings.site, analytics_endpoint="https://stats.example.com/api/analytics",
                                        analytics_origin=ORIGIN),
                           database=self.root / "archive.sqlite3", output=self.root / "site")
        release = build(settings, now=NOW, base_path="/last30days")
        for filename in ("index.html", "search.html", "404.html"):
            page = BeautifulSoup((release / filename).read_text(encoding="utf-8"), "html.parser")
            script = page.select_one('script[src="/last30days/assets/analytics.js"]')
            self.assertEqual(script["data-endpoint"], settings.site.analytics_endpoint)
            self.assertEqual(script["data-origin"], ORIGIN)
            self.assertIsNotNone(page.select_one("[data-analytics-counts][hidden]"))
        self.assertFalse(list(release.rglob("*.sqlite3")))
        disabled = build(replace(settings, site=replace(settings.site, analytics_endpoint="")), now=NOW)
        page = BeautifulSoup((disabled / "index.html").read_text(encoding="utf-8"), "html.parser")
        self.assertIsNone(page.select_one("script[src$='/analytics.js']"))
        self.assertIsNone(page.select_one("[data-analytics-counts]"))

    def test_config_rejects_insecure_or_invalid_analytics_urls(self):
        config = self.root / "config.toml"
        for endpoint, origin in (("http://stats.example.com/api/analytics", ORIGIN),
                                 ("https://user:pass@stats.example.com/api/analytics", ORIGIN),
                                 ("https://stats.example.com/api/analytics#fragment", ORIGIN),
                                 ("https://stats.example.com/api/analytics", ""),
                                 ("https://stats.example.com/api/analytics", ORIGIN + "/last30days")):
            config.write_text('[source]\nlist_url="https://example.com/forum"\n[site]\n'
                              f'analytics_endpoint={json.dumps(endpoint)}\nanalytics_origin={json.dumps(origin)}\n', encoding="utf-8")
            with self.subTest(endpoint=endpoint, origin=origin), self.assertRaises(ValueError):
                Settings.load(config)


if __name__ == "__main__":
    unittest.main()
