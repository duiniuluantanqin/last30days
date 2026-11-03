from collections import defaultdict
from datetime import datetime
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import re
import shutil
import socket
from uuid import uuid4
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

from bs4 import BeautifulSoup, NavigableString
from jinja2 import Environment, FileSystemLoader, select_autoescape

from .config import Settings
from .content import clean_display_text
from .store import Store


PACKAGE = Path(__file__).parent


def build(settings: Settings, *, now: datetime | None = None, base_path: str = "") -> Path:
    if not re.fullmatch(r"(?:/[A-Za-z0-9_.-]+)*", base_path) or any(part in {".", ".."} for part in base_path.split("/")):
        raise ValueError("base_path 必须为空或 /last30days 形式的路径，末尾不带斜线")
    now = now or datetime.now(ZoneInfo(settings.source.timezone))
    with Store(settings, now) as store:
        store.apply_filter(settings.filters)
        store.expire_old()
        articles = store.articles()
        removed_ids = store.removed_ids()
        last_success = store.get("last_success_at")
        new_article_ids = set(json.loads(store.get("new_article_ids", "[]")))
    settings.output.mkdir(parents=True, exist_ok=True)
    # Public files should inherit the website directory's read permissions.
    # mkdtemp uses owner-only permissions, which breaks a separate preview/Caddy user.
    release = settings.output / f"release-{uuid4().hex}"
    release.mkdir()
    (release / "posts").mkdir()
    (release / "archive").mkdir()
    shutil.copytree(PACKAGE / "static", release / "assets")
    env = Environment(loader=FileSystemLoader(PACKAGE / "templates"), autoescape=select_autoescape(["html"]))
    env.filters["date"] = lambda value, fmt="%m-%d %H:%M": datetime.fromisoformat(value).strftime(fmt) if value else "尚未更新"
    groups = defaultdict(list)
    search_articles = []
    for article in articles:
        article["is_new"] = article["id"] in new_article_ids
        content = BeautifulSoup(article["body_html"], "html.parser")
        if "防水图" in content.get_text():
            for image in content.select("img"):
                image.decompose()
            article["image_count"] = 0
        if clean_display_text(content):
            article["body_html"] = str(content)
            article["summary"] = " ".join(content.get_text(" ", strip=True).split())[:180]
        edit_note = re.search(r"此[貼帖]由[^\r\n]+?重新[編编][輯辑][：:]\s*\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}\s*$", content.get_text())
        # Ignore terminal forum edit metadata, including text split across tags.
        ignored_suffix = len(edit_note[0]) if edit_note else 0
        trailing_images = []
        for node in reversed(list(content.descendants)):
            if node.name == "img":
                trailing_images.append(node)
            elif type(node) is NavigableString:
                text = node[:max(0, len(node) - ignored_suffix)]
                ignored_suffix = max(0, ignored_suffix - len(node))
                if text.strip():
                    break
        if trailing_images:
            for image in trailing_images:
                image.decompose()
            article["body_html"] = str(content)
            article["image_count"] = len(content.select("img"))
        search_articles.append({**{key: article[key] for key in ("id", "title", "summary", "published_at", "image_count", "is_new")},
                                "text": content.get_text(" ", strip=True)})
        groups[article["published_at"][:7]].append(article)
    (release / "assets/search-index.js").write_text(
        "window.archiveSearchIndex = " + json.dumps({"pageSize": settings.site.page_size, "articles": search_articles},
                                                   ensure_ascii=False, separators=(",", ":")) + ";\n", encoding="utf-8")
    months = sorted(groups, reverse=True)
    common = dict(site=settings.site, total=len(articles), months=months, last_success=last_success,
                  generated_at=now.isoformat(), keyword_rules=settings.filters.keywords,
                  asset_version=release.name, base_path=base_path)

    def write(path, template, **context):
        (release / path).write_text(env.get_template(template).render(**common, **context), encoding="utf-8")

    def listing(items, *, month=None):
        size = settings.site.page_size
        pages = max(1, (len(items) + size - 1) // size)
        for page in range(1, pages + 1):
            if month:
                filename = f"archive/{month}.html" if page == 1 else f"archive/{month}-{page}.html"
                link = lambda number: f"{base_path}/archive/{month}.html" if number == 1 else f"{base_path}/archive/{month}-{number}.html"
            else:
                filename = "index.html" if page == 1 else f"page-{page}.html"
                link = lambda number: f"{base_path}/" if number == 1 else f"{base_path}/page-{number}.html"
            write(filename, "index.html", articles=items[(page - 1) * size:page * size],
                  month=month, next_page=link(page + 1) if page < pages else None)

    listing(articles)
    for month in months:
        listing(groups[month], month=month)
    write("search.html", "search.html", month=None, next_page=None)
    for article in articles:
        write(f"posts/{article['id']}.html", "post.html", article=article, month=article["published_at"][:7])
    write("404.html", "404.html", month=None)
    # Never copy the SQLite state or source pages into the public release.
    manifest = settings.output / "current.json"
    temporary = settings.output / "current.json.tmp"
    temporary.write_text(json.dumps({"release": release.name}, ensure_ascii=False), encoding="utf-8")
    temporary.replace(manifest)
    # Purge matching HTML from previous generated versions only after the new
    # release is complete. Never traverse a symlink outside the website output.
    output = settings.output.resolve()
    for page in settings.output.glob("release-*/posts/*.html"):
        if page.stem in removed_ids and page.resolve().is_relative_to(output):
            page.unlink()
    if removed_ids:
        # Old full-text indexes must not retain removed articles after a rebuild.
        for index in settings.output.glob("release-*/assets/search-index.js"):
            if index.parent.parent != release and index.resolve().is_relative_to(output):
                index.unlink()
    return release


def current_release(output: Path) -> Path:
    manifest = json.loads((output / "current.json").read_text(encoding="utf-8"))
    path = (output / manifest["release"]).resolve()
    if path.parent != output.resolve() or not path.is_dir():
        raise ValueError("无效的网站版本目录")
    return path


class ArchiveHandler(SimpleHTTPRequestHandler):
    def __init__(self, *args, output: Path, analytics_endpoint: str = "", **kwargs):
        self.output = output
        self.analytics_endpoint = analytics_endpoint
        super().__init__(*args, directory=str(current_release(output)), **kwargs)

    def do_GET(self):
        self.directory = str(current_release(self.output))
        super().do_GET()

    def do_HEAD(self):
        self.directory = str(current_release(self.output))
        super().do_HEAD()

    def list_directory(self, path):
        self.send_error(404)
        return None

    def end_headers(self):
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        connect_src = "'self'"
        if self.analytics_endpoint:
            endpoint = urlsplit(self.analytics_endpoint)
            connect_src += f" {endpoint.scheme}://{endpoint.netloc}"
        self.send_header("Content-Security-Policy", f"default-src 'none'; script-src 'self'; connect-src {connect_src}; style-src 'self'; img-src https: http:; base-uri 'none'; frame-ancestors 'none'; form-action 'none'")
        super().end_headers()


class ArchiveServer(ThreadingHTTPServer):
    allow_reuse_address = not hasattr(socket, "SO_EXCLUSIVEADDRUSE")

    def server_bind(self):
        if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        super().server_bind()


def serve(settings: Settings, host: str, port: int, *, handler_class=ArchiveHandler):
    current_release(settings.output)
    handler = partial(handler_class, output=settings.output, analytics_endpoint=settings.site.analytics_endpoint)
    with ArchiveServer((host, port), handler) as server:
        print(f"本地预览：http://{host}:{port}（Ctrl+C 停止）", flush=True)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass
