"""Reddit collector.

Uses OAuth app-only auth when REDDIT_CLIENT_ID / REDDIT_CLIENT_SECRET are set (recommended,
~100 requests/minute) and otherwise the public .json endpoints (slow, may be blocked).
Reddit listings return at most ~1000 recent items per user/subreddit; older history needs an
archive import (see importer.py).
"""
from __future__ import annotations

import re
import time
from typing import Any, Callable, Iterator

import httpx

from .. import config
from ..db import Database, account_id, community_id
from .crossplatform import extract_user_mentions, links_for_account

PLATFORM = "reddit"


class RedditError(RuntimeError):
    pass


class RedditClient:
    def __init__(
        self,
        client_id: str = config.REDDIT_CLIENT_ID,
        client_secret: str = config.REDDIT_CLIENT_SECRET,
        user_agent: str = config.REDDIT_USER_AGENT,
    ):
        self.client_id = client_id
        self.client_secret = client_secret
        self.oauth = bool(client_id and client_secret)
        self.min_interval = 0.65 if self.oauth else 2.0
        self.http = httpx.Client(headers={"User-Agent": user_agent}, timeout=30.0, follow_redirects=True)
        self._token: str | None = None
        self._token_exp = 0.0
        self._last_call = 0.0
        self.requests_made = 0

    # ----------------------------------------------------------------- transport
    def _ensure_token(self) -> None:
        if self._token and time.time() < self._token_exp - 60:
            return
        r = self.http.post(
            "https://www.reddit.com/api/v1/access_token",
            data={"grant_type": "client_credentials"},
            auth=(self.client_id, self.client_secret),
        )
        if r.status_code != 200:
            raise RedditError(f"OAuth token request failed ({r.status_code}). Check REDDIT_CLIENT_ID/SECRET.")
        body = r.json()
        self._token = body["access_token"]
        self._token_exp = time.time() + float(body.get("expires_in", 3600))

    def _get(self, path: str, params: dict | None = None) -> Any:
        params = {"raw_json": 1, **(params or {})}
        for attempt in range(4):
            wait = self.min_interval - (time.time() - self._last_call)
            if wait > 0:
                time.sleep(wait)
            headers = {}
            if self.oauth:
                self._ensure_token()
                headers["Authorization"] = f"bearer {self._token}"
                url = "https://oauth.reddit.com" + path
            else:
                url = "https://www.reddit.com" + path + ".json"
            self._last_call = time.time()
            self.requests_made += 1
            r = self.http.get(url, params=params, headers=headers)
            if r.status_code == 429:
                reset = float(r.headers.get("x-ratelimit-reset", 10 * (attempt + 1)))
                time.sleep(min(reset, 60))
                continue
            if r.status_code in (403, 404):
                raise RedditError(f"{r.status_code} for {path} (private, banned, suspended, missing, or blocked without OAuth)")
            if r.status_code >= 500:
                time.sleep(2 * (attempt + 1))
                continue
            r.raise_for_status()
            remaining = r.headers.get("x-ratelimit-remaining")
            if remaining is not None and float(remaining) < 2:
                time.sleep(float(r.headers.get("x-ratelimit-reset", 10)))
            return r.json()
        raise RedditError(f"Gave up on {path} after retries")

    def listing(self, path: str, limit: int, params: dict | None = None) -> Iterator[dict]:
        after = None
        fetched = 0
        while fetched < limit:
            page = min(100, limit - fetched)
            body = self._get(path, {**(params or {}), "limit": page, **({"after": after} if after else {})})
            data = body.get("data", {}) if isinstance(body, dict) else {}
            children = data.get("children", [])
            for child in children:
                yield child
            fetched += len(children)
            after = data.get("after")
            if not after or not children:
                break

    # ----------------------------------------------------------------- endpoints
    def user_about(self, username: str) -> dict:
        return self._get(f"/user/{username}/about").get("data", {})

    def user_submitted(self, username: str, limit: int) -> list[dict]:
        return [c["data"] for c in self.listing(f"/user/{username}/submitted", limit, {"sort": "new"}) if c.get("kind") == "t3"]

    def user_comments(self, username: str, limit: int) -> list[dict]:
        return [c["data"] for c in self.listing(f"/user/{username}/comments", limit, {"sort": "new"}) if c.get("kind") == "t1"]

    def subreddit_about(self, name: str) -> dict:
        return self._get(f"/r/{name}/about").get("data", {})

    def subreddit_posts(self, name: str, limit: int, sort: str = "new") -> list[dict]:
        return [c["data"] for c in self.listing(f"/r/{name}/{sort}", limit) if c.get("kind") == "t3"]

    def post_with_comments(self, post_id36: str, expand_more: int = 5) -> tuple[dict, list[dict]]:
        body = self._get(f"/comments/{post_id36}", {"limit": 500, "depth": 12, "sort": "old"})
        post = body[0]["data"]["children"][0]["data"]
        comments: list[dict] = []
        more_ids: list[str] = []

        def walk(children: list[dict]) -> None:
            for ch in children:
                if ch.get("kind") == "t1":
                    d = ch["data"]
                    comments.append(d)
                    replies = d.get("replies")
                    if isinstance(replies, dict):
                        walk(replies.get("data", {}).get("children", []))
                elif ch.get("kind") == "more":
                    more_ids.extend(ch.get("data", {}).get("children", []))

        walk(body[1]["data"]["children"])
        # Load collapsed "more comments" in batches of 100 ids.
        batches = 0
        while more_ids and batches < expand_more:
            chunk, more_ids = more_ids[:100], more_ids[100:]
            batches += 1
            try:
                res = self._get(
                    "/api/morechildren",
                    {"link_id": f"t3_{post_id36}", "children": ",".join(chunk), "api_type": "json", "sort": "old"},
                )
            except (RedditError, httpx.HTTPError):
                break
            for thing in res.get("json", {}).get("data", {}).get("things", []):
                if thing.get("kind") == "t1":
                    comments.append(thing["data"])
                elif thing.get("kind") == "more":
                    more_ids.extend(thing.get("data", {}).get("children", []))
        return post, comments

    def duplicates(self, post_id36: str, limit: int = 50) -> list[dict]:
        """Crossposts and reposts of the same link (Reddit's 'other discussions')."""
        try:
            body = self._get(f"/duplicates/{post_id36}", {"limit": limit})
        except RedditError:
            return []
        if isinstance(body, list) and len(body) > 1:
            return [c["data"] for c in body[1]["data"]["children"] if c.get("kind") == "t3"]
        return []

    def search(self, query: str, subreddit: str | None = None, limit: int = 50, time_filter: str = "week") -> list[dict]:
        """Reddit search, newest first. Site-wide when subreddit is None."""
        path = f"/r/{subreddit}/search" if subreddit else "/search"
        params = {"q": query[:500], "sort": "new", "t": time_filter, "type": "link"}
        if subreddit:
            params["restrict_sr"] = 1
        return [c["data"] for c in self.listing(path, limit, params) if c.get("kind") == "t3"]

    def domain_posts(self, domain: str, limit: int = 50) -> list[dict]:
        """Posts linking to a web domain (Reddit's /domain/<host> listing)."""
        return [c["data"] for c in self.listing(f"/domain/{domain}/new", limit) if c.get("kind") == "t3"]

    def info(self, fullnames: list[str]) -> list[dict]:
        """Current score / comment count / upvote ratio for up to 100 posts per request."""
        out = []
        for i in range(0, len(fullnames), 100):
            body = self._get("/api/info", {"id": ",".join(fullnames[i:i + 100])})
            out += [c["data"] for c in body.get("data", {}).get("children", []) if c.get("kind") == "t3"]
        return out


# --------------------------------------------------------------------- parsing
def _full(native: str | None) -> str | None:
    return f"{PLATFORM}:{native}" if native else None


def parse_post(d: dict) -> dict:
    return {
        "id": _full(d.get("name") or f"t3_{d.get('id')}"),
        "platform": PLATFORM,
        "account_id": account_id(PLATFORM, d.get("author")),
        "community_id": community_id(PLATFORM, d.get("subreddit")),
        "title": d.get("title"),
        "body": d.get("selftext") or "",
        "url": d.get("url"),
        "created_utc": d.get("created_utc"),
        "score": d.get("score"),
        "num_comments": d.get("num_comments"),
        "upvote_ratio": d.get("upvote_ratio"),
        "crosspost_parent": _full(d.get("crosspost_parent")),
        "permalink": ("https://www.reddit.com" + d["permalink"]) if d.get("permalink") else None,
        "_author": d.get("author"),
        "_subreddit": d.get("subreddit"),
    }


def parse_comment(d: dict) -> dict:
    return {
        "id": _full(d.get("name") or f"t1_{d.get('id')}"),
        "platform": PLATFORM,
        "account_id": account_id(PLATFORM, d.get("author")),
        "post_id": _full(d.get("link_id")),
        "parent_id": _full(d.get("parent_id")),
        "community_id": community_id(PLATFORM, d.get("subreddit")),
        "body": d.get("body") or "",
        "created_utc": d.get("created_utc"),
        "score": d.get("score"),
        "_author": d.get("author"),
        "_subreddit": d.get("subreddit"),
        "_link_title": d.get("link_title"),
        "_link_author": d.get("link_author"),
    }


def post_id_from_ref(ref: str) -> str:
    """Accept a full URL, a redd.it short link, 't3_abc' or 'abc'."""
    ref = ref.strip()
    m = re.search(r"/comments/([a-z0-9]+)", ref, re.I) or re.search(r"redd\.it/([a-z0-9]+)", ref, re.I)
    if m:
        return m.group(1).lower()
    ref = ref.split(":")[-1]
    if ref.lower().startswith("t3_"):
        ref = ref[3:]
    if not re.fullmatch(r"[a-z0-9]{3,10}", ref, re.I):
        raise RedditError(f"Not a Reddit post id or URL: {ref!r}")
    return ref.lower()


# --------------------------------------------------------------------- storing
def _store(db: Database, posts: list[dict], comments: list[dict]) -> None:
    accounts = set()
    comms = set()
    stubs = []
    for p in posts:
        if p.get("account_id"):
            accounts.add((p["account_id"], PLATFORM, p["_author"]))
        if p.get("community_id"):
            comms.add((p["community_id"], p["_subreddit"]))
    for c in comments:
        if c.get("account_id"):
            accounts.add((c["account_id"], PLATFORM, c["_author"]))
        if c.get("community_id"):
            comms.add((c["community_id"], c["_subreddit"]))
        # Comment listings name the parent post and its author; keep that as a stub post so
        # commenter -> post-author interactions exist even if the post itself was never fetched.
        if c.get("_link_title") is not None and c.get("post_id"):
            link_author = account_id(PLATFORM, c.get("_link_author"))
            if link_author:
                accounts.add((link_author, PLATFORM, c["_link_author"]))
            stubs.append({
                "id": c["post_id"], "platform": PLATFORM, "account_id": link_author,
                "community_id": c.get("community_id"), "title": c["_link_title"], "body": None,
            })
    db.ensure_accounts(accounts)
    db.upsert_communities({"id": cid, "platform": PLATFORM, "name": name} for cid, name in comms)
    db.upsert_posts(stubs, stub=True)
    db.upsert_posts(posts)
    db.upsert_comments(comments)


def _store_mentions(db: Database, items: list[dict]) -> None:
    mentioned = set()
    for it in items:
        for name in extract_user_mentions((it.get("title") or "") + " " + (it.get("body") or "")):
            mentioned.add((f"{PLATFORM}:{name}", PLATFORM, name))
    db.ensure_accounts(mentioned)


def ingest_user(db: Database, client: RedditClient, username: str, limit: int = 300, log: Callable[[str], None] = print) -> dict:
    username = username.strip().lstrip("@")
    for prefix in ("/u/", "u/", "/user/", "user/"):
        if username.lower().startswith(prefix):
            username = username[len(prefix):]
    aid = account_id(PLATFORM, username)
    about: dict = {}
    try:
        about = client.user_about(username)
    except RedditError as e:
        log(f"about u/{username}: {e}")
    status = "suspended" if about.get("is_suspended") else ("active" if about else "unknown")
    bio = (about.get("subreddit") or {}).get("public_description") if isinstance(about.get("subreddit"), dict) else None
    db.upsert_accounts([{
        "id": aid, "platform": PLATFORM, "username": about.get("name", username),
        "created_utc": about.get("created_utc"), "link_karma": about.get("link_karma"),
        "comment_karma": about.get("comment_karma"), "bio": bio, "status": status, "source": "reddit-api",
        "verified_email": about.get("has_verified_email"),
    }])
    posts = [parse_post(d) for d in _safe(lambda: client.user_submitted(username, limit), log)]
    comments = [parse_comment(d) for d in _safe(lambda: client.user_comments(username, limit), log)]
    _store(db, posts, comments)
    _store_mentions(db, posts + comments)
    evidence = [(p["id"], "post", (p.get("title") or "") + " " + (p.get("body") or "") + " " + (p.get("url") or "")) for p in posts]
    evidence += [(c["id"], "comment", c.get("body")) for c in comments]
    db.add_external_links(links_for_account(aid, bio, evidence))
    log(f"u/{username}: {len(posts)} posts, {len(comments)} comments")
    return {"account": aid, "posts": len(posts), "comments": len(comments), "status": status}


def ingest_post(
    db: Database, client: RedditClient, ref: str, expand_commenters: int = 0, history_limit: int = 100,
    log: Callable[[str], None] = print,
) -> dict:
    pid36 = post_id_from_ref(ref)
    post_raw, comments_raw = client.post_with_comments(pid36)
    post = parse_post(post_raw)
    comments = [parse_comment(d) for d in comments_raw]
    for c in comments:  # morechildren results may omit subreddit
        c["community_id"] = c.get("community_id") or post["community_id"]
        c["_subreddit"] = c.get("_subreddit") or post["_subreddit"]
    dups = [parse_post(d) for d in client.duplicates(pid36)]
    for d in dups:  # treat every duplicate as a crosspost of this post if Reddit didn't say otherwise
        d["crosspost_parent"] = d.get("crosspost_parent") or post["id"]
    _store(db, [post] + dups, comments)
    db.add_snapshots(snapshot_rows([post] + dups))
    _store_mentions(db, [post] + comments)
    if post.get("community_id"):
        try:
            about = client.subreddit_about(post["_subreddit"])
            db.upsert_communities([{
                "id": post["community_id"], "platform": PLATFORM, "name": post["_subreddit"],
                "subscribers": about.get("subscribers"), "description": about.get("public_description"),
            }])
        except RedditError:
            pass
    histories = []
    if post.get("_author"):
        histories.append(post["_author"])
    if expand_commenters:
        # Earliest commenters first: they are the ones that shape traction.
        seen = set(h.lower() for h in histories)
        for c in sorted(comments, key=lambda c: c.get("created_utc") or 0):
            a = c.get("_author")
            if a and a.lower() not in seen and c.get("account_id"):
                seen.add(a.lower())
                histories.append(a)
            if len(histories) >= expand_commenters + 1:
                break
    for name in histories:
        try:
            ingest_user(db, client, name, history_limit, log)
        except RedditError as e:
            log(f"skip u/{name}: {e}")
    log(f"post {pid36}: {len(comments)} comments, {len(dups)} crossposts, {len(histories)} histories")
    return {"post": post["id"], "comments": len(comments), "crossposts": len(dups), "histories": len(histories)}


def ingest_subreddit(
    db: Database, client: RedditClient, name: str, limit: int = 100, expand_authors: int = 0,
    history_limit: int = 100, sort: str = "new", log: Callable[[str], None] = print,
) -> dict:
    name = name.strip()
    for prefix in ("/r/", "r/"):
        if name.lower().startswith(prefix):
            name = name[len(prefix):]
    try:
        about = client.subreddit_about(name)
        db.upsert_communities([{
            "id": community_id(PLATFORM, name), "platform": PLATFORM, "name": about.get("display_name", name),
            "subscribers": about.get("subscribers"), "description": about.get("public_description"),
        }])
    except RedditError as e:
        log(f"about r/{name}: {e}")
    posts = [parse_post(d) for d in client.subreddit_posts(name, limit, sort)]
    _store(db, posts, [])
    _store_mentions(db, posts)
    # Most frequent posters first: these are the "regular posters" whose other subs we want.
    freq: dict[str, int] = {}
    for p in posts:
        if p.get("_author") and p.get("account_id"):
            freq[p["_author"]] = freq.get(p["_author"], 0) + 1
    authors = sorted(freq, key=lambda a: -freq[a])[:expand_authors]
    for a in authors:
        try:
            ingest_user(db, client, a, history_limit, log)
        except RedditError as e:
            log(f"skip u/{a}: {e}")
    log(f"r/{name}: {len(posts)} posts, {len(authors)} author histories")
    return {"community": community_id(PLATFORM, name), "posts": len(posts), "histories": len(authors)}


def snapshot_rows(posts: list[dict], ts: float | None = None) -> list[dict]:
    ts = ts or time.time()
    return [{"post_id": p["id"], "ts": ts, "score": p.get("score"), "num_comments": p.get("num_comments"),
             "upvote_ratio": p.get("upvote_ratio")} for p in posts if p.get("id")]


def poll_scores(db: Database, client: RedditClient, post_ids: list[str], log: Callable[[str], None] = print) -> int:
    """Record the current score of watched posts (vote-velocity tracking)."""
    natives = [p.split(":", 1)[1] for p in post_ids if p.startswith(f"{PLATFORM}:t3_")]
    if not natives:
        return 0
    posts = [parse_post(d) for d in client.info(natives)]
    now = time.time()
    db.add_snapshots(snapshot_rows(posts, now))
    db.update_post_counts(posts)
    db.mark_polled(post_ids, now)
    return len(posts)


def run_watch_topic(db: Database, client: RedditClient, topic: dict, log: Callable[[str], None] = print,
                    max_full_fetch: int = 12, expand_commenters: int = 5, history_limit: int = 100) -> dict:
    """Find new posts for a watch topic, collect the newest ones in full and start tracking their score."""
    from ..analysis.watch import compile_topic, search_query  # local import: analysis depends on collectors

    m = compile_topic(topic)
    query = search_query(topic)
    raw: dict[str, dict] = {}
    targets = [None] if not m.communities else [cid.split("c/", 1)[1] for cid in m.communities]
    for sub in targets:
        try:
            found = client.search(query, sub, 50) if query else (client.subreddit_posts(sub, 50) if sub else [])
        except RedditError as e:
            log(f"topic '{topic['name']}' search r/{sub or 'all'}: {e}")
            continue
        for d in found:
            raw[d.get("name") or d.get("id")] = d
    for domain in m.domains:
        try:
            for d in client.domain_posts(domain, 50):
                raw[d.get("name") or d.get("id")] = d
        except RedditError as e:
            log(f"topic '{topic['name']}' domain {domain}: {e}")
    posts = [parse_post(d) for d in raw.values()]
    posts = [p for p in posts if m.match_post(p["title"], p["body"], p["url"], p["community_id"])]
    known = {r["id"] for r in db.query("SELECT id FROM posts WHERE is_stub=0")}
    new = sorted((p for p in posts if p["id"] not in known), key=lambda p: -(p["created_utc"] or 0))
    _store(db, posts, [])
    db.add_snapshots(snapshot_rows(posts))
    fetched = 0
    for p in new[:max_full_fetch]:
        try:
            ingest_post(db, client, p["id"], expand_commenters, history_limit, log)
            fetched += 1
        except RedditError as e:
            log(f"topic post {p['id']}: {e}")
    recent = [p for p in posts if p["created_utc"] and time.time() - p["created_utc"] < 48 * 3600]
    for p in recent:
        db.watch_post(p["id"], hours=max(1.0, 48 - (time.time() - p["created_utc"]) / 3600), interval_min=5, topic_id=topic["id"])
    status = f"{len(posts)} matching, {len(new)} new, {fetched} fetched in full, {len(recent)} tracked"
    db.topic_ran(topic["id"], status)
    log(f"topic '{topic['name']}': {status}")
    return {"matching": len(posts), "new": len(new), "fetched": fetched, "tracked": len(recent)}


def _safe(fn: Callable[[], list], log: Callable[[str], None]) -> list:
    try:
        return fn()
    except RedditError as e:
        log(str(e))
        return []
