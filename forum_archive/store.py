from contextlib import AbstractContextManager
from datetime import datetime, timedelta
import sqlite3

from .config import Settings, TitleFilter
from .source import Post, Thread


class Store(AbstractContextManager):
    def __init__(self, settings: Settings, now: datetime):
        settings.database.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(settings.database)
        self.db.row_factory = sqlite3.Row
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS threads (
                id TEXT PRIMARY KEY, url TEXT NOT NULL, title TEXT NOT NULL,
                listed_at TEXT, first_seen TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'pending',
                published_at TEXT, collected_at TEXT, body_html TEXT, summary TEXT,
                image_count INTEGER NOT NULL DEFAULT 0, attempts INTEGER NOT NULL DEFAULT 0,
                last_error TEXT
            );
            CREATE INDEX IF NOT EXISTS threads_status ON threads(status, attempts);
        """)
        source = self.get("source_url")
        if source and source != settings.source.list_url:
            self.db.close()
            raise ValueError("采集来源与现有状态不一致，请为新来源使用独立数据库")
        if not source:
            self.set("source_url", settings.source.list_url)
            self.set("archive_start", (now - timedelta(days=settings.crawl.initial_days)).isoformat())
            self.set("backfill_page", "1")
            self.set("recent_page", "1")
        self.cutoff = max(datetime.fromisoformat(self.get("archive_start")), now - timedelta(days=30))

    def __exit__(self, *args):
        self.db.close()

    def get(self, key: str, default: str = "") -> str:
        row = self.db.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
        return row[0] if row else default

    def set(self, key: str, value: str):
        with self.db:
            self.db.execute("INSERT INTO meta VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, value))

    def discover(self, thread: Thread, now: datetime):
        with self.db:
            self.db.execute("""INSERT INTO threads(id,url,title,listed_at,first_seen) VALUES (?,?,?,?,?)
                ON CONFLICT(id) DO UPDATE SET url=excluded.url, listed_at=COALESCE(excluded.listed_at,threads.listed_at)
            """, (thread.id, thread.url, thread.title, thread.listed_at, now.isoformat()))

    def apply_filter(self, rules: TitleFilter) -> int:
        changes = 0
        with self.db:
            for row in self.db.execute("SELECT id,title,status,body_html FROM threads").fetchall():
                if rules.blocks(row["title"]):
                    if row["status"] != "blocked":
                        self.db.execute("UPDATE threads SET status='blocked' WHERE id=?", (row["id"],))
                        changes += 1
                elif row["status"] == "blocked":
                    state = "collected" if row["body_html"] else "pending"
                    self.db.execute("UPDATE threads SET status=?,attempts=0,last_error=NULL WHERE id=?", (state, row["id"]))
        return changes

    def pending(self, limit: int, max_attempts: int):
        return self.db.execute("""SELECT * FROM threads WHERE status='pending' AND attempts < ?
            ORDER BY COALESCE(listed_at,first_seen) DESC,id DESC LIMIT ?""", (max_attempts, limit)).fetchall()

    def expire_old(self) -> int:
        expired = []
        for row in self.db.execute("SELECT id,published_at,listed_at FROM threads"):
            date = row["published_at"] or row["listed_at"]
            if date and datetime.fromisoformat(date) < self.cutoff:
                expired.append((row["id"],))
        with self.db:
            self.db.executemany("""UPDATE threads SET status='expired',body_html=NULL,summary=NULL,
                image_count=0,last_error=NULL WHERE id=?""", expired)
        return len(expired)

    def save_post(self, thread_id: str, post: Post, now: datetime, blocked: bool):
        status = "blocked" if blocked else ("expired" if datetime.fromisoformat(post.published_at) < self.cutoff else "collected")
        with self.db:
            self.db.execute("""UPDATE threads SET title=?,published_at=?,collected_at=?,body_html=?,summary=?,image_count=?,status=?,last_error=NULL
                WHERE id=?""", (post.title, post.published_at, now.isoformat(),
                              post.body_html if status == "collected" else None,
                              post.summary if status == "collected" else None,
                              post.image_count if status == "collected" else 0, status, thread_id))
        return status

    def fail(self, thread_id: str, error: str):
        with self.db:
            self.db.execute("UPDATE threads SET attempts=attempts+1,last_error=? WHERE id=?", (error[:300], thread_id))

    def retry_failed(self):
        with self.db:
            self.db.execute("UPDATE threads SET attempts=0,last_error=NULL WHERE status='pending'")

    def articles(self):
        return [dict(row) for row in self.db.execute("SELECT * FROM threads WHERE status='collected' ORDER BY published_at DESC,id DESC")]

    def removed_ids(self):
        return {row[0] for row in self.db.execute("SELECT id FROM threads WHERE status IN ('blocked','expired')")}

    def counts(self):
        return dict(self.db.execute("SELECT status,COUNT(*) FROM threads GROUP BY status").fetchall())
