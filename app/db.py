"""SQLite storage. Every record is platform-scoped so Reddit and other networks share one model.

ID conventions
  account    "<platform>:<username lowercased>"         e.g. reddit:spez, twitter:jack
  community  "<platform>:c/<name lowercased>"           e.g. reddit:c/buildapc
  post       "<platform>:<native id>"                   e.g. reddit:t3_abc123
  comment    "<platform>:<native id>"                   e.g. reddit:t1_def456
"""
from __future__ import annotations

import sqlite3
import threading
import time
from pathlib import Path
from typing import Any, Iterable

SCHEMA = """
CREATE TABLE IF NOT EXISTS accounts (
  id TEXT PRIMARY KEY,
  platform TEXT NOT NULL,
  username TEXT NOT NULL,
  created_utc REAL,
  link_karma INTEGER,
  comment_karma INTEGER,
  bio TEXT,
  status TEXT,
  fetched_utc REAL,
  source TEXT
);
CREATE TABLE IF NOT EXISTS communities (
  id TEXT PRIMARY KEY,
  platform TEXT NOT NULL,
  name TEXT NOT NULL,
  subscribers INTEGER,
  description TEXT
);
CREATE TABLE IF NOT EXISTS posts (
  id TEXT PRIMARY KEY,
  platform TEXT NOT NULL,
  account_id TEXT,
  community_id TEXT,
  title TEXT,
  body TEXT,
  url TEXT,
  created_utc REAL,
  score INTEGER,
  num_comments INTEGER,
  upvote_ratio REAL,
  crosspost_parent TEXT,
  permalink TEXT,
  is_stub INTEGER DEFAULT 0
);
CREATE TABLE IF NOT EXISTS comments (
  id TEXT PRIMARY KEY,
  platform TEXT NOT NULL,
  account_id TEXT,
  post_id TEXT,
  parent_id TEXT,
  community_id TEXT,
  body TEXT,
  created_utc REAL,
  score INTEGER
);
CREATE TABLE IF NOT EXISTS external_links (
  account_id TEXT NOT NULL,
  platform TEXT NOT NULL,
  handle TEXT NOT NULL,
  url TEXT,
  source TEXT,
  evidence_id TEXT,
  hits INTEGER DEFAULT 1,
  PRIMARY KEY (account_id, platform, handle, source)
);
CREATE TABLE IF NOT EXISTS manual_links (
  a TEXT NOT NULL,
  b TEXT NOT NULL,
  relation TEXT NOT NULL,
  note TEXT,
  created_utc REAL,
  PRIMARY KEY (a, b, relation)
);
CREATE TABLE IF NOT EXISTS watch_topics (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  name TEXT NOT NULL,
  keywords TEXT,
  domains TEXT,
  communities TEXT,
  interval_min INTEGER DEFAULT 30,
  active INTEGER DEFAULT 1,
  created_utc REAL,
  last_run_utc REAL,
  last_status TEXT
);
CREATE TABLE IF NOT EXISTS watched_posts (
  post_id TEXT PRIMARY KEY,
  added_utc REAL,
  until_utc REAL,
  interval_min INTEGER DEFAULT 5,
  last_poll_utc REAL,
  topic_id INTEGER
);
CREATE TABLE IF NOT EXISTS score_snapshots (
  post_id TEXT NOT NULL,
  ts REAL NOT NULL,
  score INTEGER,
  num_comments INTEGER,
  upvote_ratio REAL,
  PRIMARY KEY (post_id, ts)
);
CREATE INDEX IF NOT EXISTS idx_posts_account ON posts(account_id);
CREATE INDEX IF NOT EXISTS idx_posts_comm ON posts(community_id);
CREATE INDEX IF NOT EXISTS idx_comments_account ON comments(account_id);
CREATE INDEX IF NOT EXISTS idx_comments_post ON comments(post_id);
"""


def account_id(platform: str, username: str | None) -> str | None:
    if not username or username in ("[deleted]", "[removed]", "AutoModerator"):
        return None
    return f"{platform.lower()}:{username.strip().lstrip('@').lower()}"


def community_id(platform: str, name: str | None) -> str | None:
    if not name:
        return None
    name = name.strip()
    for prefix in ("/r/", "r/"):
        if name.lower().startswith(prefix):
            name = name[len(prefix):]
    # "c/" keeps community ids from ever colliding with account ids (usernames cannot contain "/").
    return f"{platform.lower()}:c/{name.lower()}"


class Database:
    def __init__(self, path: Path | str):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(str(self.path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.executescript(SCHEMA)
        cols = {r[1] for r in self._conn.execute("PRAGMA table_info(accounts)")}
        if "verified_email" not in cols:  # migration for databases created before bot scoring
            self._conn.execute("ALTER TABLE accounts ADD COLUMN verified_email INTEGER")
        self._conn.commit()
        self.version = 0  # bumped on every write so analysis caches can invalidate

    # ------------------------------------------------------------------ helpers
    def _write(self, sql: str, rows: Iterable[tuple], bump: bool = True) -> None:
        rows = list(rows)
        if not rows:
            return
        with self._lock:
            self._conn.executemany(sql, rows)
            self._conn.commit()
            if bump:
                self.version += 1

    def query(self, sql: str, params: tuple = ()) -> list[dict[str, Any]]:
        with self._lock:
            return [dict(r) for r in self._conn.execute(sql, params).fetchall()]

    def execute(self, sql: str, params: tuple = (), bump: bool = False) -> int:
        with self._lock:
            cur = self._conn.execute(sql, params)
            self._conn.commit()
            if bump:
                self.version += 1
            return cur.lastrowid

    def reset(self) -> None:
        with self._lock:
            for table in ("accounts", "communities", "posts", "comments", "external_links", "manual_links",
                          "watch_topics", "watched_posts", "score_snapshots"):
                self._conn.execute(f"DELETE FROM {table}")
            self._conn.commit()
            self.version += 1

    # ------------------------------------------------------------------ monitoring
    def add_snapshots(self, rows: Iterable[dict]) -> None:
        """Score/comment counts over time. Kept out of the analysis cache version on purpose."""
        self._write(
            "INSERT OR REPLACE INTO score_snapshots (post_id, ts, score, num_comments, upvote_ratio) VALUES (?,?,?,?,?)",
            ((r["post_id"], r["ts"], r.get("score"), r.get("num_comments"), r.get("upvote_ratio")) for r in rows if r.get("post_id")),
            bump=False,
        )

    def snapshots(self, post_id: str) -> list[dict]:
        return self.query("SELECT ts, score, num_comments, upvote_ratio FROM score_snapshots WHERE post_id=? ORDER BY ts", (post_id,))

    def snapshot_post_ids(self) -> set[str]:
        return {r["post_id"] for r in self.query("SELECT DISTINCT post_id FROM score_snapshots")}

    def update_post_counts(self, rows: Iterable[dict]) -> None:
        self._write("UPDATE posts SET score=?, num_comments=?, upvote_ratio=COALESCE(?, upvote_ratio) WHERE id=?",
                    ((r.get("score"), r.get("num_comments"), r.get("upvote_ratio"), r["id"]) for r in rows), bump=False)

    def watch_post(self, post_id: str, hours: float = 48, interval_min: int = 5, topic_id: int | None = None) -> None:
        now = time.time()
        self._write(
            """INSERT INTO watched_posts (post_id, added_utc, until_utc, interval_min, topic_id) VALUES (?,?,?,?,?)
               ON CONFLICT(post_id) DO UPDATE SET until_utc=MAX(watched_posts.until_utc, excluded.until_utc)""",
            [(post_id, now, now + hours * 3600, interval_min, topic_id)], bump=False)

    def unwatch_post(self, post_id: str) -> None:
        self.execute("DELETE FROM watched_posts WHERE post_id=?", (post_id,))

    def watched_posts(self) -> list[dict]:
        return self.query("SELECT * FROM watched_posts ORDER BY added_utc DESC")

    def due_watched_posts(self, now: float) -> list[dict]:
        return self.query(
            "SELECT * FROM watched_posts WHERE until_utc > ? AND (last_poll_utc IS NULL OR ? - last_poll_utc >= interval_min * 60)",
            (now, now))

    def mark_polled(self, post_ids: Iterable[str], now: float) -> None:
        self._write("UPDATE watched_posts SET last_poll_utc=? WHERE post_id=?", ((now, p) for p in post_ids), bump=False)

    def topics(self) -> list[dict]:
        return self.query("SELECT * FROM watch_topics ORDER BY id")

    def topic(self, topic_id: int) -> dict | None:
        rows = self.query("SELECT * FROM watch_topics WHERE id=?", (topic_id,))
        return rows[0] if rows else None

    def save_topic(self, t: dict) -> int:
        fields = (t.get("name") or "Untitled", t.get("keywords") or "", t.get("domains") or "", t.get("communities") or "",
                  int(t.get("interval_min") or 30), 1 if t.get("active", True) else 0)
        if t.get("id"):
            self.execute("UPDATE watch_topics SET name=?, keywords=?, domains=?, communities=?, interval_min=?, active=? WHERE id=?",
                         (*fields, int(t["id"])))
            return int(t["id"])
        return self.execute(
            "INSERT INTO watch_topics (name, keywords, domains, communities, interval_min, active, created_utc) VALUES (?,?,?,?,?,?,?)",
            (*fields, time.time()))

    def delete_topic(self, topic_id: int) -> None:
        self.execute("DELETE FROM watch_topics WHERE id=?", (topic_id,))

    def topic_ran(self, topic_id: int, status: str) -> None:
        self.execute("UPDATE watch_topics SET last_run_utc=?, last_status=? WHERE id=?", (time.time(), status, topic_id))

    # ------------------------------------------------------------------ upserts
    def upsert_accounts(self, accounts: Iterable[dict]) -> None:
        """Insert or update accounts. Fields that are None never overwrite known values."""
        now = time.time()
        self._write(
            """INSERT INTO accounts (id, platform, username, created_utc, link_karma, comment_karma, bio, status, fetched_utc, source, verified_email)
               VALUES (?,?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT(id) DO UPDATE SET
                 verified_email=COALESCE(excluded.verified_email, accounts.verified_email),
                 username=excluded.username,
                 created_utc=COALESCE(excluded.created_utc, accounts.created_utc),
                 link_karma=COALESCE(excluded.link_karma, accounts.link_karma),
                 comment_karma=COALESCE(excluded.comment_karma, accounts.comment_karma),
                 bio=COALESCE(excluded.bio, accounts.bio),
                 status=COALESCE(excluded.status, accounts.status),
                 fetched_utc=COALESCE(excluded.fetched_utc, accounts.fetched_utc),
                 source=COALESCE(excluded.source, accounts.source)""",
            (
                (
                    a["id"], a["platform"], a["username"], a.get("created_utc"), a.get("link_karma"),
                    a.get("comment_karma"), a.get("bio"), a.get("status"), a.get("fetched_utc", now), a.get("source"),
                    None if a.get("verified_email") is None else int(bool(a.get("verified_email"))),
                )
                for a in accounts
                if a.get("id")
            ),
        )

    def ensure_accounts(self, ids: Iterable[tuple[str, str, str]]) -> None:
        """Create placeholder accounts (id, platform, username) without touching existing rows."""
        self._write(
            "INSERT OR IGNORE INTO accounts (id, platform, username, fetched_utc) VALUES (?,?,?,NULL)",
            (i for i in ids if i[0]),
        )

    def upsert_communities(self, comms: Iterable[dict]) -> None:
        self._write(
            """INSERT INTO communities (id, platform, name, subscribers, description) VALUES (?,?,?,?,?)
               ON CONFLICT(id) DO UPDATE SET
                 subscribers=COALESCE(excluded.subscribers, communities.subscribers),
                 description=COALESCE(excluded.description, communities.description)""",
            ((c["id"], c["platform"], c["name"], c.get("subscribers"), c.get("description")) for c in comms if c.get("id")),
        )

    def upsert_posts(self, posts: Iterable[dict], stub: bool = False) -> None:
        """Full posts replace stubs; stubs (seen only via a comment's parent link) never replace full posts."""
        cols = "(id, platform, account_id, community_id, title, body, url, created_utc, score, num_comments, upvote_ratio, crosspost_parent, permalink, is_stub)"
        if stub:
            sql = f"INSERT OR IGNORE INTO posts {cols} VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,1)"
        else:
            sql = f"INSERT OR REPLACE INTO posts {cols} VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,0)"
        self._write(
            sql,
            (
                (
                    p["id"], p["platform"], p.get("account_id"), p.get("community_id"), p.get("title"), p.get("body"),
                    p.get("url"), p.get("created_utc"), p.get("score"), p.get("num_comments"), p.get("upvote_ratio"),
                    p.get("crosspost_parent"), p.get("permalink"),
                )
                for p in posts
                if p.get("id")
            ),
        )

    def upsert_comments(self, comments: Iterable[dict]) -> None:
        self._write(
            """INSERT OR REPLACE INTO comments (id, platform, account_id, post_id, parent_id, community_id, body, created_utc, score)
               VALUES (?,?,?,?,?,?,?,?,?)""",
            (
                (
                    c["id"], c["platform"], c.get("account_id"), c.get("post_id"), c.get("parent_id"),
                    c.get("community_id"), c.get("body"), c.get("created_utc"), c.get("score"),
                )
                for c in comments
                if c.get("id")
            ),
        )

    def add_external_links(self, links: Iterable[dict]) -> None:
        self._write(
            """INSERT INTO external_links (account_id, platform, handle, url, source, evidence_id, hits) VALUES (?,?,?,?,?,?,1)
               ON CONFLICT(account_id, platform, handle, source) DO UPDATE SET hits = external_links.hits + 1""",
            (
                (l["account_id"], l["platform"], l["handle"].lower(), l.get("url"), l.get("source"), l.get("evidence_id"))
                for l in links
                if l.get("account_id") and l.get("handle")
            ),
        )

    def add_manual_link(self, a: str, b: str, relation: str, note: str = "") -> None:
        self._write(
            "INSERT OR REPLACE INTO manual_links (a, b, relation, note, created_utc) VALUES (?,?,?,?,?)",
            [(a, b, relation, note, time.time())],
        )

    def delete_manual_link(self, a: str, b: str, relation: str) -> None:
        self._write("DELETE FROM manual_links WHERE a=? AND b=? AND relation=?", [(a, b, relation)])

    def counts(self) -> dict[str, int]:
        out = {}
        for table in ("accounts", "communities", "posts", "comments", "external_links", "manual_links",
                      "watch_topics", "watched_posts", "score_snapshots"):
            out[table] = self.query(f"SELECT COUNT(*) AS n FROM {table}")[0]["n"]
        out["platforms"] = [r["platform"] for r in self.query("SELECT DISTINCT platform FROM accounts ORDER BY platform")]
        return out
