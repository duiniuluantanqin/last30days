from dataclasses import dataclass
from datetime import datetime, timedelta
import ipaddress
import re
from urllib.parse import parse_qs, urlencode, urljoin, urlsplit, urlunsplit
from zoneinfo import ZoneInfo

import bleach
from bs4 import BeautifulSoup

from .config import Source


class ParseError(ValueError):
    pass


@dataclass
class Thread:
    id: str
    url: str
    title: str
    listed_at: str | None


@dataclass
class Listing:
    threads: list[Thread]
    last_page: int


@dataclass
class Post:
    title: str
    published_at: str
    body_html: str
    summary: str
    image_count: int


def web_url(value: str, base: str) -> str | None:
    value = value.strip()
    if any(ord(c) < 32 for c in value) or "\\" in value:
        return None
    try:
        url = urljoin(base, value)
        parts = urlsplit(url)
    except ValueError:
        return None
    if parts.scheme not in {"http", "https"} or not parts.hostname or parts.username or parts.password:
        return None
    if parts.hostname.lower() in {"localhost", "localhost.localdomain"} or parts.hostname.endswith(".local"):
        return None
    try:
        if not ipaddress.ip_address(parts.hostname).is_global:
            return None
    except ValueError:
        pass
    return url


def timestamp(node, timezone: str) -> str | None:
    if node is None:
        return None
    element = node if node.get("data-timestamp") else node.find(attrs={"data-timestamp": True})
    if element:
        try:
            value = str(element["data-timestamp"]).removesuffix("s")
            return datetime.fromtimestamp(int(value), ZoneInfo(timezone)).isoformat()
        except (ValueError, OverflowError, OSError):
            return None
    time = node.find("time", datetime=True)
    if time:
        try:
            date = datetime.fromisoformat(time["datetime"])
            if date.tzinfo is None:
                date = date.replace(tzinfo=ZoneInfo(timezone))
            return date.astimezone(ZoneInfo(timezone)).isoformat()
        except ValueError:
            return None
    return None


class ForumSource:
    def __init__(self, settings: Source):
        self.settings = settings
        self.origin = urlsplit(settings.list_url)
        self.forum_id = parse_qs(self.origin.query).get("fid", ["7"])[0]
        self.today_only = parse_qs(self.origin.query).get("search") == ["today"]

    def same_origin(self, url: str) -> bool:
        parsed = urlsplit(url)
        return (parsed.scheme, parsed.netloc) == (self.origin.scheme, self.origin.netloc)

    def page_url(self, page: int) -> str:
        parts = self.origin
        query = parse_qs(parts.query, keep_blank_values=True)
        query["page"] = [str(page)]
        return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query, doseq=True), ""))

    def parse_listing(self, html: bytes | str, page: int) -> Listing:
        soup = BeautifulSoup(html, "html.parser")
        threads = {}
        for anchor in soup.select("h3 a[href]"):
            url = urljoin(self.settings.list_url, anchor["href"])
            if not self.same_origin(url):
                continue
            parts = urlsplit(url)
            match = re.fullmatch(r"/htm_data/\d{4}/" + re.escape(self.forum_id) + r"/(\d+)\.html", parts.path)
            query = parse_qs(parts.query)
            thread_id = match[1] if match else None
            if not thread_id and parts.path.endswith("/read.php"):
                candidate = query.get("tid", [""])[0]
                if candidate.isdigit() and query.get("fid", [self.forum_id])[0] == self.forum_id:
                    thread_id = candidate
            title = anchor.get_text(" ", strip=True)
            if not thread_id or not title:
                continue
            row = anchor.find_parent("tr")
            date_node = row.select_one(".f12") if row else None
            threads[thread_id] = Thread(thread_id, urlunsplit((*parts[:4], "")), title,
                                        timestamp(date_node, self.settings.timezone))
        if not threads:
            raise ParseError("列表中没有可识别的本版块帖子，可能是访问限制或页面结构变化；进度未推进")
        last_page = page
        for anchor in soup.select("a[href]"):
            url = urljoin(self.settings.list_url, anchor["href"])
            parts = urlsplit(url)
            query = parse_qs(parts.query)
            if (self.same_origin(url) and parts.path == self.origin.path
                    and query.get("fid", [self.forum_id])[0] == self.forum_id
                    and (not self.today_only or query.get("search") == ["today"])):
                value = query.get("page", [""])[0]
                if value.isdigit():
                    last_page = max(last_page, int(value))
        return Listing(list(threads.values()), last_page)

    def parse_post(self, html: bytes | str, url: str, listed_at: str | None, *, now: datetime | None = None) -> Post:
        soup = BeautifulSoup(html, "html.parser")
        content = soup.select_one("#conttpc")
        if content is None:
            raise ParseError("未找到主帖正文 #conttpc；不会把登录页、错误页或回复当成主帖")
        container = content.find_parent("td") or content.parent
        heading = container.select_one("h4.f16, h1, #subject_tpc")
        if heading is None or not heading.get_text(strip=True):
            raise ParseError("无法取得主帖完整标题")
        title = heading.get_text(" ", strip=True)
        posted = soup.select_one(".tipad")
        published_at = timestamp(posted, self.settings.timezone)
        if not published_at and posted:
            match = re.search(r"\bPosted\s*:\s*(\d{2})-(\d{2})\s+(\d{2}):(\d{2})", posted.get_text(" ", strip=True), re.I)
            if match:
                reference = datetime.fromisoformat(listed_at) if listed_at else (now or datetime.now(ZoneInfo(self.settings.timezone)))
                reference = reference.astimezone(ZoneInfo(self.settings.timezone))
                try:
                    date = reference.replace(month=int(match[1]), day=int(match[2]), hour=int(match[3]), minute=int(match[4]), second=0, microsecond=0)
                    if date > reference + timedelta(days=1):
                        date = date.replace(year=date.year - 1)
                    published_at = date.isoformat()
                except ValueError:
                    raise ParseError("楼主 Posted 发布时间无效")
        published_at = published_at or listed_at
        if not published_at:
            raise ParseError("无法确定主帖发布时间，未将注册日期或回复日期作为发帖时间")
        cleaned, summary, images = clean_content(content, url)
        if not cleaned.strip():
            raise ParseError("主帖正文为空")
        return Post(title, published_at, cleaned, summary, images)


def clean_content(content, base_url: str) -> tuple[str, str, int]:
    content = BeautifulSoup(str(content), "html.parser")
    for node in content.select("script, style, iframe, object, embed, video, audio, source, form, textarea, button, link, meta"):
        node.decompose()
    for image in content.select("img, input"):
        if image.name == "input" and image.get("type", "").lower() != "image":
            image.decompose()
            continue
        # The forum's lazy images use ess-data; iyl-data is an ad-block placeholder.
        original = next((image.get(key) for key in ("ess-data", "data-original", "data-src", "src") if image.get(key)), "")
        url = web_url(original, base_url) if original else None
        if not url:
            image.decompose()
            continue
        alt = image.get("alt", "原帖图片")
        image.name = "img"
        image.attrs = {"src": url, "alt": alt, "loading": "lazy", "decoding": "async", "referrerpolicy": "no-referrer"}
    for anchor in content.select("a"):
        href = web_url(anchor.get("href", ""), base_url) if anchor.get("href") else None
        if not href:
            anchor.unwrap()
        else:
            anchor.attrs = {"href": href, "target": "_blank", "rel": "noopener noreferrer nofollow"}
    tags = {"p", "br", "div", "span", "strong", "b", "i", "em", "u", "s", "blockquote", "pre", "code", "ul", "ol", "li", "table", "thead", "tbody", "tr", "th", "td", "hr", "a", "img", "h2", "h3", "h4"}
    attrs = {"a": ["href", "target", "rel"], "img": ["src", "alt", "loading", "decoding", "referrerpolicy"]}
    cleaned = bleach.clean(str(content), tags=tags, attributes=attrs, protocols={"http", "https"}, strip=True, strip_comments=True)
    parsed = BeautifulSoup(cleaned, "html.parser")
    summary = " ".join(parsed.get_text(" ", strip=True).split())[:180]
    return cleaned, summary, len(parsed.select("img"))
