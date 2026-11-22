"""Import data from files: a generic multi-platform JSON format, or Reddit archive dumps (NDJSON).

Generic format (any platform: twitter, instagram, youtube, mastodon, ...):
{
  "accounts": [{"platform": "twitter", "username": "foo", "created_utc": 1700000000, "bio": "..."}],
  "communities": [{"platform": "twitter", "name": "#gadgets"}],
  "posts":    [{"platform": "twitter", "id": "123", "author": "foo", "community": "#gadgets",
                "title": "", "body": "text", "url": "...", "created_utc": 1700000100, "score": 4,
                "crosspost_parent": null}],
  "comments": [{"platform": "twitter", "id": "456", "author": "bar", "post_id": "123",
                "parent_id": "123", "body": "reply", "created_utc": 1700000200}],
  "links":    [{"a": "reddit:foo", "b": "twitter:foo", "relation": "sameAs", "note": "confirmed"}]
}
Reddit dumps (Pushshift / Arctic Shift style, one JSON object per line) are detected by their
fields (subreddit + title/selftext for submissions, subreddit + body + link_id for comments).
"""
from __future__ import annotations

import json
from typing import Any

from ..db import Database, account_id, community_id
from .crossplatform import links_for_account


def _pid(platform: str, raw: Any, prefix: str = "") -> str | None:
    if raw is None or raw == "":
        return None
    raw = str(raw)
    if ":" in raw and raw.split(":", 1)[0] == platform:
        return raw
    if prefix and not raw.startswith(("t1_", "t3_")):
        raw = prefix + raw
    return f"{platform}:{raw}"


def import_text(db: Database, text: str) -> dict:
    text = text.strip()
    if not text:
        return {"error": "empty file"}
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        payload = [json.loads(line) for line in text.splitlines() if line.strip()]
    if isinstance(payload, dict) and any(k in payload for k in ("accounts", "posts", "comments", "links")):
        return import_generic(db, payload)
    if isinstance(payload, dict):
        payload = [payload]
    return import_reddit_records(db, payload)


def import_generic(db: Database, data: dict) -> dict:
    accounts, comms, posts, comments, ext = {}, {}, [], [], []
    texts: dict[str, list] = {}

    def ensure_account(platform: str, username: str | None) -> str | None:
        aid = account_id(platform, username)
        if aid and aid not in accounts:
            accounts[aid] = {"id": aid, "platform": platform, "username": username.lstrip("@"), "source": "import"}
        return aid

    for a in data.get("accounts", []):
        platform = a.get("platform", "unknown").lower()
        aid = ensure_account(platform, a.get("username"))
        if aid:
            accounts[aid].update({k: a.get(k) for k in ("created_utc", "link_karma", "comment_karma", "bio", "status", "verified_email")
                                  if a.get(k) is not None})
    for c in data.get("communities", []):
        platform = c.get("platform", "unknown").lower()
        cid = community_id(platform, c.get("name"))
        if cid:
            comms[cid] = {"id": cid, "platform": platform, "name": c["name"], "subscribers": c.get("subscribers"), "description": c.get("description")}
    for p in data.get("posts", []):
        platform = p.get("platform", "unknown").lower()
        aid = ensure_account(platform, p.get("author"))
        cid = community_id(platform, p.get("community"))
        if cid and cid not in comms:
            comms[cid] = {"id": cid, "platform": platform, "name": p["community"]}
        pid = _pid(platform, p.get("id"))
        posts.append({
            "id": pid, "platform": platform, "account_id": aid, "community_id": cid, "title": p.get("title"),
            "body": p.get("body"), "url": p.get("url"), "created_utc": p.get("created_utc"), "score": p.get("score"),
            "num_comments": p.get("num_comments"), "upvote_ratio": p.get("upvote_ratio"),
            "crosspost_parent": _pid(platform, p.get("crosspost_parent")), "permalink": p.get("permalink"),
        })
        if aid:
            texts.setdefault(aid, []).append((pid, "post", f"{p.get('title') or ''} {p.get('body') or ''} {p.get('url') or ''}"))
    post_comm = {p["id"]: p["community_id"] for p in posts}
    for c in data.get("comments", []):
        platform = c.get("platform", "unknown").lower()
        aid = ensure_account(platform, c.get("author"))
        post_id = _pid(platform, c.get("post_id"))
        cid = community_id(platform, c.get("community")) or post_comm.get(post_id)
        comments.append({
            "id": _pid(platform, c.get("id")), "platform": platform, "account_id": aid, "post_id": post_id,
            "parent_id": _pid(platform, c.get("parent_id")) or post_id, "community_id": cid,
            "body": c.get("body"), "created_utc": c.get("created_utc"), "score": c.get("score"),
        })
        if aid:
            texts.setdefault(aid, []).append((comments[-1]["id"], "comment", c.get("body")))

    db.upsert_accounts(accounts.values())
    db.upsert_communities(comms.values())
    db.upsert_posts(posts)
    db.upsert_comments(comments)
    for aid, acc in accounts.items():
        ext.extend(links_for_account(aid, acc.get("bio"), texts.get(aid, [])))
    db.add_external_links(ext)
    for l in data.get("links", []):
        if l.get("a") and l.get("b"):
            db.add_manual_link(l["a"].lower(), l["b"].lower(), l.get("relation", "sameAs"), l.get("note", ""))
    # score/comment counts over time: [{"platform", "post_id", "ts", "score", "num_comments", "upvote_ratio"}]
    snaps = [{**s, "post_id": _pid(s.get("platform", "unknown").lower(), s.get("post_id"))} for s in data.get("snapshots", [])]
    db.add_snapshots(snaps)
    return {"accounts": len(accounts), "communities": len(comms), "posts": len(posts), "comments": len(comments),
            "links": len(data.get("links", [])), "snapshots": len(snaps)}


def import_reddit_records(db: Database, records: list[dict]) -> dict:
    """Pushshift / Arctic Shift style records."""
    P = "reddit"
    accounts, comms, posts, comments = {}, {}, [], []
    texts: dict[str, list] = {}
    for r in records:
        if not isinstance(r, dict) or "subreddit" not in r:
            continue
        aid = account_id(P, r.get("author"))
        if aid:
            accounts.setdefault(aid, {"id": aid, "platform": P, "username": r["author"], "source": "archive"})
            if r.get("author_created_utc"):
                accounts[aid]["created_utc"] = r["author_created_utc"]
        cid = community_id(P, r.get("subreddit"))
        if cid:
            comms.setdefault(cid, {"id": cid, "platform": P, "name": r["subreddit"]})
        if "body" in r and "link_id" in r:
            cm = {
                "id": _pid(P, r.get("name") or r.get("id"), "t1_"), "platform": P, "account_id": aid,
                "post_id": _pid(P, r.get("link_id"), "t3_"), "parent_id": _pid(P, r.get("parent_id")),
                "community_id": cid, "body": r.get("body"), "created_utc": float(r.get("created_utc") or 0) or None,
                "score": r.get("score"),
            }
            comments.append(cm)
            if aid:
                texts.setdefault(aid, []).append((cm["id"], "comment", r.get("body")))
        elif "title" in r:
            pst = {
                "id": _pid(P, r.get("name") or r.get("id"), "t3_"), "platform": P, "account_id": aid, "community_id": cid,
                "title": r.get("title"), "body": r.get("selftext"), "url": r.get("url"),
                "created_utc": float(r.get("created_utc") or 0) or None, "score": r.get("score"),
                "num_comments": r.get("num_comments"), "upvote_ratio": r.get("upvote_ratio"),
                "crosspost_parent": _pid(P, r.get("crosspost_parent")),
                "permalink": ("https://www.reddit.com" + r["permalink"]) if r.get("permalink") else None,
            }
            posts.append(pst)
            if aid:
                texts.setdefault(aid, []).append((pst["id"], "post", f"{r.get('title') or ''} {r.get('selftext') or ''} {r.get('url') or ''}"))
    db.upsert_accounts(accounts.values())
    db.upsert_communities(comms.values())
    db.upsert_posts(posts)
    db.upsert_comments(comments)
    ext = []
    for aid, items in texts.items():
        ext.extend(links_for_account(aid, None, items))
    db.add_external_links(ext)
    return {"accounts": len(accounts), "communities": len(comms), "posts": len(posts), "comments": len(comments)}
