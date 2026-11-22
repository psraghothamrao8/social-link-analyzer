"""In-memory snapshot of the database that every analysis works on (rebuilt when the DB changes)."""
from __future__ import annotations

import threading
from collections import defaultdict
from dataclasses import dataclass, field

from ..collectors.crossplatform import extract_domains, extract_user_mentions
from ..db import Database


@dataclass
class Item:
    id: str
    kind: str  # "post" | "comment"
    platform: str
    account_id: str | None
    community_id: str | None
    post_id: str
    parent_id: str | None
    created_utc: float | None
    score: int | None
    title: str
    body: str
    url: str | None

    @property
    def text(self) -> str:
        return f"{self.title}\n{self.body}".strip() if self.title else (self.body or "")


@dataclass
class Corpus:
    version: int
    accounts: dict[str, dict]
    communities: dict[str, dict]
    posts: dict[str, dict]
    comments: dict[str, dict]
    external_links: list[dict]
    manual_links: list[dict]
    items: dict[str, Item] = field(default_factory=dict)
    by_account: dict[str, list[Item]] = field(default_factory=lambda: defaultdict(list))
    by_post: dict[str, list[Item]] = field(default_factory=lambda: defaultdict(list))
    children: dict[str, list[Item]] = field(default_factory=lambda: defaultdict(list))
    crossposts: dict[str, list[dict]] = field(default_factory=lambda: defaultdict(list))
    ext_by_account: dict[str, list[dict]] = field(default_factory=lambda: defaultdict(list))

    @classmethod
    def load(cls, db: Database) -> "Corpus":
        version = db.version
        c = cls(
            version=version,
            accounts={r["id"]: r for r in db.query("SELECT * FROM accounts")},
            communities={r["id"]: r for r in db.query("SELECT * FROM communities")},
            posts={r["id"]: r for r in db.query("SELECT * FROM posts")},
            comments={r["id"]: r for r in db.query("SELECT * FROM comments")},
            external_links=db.query("SELECT * FROM external_links"),
            manual_links=db.query("SELECT * FROM manual_links"),
        )
        for p in c.posts.values():
            it = Item(p["id"], "post", p["platform"], p["account_id"], p["community_id"], p["id"], None,
                      p["created_utc"], p["score"], p["title"] or "", p["body"] or "", p["url"])
            c.items[it.id] = it
            if p["crosspost_parent"]:
                c.crossposts[p["crosspost_parent"]].append(p)
        for m in c.comments.values():
            it = Item(m["id"], "comment", m["platform"], m["account_id"], m["community_id"], m["post_id"], m["parent_id"],
                      m["created_utc"], m["score"], "", m["body"] or "", None)
            c.items[it.id] = it
            c.by_post[it.post_id].append(it)
            if it.parent_id:
                c.children[it.parent_id].append(it)
        for it in c.items.values():
            # stub posts (title only, no timestamp) carry no authored content of their own
            if it.account_id and not (it.kind == "post" and c.posts[it.id].get("is_stub")):
                c.by_account[it.account_id].append(it)
        for lst in list(c.by_account.values()) + list(c.by_post.values()) + list(c.children.values()):
            lst.sort(key=lambda i: i.created_utc or 0)
        for l in c.external_links:
            c.ext_by_account[l["account_id"]].append(l)
        return c

    # ---------------------------------------------------------------- labels
    def account_label(self, aid: str | None) -> str:
        if not aid:
            return "[deleted]"
        a = self.accounts.get(aid)
        platform, _, name = aid.partition(":")
        name = a["username"] if a else name
        return f"u/{name}" if platform == "reddit" else f"{platform}:@{name}"

    def community_label(self, cid: str | None) -> str:
        if not cid:
            return "?"
        c = self.communities.get(cid)
        platform, _, name = cid.partition(":")
        name = c["name"] if c else name.removeprefix("c/")
        return f"r/{name}" if platform == "reddit" else f"{platform}:{name}"

    def author_of(self, item_id: str | None) -> str | None:
        it = self.items.get(item_id) if item_id else None
        if it:
            return it.account_id
        p = self.posts.get(item_id) if item_id else None
        return p["account_id"] if p else None

    def mentions(self, it: Item) -> list[str]:
        return [f"reddit:{m}" for m in extract_user_mentions(it.text)]

    def domains(self, it: Item) -> list[str]:
        out = extract_domains(it.text)
        if it.url:
            out += extract_domains(it.url)
        return out


class CorpusCache:
    def __init__(self, db: Database):
        self.db = db
        self._lock = threading.Lock()
        self._corpus: Corpus | None = None
        self.derived: dict = {}  # analysis caches keyed by name, cleared with the corpus

    def get(self) -> Corpus:
        with self._lock:
            if self._corpus is None or self._corpus.version != self.db.version:
                self._corpus = Corpus.load(self.db)
                self.derived = {}
            return self._corpus
