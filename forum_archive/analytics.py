"""First-party site counters, stored separately from replaceable archive snapshots."""

from contextlib import closing
from datetime import datetime
from functools import partial
from http.server import BaseHTTPRequestHandler
import json
from pathlib import Path
import re
import sqlite3
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

from .site import ArchiveServer


TIMEZONE = ZoneInfo("Asia/Shanghai")


class AnalyticsStore:
    def __init__(self, database: Path):
        self.database = database
        database.parent.mkdir(parents=True, exist_ok=True)
        with closing(sqlite3.connect(database)) as db, db:
            db.execute("""CREATE TABLE IF NOT EXISTS visits (
                day TEXT NOT NULL, visitor TEXT NOT NULL, pv INTEGER NOT NULL,
                PRIMARY KEY (day, visitor)
            )""")

    @staticmethod
    def _counts(db, day):
        row = db.execute("""SELECT COUNT(DISTINCT NULLIF(visitor, '')), COALESCE(SUM(pv), 0),
            COUNT(DISTINCT CASE WHEN day=? THEN NULLIF(visitor, '') END),
            COALESCE(SUM(CASE WHEN day=? THEN pv ELSE 0 END), 0) FROM visits""", (day, day)).fetchone()
        return {"today": {"date": day, "uv": row[2], "pv": row[3]}, "total": {"uv": row[0], "pv": row[1]}}

    def counts(self, *, now=None):
        day = (now or datetime.now(TIMEZONE)).astimezone(TIMEZONE).date().isoformat()
        with closing(sqlite3.connect(self.database, timeout=10)) as db:
            return self._counts(db, day)

    def record(self, visitor: str | None, *, now=None):
        day = (now or datetime.now(TIMEZONE)).astimezone(TIMEZONE).date().isoformat()
        with closing(sqlite3.connect(self.database, timeout=10)) as db, db:
            db.execute("""INSERT INTO visits(day, visitor, pv) VALUES (?, ?, 1)
                ON CONFLICT(day, visitor) DO UPDATE SET pv=pv+1""", (day, visitor or ""))
            return self._counts(db, day)


class AnalyticsHandler(BaseHTTPRequestHandler):
    def __init__(self, *args, store: AnalyticsStore, origin: str, **kwargs):
        self.store = store
        self.origin = origin
        super().__init__(*args, **kwargs)

    def setup(self):
        super().setup()
        self.connection.settimeout(10)

    def _check_request(self):
        if urlsplit(self.path).path != "/api/analytics":
            self.send_error(404)
            return False
        if self.headers.get("Origin") != self.origin:
            self.send_error(403)
            return False
        return True

    def end_headers(self):
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Vary", "Origin")
        if self.headers.get("Origin") == self.origin:
            self.send_header("Access-Control-Allow-Origin", self.origin)
        super().end_headers()

    def do_OPTIONS(self):
        if self._check_request():
            self.send_response(204)
            self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
            self.send_header("Access-Control-Allow-Headers", "Content-Type")
            self.send_header("Access-Control-Max-Age", "86400")
            self.end_headers()

    def _respond(self, counts):
        content = json.dumps(counts, separators=(",", ":")).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(content)))
        self.end_headers()
        self.wfile.write(content)

    def do_GET(self):
        if self._check_request():
            try:
                self._respond(self.store.counts())
            except sqlite3.Error:
                self.send_error(503)

    def do_POST(self):
        if not self._check_request():
            return
        if self.headers.get("Content-Type", "").split(";")[0].strip().lower() != "application/json":
            self.send_error(415)
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            self.send_error(400)
            return
        if not 0 < length <= 1024:
            self.send_error(413 if length > 1024 else 400)
            return
        try:
            payload = json.loads(self.rfile.read(length))
        except (ValueError, UnicodeDecodeError):
            self.send_error(400)
            return
        if not isinstance(payload, dict) or "visitor" not in payload:
            self.send_error(400)
            return
        visitor = payload["visitor"]
        if visitor is not None and (not isinstance(visitor, str) or not re.fullmatch(r"[a-f0-9]{32}", visitor)):
            self.send_error(400)
            return
        try:
            self._respond(self.store.record(visitor))
        except sqlite3.Error:
            self.send_error(503)

    def log_message(self, format, *args):
        # The database stores random browser IDs; don't add visitor IPs to access logs.
        pass


def serve_analytics(database: Path, origin: str, host: str, port: int):
    if not origin:
        raise ValueError("请先设置 site.analytics_origin 为 Pages 站点来源")
    store = AnalyticsStore(database)
    handler = partial(AnalyticsHandler, store=store, origin=origin)
    with ArchiveServer((host, port), handler) as server:
        print(f"访问统计接口：http://{host}:{port}/api/analytics（Ctrl+C 停止）", flush=True)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass
