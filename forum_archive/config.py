from dataclasses import dataclass, field
from pathlib import Path
import re
import tomllib
import unicodedata
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

from bs4 import BeautifulSoup


@dataclass
class Source:
    list_url: str
    timezone: str = "Asia/Shanghai"
    request_interval_seconds: float = 2.0
    timeout_seconds: float = 25
    retries: int = 2
    max_response_bytes: int = 4_000_000
    respect_robots: bool = True
    user_agent: str = "ForumArchive/0.1 (personal text archive)"


@dataclass
class Crawl:
    initial_days: int = 30
    overlap_hours: int = 48
    recent_pages_per_run: int = 5
    backfill_pages_per_run: int = 5
    max_posts_per_run: int = 60
    max_attempts: int = 3


def normalize_title(value: str) -> str:
    value = unicodedata.normalize("NFKC", value).casefold()
    return "".join(c for c in value if not c.isspace() and unicodedata.category(c) != "Cf")


@dataclass
class TitleFilter:
    blocked_keywords: list[str] = field(default_factory=list)
    blocked_patterns: list[str] = field(default_factory=list)

    def __post_init__(self):
        self.keywords = list(dict.fromkeys(normalized for word in self.blocked_keywords
                                          if (normalized := normalize_title(word))))
        self.patterns = [re.compile(pattern, re.IGNORECASE) for pattern in self.blocked_patterns]

    def blocks(self, title: str, body_html: str = "") -> bool:
        texts = [title]
        if body_html:
            texts.append(BeautifulSoup(body_html, "html.parser").get_text(" ", strip=True))
        for text in texts:
            normalized = normalize_title(text)
            if any(word in normalized for word in self.keywords) or any(
                pattern.search(normalized) for pattern in self.patterns
            ):
                return True
        return False


@dataclass
class Site:
    title: str = "拾页"
    subtitle: str = "技术讨论 · 每小时更新"
    description: str = "把值得阅读的讨论，留在这里。"
    page_size: int = 20
    github_repository: str = ""
    github_branch: str = "main"


@dataclass
class Settings:
    root: Path
    source: Source
    crawl: Crawl
    filters: TitleFilter
    site: Site
    database: Path
    output: Path

    @classmethod
    def load(cls, path: str | Path = "config.toml"):
        path = Path(path).resolve()
        with path.open("rb") as stream:
            raw = tomllib.load(stream)
        root = path.parent
        source = Source(**raw["source"])
        crawl = Crawl(**raw.get("crawl", {}))
        site = Site(**raw.get("site", {}))
        if site.github_repository and (
            not re.fullmatch(r"[A-Za-z0-9](?:[A-Za-z0-9-]*[A-Za-z0-9])?/[A-Za-z0-9_.-]+", site.github_repository)
            or site.github_repository.split("/")[-1] in {".", ".."}
        ):
            raise ValueError("site.github_repository 必须为 GitHub 的 owner/repo")
        if not site.github_branch.strip():
            raise ValueError("site.github_branch 不能为空")
        parts = urlsplit(source.list_url)
        if parts.scheme not in {"http", "https"} or not parts.hostname or parts.username or parts.password:
            raise ValueError("source.list_url 必须是没有账号密码的 HTTP(S) 网址")
        ZoneInfo(source.timezone)
        if source.request_interval_seconds < 0 or source.retries < 0:
            raise ValueError("请求间隔和重试次数不能为负数")
        if source.timeout_seconds <= 0 or source.max_response_bytes <= 0:
            raise ValueError("请求超时和响应大小限制必须为正数")
        if any(value <= 0 for value in vars(crawl).values()) or site.page_size <= 0:
            raise ValueError("采集限制与分页数量必须为正数")
        storage = raw.get("storage", {})
        filters = dict(raw.get("filter", {}))
        keywords = list(filters.get("blocked_keywords", []))
        for keyword_file in sorted((root / "blacklist").glob("*.txt")):
            keywords.extend(keyword_file.read_text(encoding="utf-8-sig").splitlines())
        for filename in filters.pop("keyword_files", []):
            keyword_file = root / filename
            if not keyword_file.is_file():
                raise ValueError(f"词库文件不存在：{filename}；请运行 git submodule update --init 或检查配置")
            keywords.extend(keyword_file.read_text(encoding="utf-8-sig").splitlines())
        filters["blocked_keywords"] = keywords
        return cls(root, source, crawl, TitleFilter(**filters), site,
                   root / storage.get("database", "data/archive.sqlite3"),
                   root / storage.get("output", "site"))
