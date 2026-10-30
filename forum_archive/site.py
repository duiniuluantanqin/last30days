from collections import defaultdict
from datetime import datetime
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import re
import shutil
from uuid import uuid4
from zoneinfo import ZoneInfo

from bs4 import BeautifulSoup, NavigableString
from jinja2 import Environment, FileSystemLoader, select_autoescape

from .config import Settings
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
        likes = re.search(r"赞\s*[(（]\s*\d+\s*[)）]\s*$", content.get_text())
        if likes:
            offset = 0
            for node in list(content.strings):
                text = str(node)
                node.replace_with(text[:max(0, likes.start() - offset)])
                offset += len(text)
            article["body_html"] = str(content)
            article["summary"] = " ".join(content.get_text(" ", strip=True).split())[:180]
        if "防水图" in content.get_text():
            for image in content.select("img"):
                image.decompose()
            # Use text offsets so a marker split across tags is removed too.
            removed = {index for match in re.finditer("防水图", content.get_text())
                       for index in range(*match.span())}
            offset = 0
            for node in list(content.strings):
                text = str(node)
                node.replace_with("".join(char for index, char in enumerate(text, offset) if index not in removed))
                offset += len(text)
            article["body_html"] = str(content)
            article["summary"] = " ".join(content.get_text(" ", strip=True).split())[:180]
            article["image_count"] = 0
        elif sum(not char.isspace() for char in content.get_text()) >= 200:
            trailing_images = []
            for node in reversed(list(content.descendants)):
                if node.name == "img":
                    trailing_images.append(node)
                elif type(node) is NavigableString and node.strip():
                    break
            if trailing_images and len(trailing_images) == len(content.select("img")):
                for image in trailing_images:
                    image.decompose()
                article["body_html"] = str(content)
                article["image_count"] = 0
        search_articles.append({**{key: article[key] for key in ("id", "title", "summary", "published_at", "image_count", "is_new")},
                                "text": content.get_text(" ", strip=True)})
        groups[article["published_at"][:7]].append(article)
    (release / "assets/search-index.js").write_text(
        "window.archiveSearchIndex = " + json.dumps({"pageSize": settings.site.page_size, "articles": search_articles},
                                                   ensure_ascii=False, separators=(",", ":")) + ";\n", encoding="utf-8")
    months = sorted(groups, reverse=True)
    common = dict(site=settings.site, total=len(articles), months=months, last_success=last_success,
                  source_url=settings.source.list_url, generated_at=now.isoformat(), keyword_rules=settings.filters.keywords,
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
    def __init__(self, *args, output: Path, **kwargs):
        self.output = output
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
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Content-Security-Policy", "default-src 'none'; script-src 'self'; connect-src 'self'; style-src 'self'; img-src https: http:; base-uri 'none'; frame-ancestors 'none'; form-action 'none'")
        super().end_headers()


def serve(settings: Settings, host: str, port: int):
    current_release(settings.output)
    handler = partial(ArchiveHandler, output=settings.output)
    with ThreadingHTTPServer((host, port), handler) as server:
        print(f"本地预览：http://{host}:{port}（Ctrl+C 停止）", flush=True)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass
