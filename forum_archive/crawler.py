from datetime import datetime, timedelta
import logging
import time
from urllib.parse import urljoin
from urllib.robotparser import RobotFileParser
from zoneinfo import ZoneInfo

import httpx

from .config import Settings
from .source import ForumSource, ParseError
from .store import Store


log = logging.getLogger(__name__)


class Fetcher:
    """Only fetch same-origin HTML/robots; there is no image download path."""

    def __init__(self, settings: Settings):
        self.config = settings.source
        self.source = ForumSource(self.config)
        self.client = httpx.Client(timeout=self.config.timeout_seconds,
                                  headers={"User-Agent": self.config.user_agent})
        self.last_request = 0.0
        self.robot = None

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.client.close()

    def _request(self, url: str, *, robots: bool = False) -> bytes:
        if not self.source.same_origin(url):
            raise ValueError("拒绝请求来源站点以外的网址")
        for attempt in range(self.config.retries + 1):
            try:
                delay = max(0, self.config.request_interval_seconds - (time.monotonic() - self.last_request))
                if self.robot is not None:
                    configured = self.robot.crawl_delay(self.config.user_agent) or self.robot.crawl_delay("*") or 0
                    rate = self.robot.request_rate(self.config.user_agent) or self.robot.request_rate("*")
                    if rate:
                        configured = max(configured, rate.seconds / rate.requests)
                    delay = max(delay, configured - (time.monotonic() - self.last_request))
                time.sleep(delay)
                self.last_request = time.monotonic()
                with self.client.stream("GET", url) as response:
                    if robots and response.status_code in {404, 410}:
                        return b""
                    if 300 <= response.status_code < 400:
                        raise RuntimeError("来源返回重定向，需人工确认来源网址；未跟随到其他站点")
                    response.raise_for_status()
                    kind = response.headers.get("content-type", "").lower()
                    if not robots and not any(value in kind for value in ("text/html", "application/xhtml+xml")):
                        raise RuntimeError("来源响应不是 HTML，已停止处理")
                    chunks = []
                    total = 0
                    for chunk in response.iter_bytes():
                        total += len(chunk)
                        if total > self.config.max_response_bytes:
                            raise RuntimeError("响应超过配置的大小限制")
                        chunks.append(chunk)
                    return b"".join(chunks)
            except (httpx.TransportError, httpx.HTTPStatusError) as error:
                retryable = not isinstance(error, httpx.HTTPStatusError) or error.response.status_code in {429, 500, 502, 503, 504}
                if not retryable or attempt == self.config.retries:
                    raise
                time.sleep(min(2 ** (attempt + 1), 8))
        raise RuntimeError("请求未完成")

    def check_robots(self):
        if not self.config.respect_robots:
            return
        url = urljoin(self.config.list_url, "/robots.txt")
        body = self._request(url, robots=True).decode("utf-8", "replace")
        if "<html" in body.lower() or "<!doctype" in body.lower():
            raise RuntimeError("robots.txt 返回了网页，无法确认抓取规则，请先人工核实")
        self.robot = RobotFileParser(url)
        self.robot.parse(body.splitlines())

    def get(self, url: str) -> bytes:
        if self.robot and not self.robot.can_fetch(self.config.user_agent, url):
            raise RuntimeError("robots.txt 不允许访问该地址")
        return self._request(url)


def crawl(settings: Settings, *, retry_failed: bool = False, fetcher=None, now: datetime | None = None) -> dict:
    now = now or datetime.now(ZoneInfo(settings.source.timezone))
    source = ForumSource(settings.source)
    stats = {"list_pages": 0, "discovered": 0, "collected": 0, "blocked": 0, "expired": 0, "errors": 0}
    with Store(settings, now) as store:
        if retry_failed:
            store.retry_failed()
        if store.get("crawl_in_progress"):
            log.warning("上次采集未正常结束，将继续处理已保存的任务")
        store.set("crawl_in_progress", now.isoformat())
        client = fetcher or Fetcher(settings)
        with client:
            client.check_robots()
            scanned = {}

            def discover_page(page: int):
                if page in scanned:
                    return scanned[page]
                listing = source.parse_listing(client.get(source.page_url(page)), page)
                for thread in listing.threads:
                    store.discover(thread, now)
                stats["list_pages"] += 1
                stats["discovered"] += len(listing.threads)
                scanned[page] = listing
                return listing

            # Refresh the front page every time; continue deeper scans across runs.
            first = discover_page(1)
            recent_page = max(2, int(store.get("recent_page", "2")))
            recent_cutoff = now - timedelta(hours=settings.crawl.overlap_hours)
            for _ in range(max(0, settings.crawl.recent_pages_per_run - 1)):
                if recent_page > first.last_page:
                    recent_page = 2
                    break
                listing = discover_page(recent_page)
                # All discovered threads are persisted even if a page spans the cutoff.
                dates = [datetime.fromisoformat(t.last_reply_at) for t in listing.threads if t.last_reply_at]
                recent_page += 1
                if len(dates) == len(listing.threads) and max(dates) < recent_cutoff:
                    recent_page = 2
                    break
            store.set("recent_page", str(recent_page))

            # This forum orders ordinary threads by their last reply. A page of old
            # creation dates cannot end backfill, but a page whose last replies are
            # ALL older than the cutoff can. Missing timestamps force a full scan.
            if store.get("backfill_complete") != "true":
                page = int(store.get("backfill_page", "1"))
                for _ in range(settings.crawl.backfill_pages_per_run):
                    if page > first.last_page:
                        store.set("backfill_complete", "true")
                        break
                    listing = discover_page(page)
                    page += 1
                    store.set("backfill_page", str(page))
                    replies = [datetime.fromisoformat(t.last_reply_at) for t in listing.threads if t.last_reply_at]
                    if len(replies) == len(listing.threads) and max(replies) < store.cutoff:
                        store.set("backfill_complete", "true")
                        break

            stats["blocked"] += store.apply_filter(settings.filters)
            # Expire saved bodies as well as old discovered links before fetching.
            stats["expired"] += store.expire_old()
            for thread in store.pending(settings.crawl.max_posts_per_run, settings.crawl.max_attempts):
                try:
                    if not source.same_origin(thread["url"]):
                        raise ValueError("状态中的帖子 URL 与来源不一致")
                    post = source.parse_post(client.get(thread["url"]), thread["url"], thread["listed_at"])
                    status = store.save_post(thread["id"], post, now, settings.filters.blocks(post.title, post.body_html))
                    stats[status] += 1
                except (httpx.HTTPError, ParseError, RuntimeError, ValueError) as error:
                    store.fail(thread["id"], str(error))
                    stats["errors"] += 1
                    log.warning("帖子 %s 获取失败：%s", thread["id"], type(error).__name__)
            store.set("last_scan_at", now.isoformat())
            if not stats["errors"]:
                store.set("last_success_at", now.isoformat())
            store.set("crawl_in_progress", "")
            stats["totals"] = store.counts()
            stats["backfill_page"] = int(store.get("backfill_page"))
            stats["backfill_complete"] = store.get("backfill_complete") == "true"
            stats["retry_exhausted"] = store.db.execute("SELECT COUNT(*) FROM threads WHERE status='pending' AND attempts>=?", (settings.crawl.max_attempts,)).fetchone()[0]
    return stats
