"""FastAPI app: REST API + single-page UI + background monitoring.

Run:  python run.py   (or: uvicorn app.main:app --port 8000)
Set SLA_SCHEDULER=0 to disable the background watchlist / score-tracking jobs.
"""
from __future__ import annotations

import os
import threading
import time
from contextlib import asynccontextmanager

from fastapi import Body, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles

from . import config
from .analysis import activity, bots as botmod, coordination, graph, linking, ontology, traction, watch
from .analysis.corpus import CorpusCache
from .collectors import demo, importer
from .collectors.reddit import (RedditClient, RedditError, ingest_post, ingest_subreddit, ingest_user, poll_scores,
                                run_watch_topic)
from .db import Database

STATIC = config.BASE_DIR / "static"

db = Database(config.DB_PATH)
cache = CorpusCache(db)
_collect_lock = threading.Lock()
_log: list[str] = []
_scheduler = {"enabled": os.environ.get("SLA_SCHEDULER", "1") != "0", "last_tick": None, "running": False}


def log(msg: str) -> None:
    _log.append(time.strftime("%H:%M:%S ") + msg)
    del _log[:-200]


# --------------------------------------------------------------------- background monitoring
def run_due_jobs(force_topic: int | None = None) -> dict:
    """Run watch topics whose interval elapsed and poll scores of tracked posts."""
    now = time.time()
    topics = [t for t in db.topics() if (force_topic is not None and t["id"] == force_topic) or
              (force_topic is None and t["active"] and (not t["last_run_utc"] or now - t["last_run_utc"] >= t["interval_min"] * 60))]
    posts = [] if force_topic is not None else [p["post_id"] for p in db.due_watched_posts(now)]
    if not topics and not posts:
        return {"topics": 0, "polled": 0}
    if not _collect_lock.acquire(blocking=False):
        return {"busy": True}
    out = {"topics": 0, "polled": 0}
    try:
        client = RedditClient()
        for t in topics:
            try:
                run_watch_topic(db, client, t, log)
                out["topics"] += 1
            except (RedditError, Exception) as e:  # keep the loop alive whatever happens
                db.topic_ran(t["id"], f"error: {e}")
                log(f"topic '{t['name']}' failed: {e}")
        if posts:
            try:
                out["polled"] = poll_scores(db, client, posts, log)
            except (RedditError, Exception) as e:
                db.mark_polled(posts, now)  # back off until the next interval
                log(f"score polling failed: {e}")
    finally:
        _collect_lock.release()
    return out


def _scheduler_loop() -> None:
    while _scheduler["enabled"]:
        time.sleep(30)
        _scheduler["last_tick"] = time.time()
        try:
            run_due_jobs()
        except Exception as e:  # pragma: no cover - defensive
            log(f"scheduler error: {e}")


@asynccontextmanager
async def lifespan(_: FastAPI):
    if _scheduler["enabled"]:
        threading.Thread(target=_scheduler_loop, daemon=True, name="sla-scheduler").start()
        _scheduler["running"] = True
    yield
    _scheduler["enabled"] = False


app = FastAPI(title="Social Link Analyzer", version="0.2.0", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=STATIC), name="static")


# --------------------------------------------------------------------- derived caches
def model():
    c = cache.get()
    if "model" not in cache.derived:
        cache.derived["model"] = linking.build_model(c)
    return c, cache.derived["model"]


def link_scan(min_score: float = 0.45):
    c, m = model()
    key = f"scan:{min_score}"
    if key not in cache.derived:
        cache.derived[key] = linking.scan(m, min_score=min_score)
    return c, m, cache.derived[key]


def link_index() -> dict:
    _, _, s = link_scan()
    return {tuple(sorted((p["a"], p["b"]))): p for p in s["pairs"]}


def cluster_index() -> dict:
    _, _, s = link_scan()
    return {m: cl["id"] for cl in s["clusters"] for m in cl["members"]}


def bot_scores() -> dict:
    c = cache.get()
    if "bots" not in cache.derived:
        cache.derived["bots"] = botmod.score_all(c)
    return cache.derived["bots"]


def coord(window: int = 600, min_repeats: int = 2) -> dict:
    c = cache.get()
    key = f"coord:{window}:{min_repeats}"
    if key not in cache.derived:
        cache.derived[key] = coordination.detect(c, window, min_repeats, bot_scores())
    return cache.derived[key]


def analyze(post_id: str) -> dict:
    """Traction analysis with every signal, cached per corpus version (snapshots are read fresh)."""
    c = cache.get()
    tc = cache.derived.setdefault("traction_cache", {})
    return traction.analyze_post(c, post_id, link_index(), bot_scores(), coord(), db.snapshots(post_id), tc)


# --------------------------------------------------------------------- pages
@app.get("/")
def index():
    return FileResponse(STATIC / "index.html")


@app.get("/api/status")
def status():
    return {"counts": db.counts(), "reddit": {"oauth": bool(config.REDDIT_CLIENT_ID and config.REDDIT_CLIENT_SECRET),
                                             "userAgent": config.REDDIT_USER_AGENT},
            "collecting": _collect_lock.locked(), "log": _log[-40:], "db": str(config.DB_PATH),
            "scheduler": {"enabled": _scheduler["enabled"], "running": _scheduler["running"], "lastTick": _scheduler["last_tick"]}}


@app.get("/api/ontology")
def ontology_schema():
    return ontology.schema_json()


# --------------------------------------------------------------------- data
DEMO_TOPICS = [
    {"name": "Northbridge Harbor Bill", "keywords": 'Northbridge, "Harbor Bill"', "domains": "dailytruth-news.example, northbridge-herald.example",
     "communities": "", "interval_min": 30, "active": False},
    {"name": "ZetaPhone promotion", "keywords": "ZetaPhone", "domains": "zetaphone-store.example", "communities": "", "interval_min": 60,
     "active": False},
]


@app.post("/api/demo")
def load_demo(reset: bool = True):
    if reset:
        db.reset()
    res = importer.import_generic(db, demo.generate())
    for t in DEMO_TOPICS:  # paused: the demo topics only exist in the synthetic data
        db.save_topic(t)
    log(f"demo dataset loaded: {res}")
    return res


@app.post("/api/reset")
def reset():
    db.reset()
    log("database cleared")
    return {"ok": True}


@app.post("/api/import")
async def import_file(request: Request):
    text = (await request.body()).decode("utf-8", errors="replace")
    try:
        res = importer.import_text(db, text)
    except Exception as e:  # malformed user file
        raise HTTPException(400, f"Could not import: {e}")
    log(f"import: {res}")
    return res


def _collect(fn, *args, **kwargs):
    if not _collect_lock.acquire(blocking=False):
        raise HTTPException(409, "A collection job is already running.")
    try:
        client = RedditClient()
        res = fn(db, client, *args, log=log, **kwargs)
        res["requests"] = client.requests_made
        return res
    except RedditError as e:
        log(f"error: {e}")
        raise HTTPException(502, str(e))
    finally:
        _collect_lock.release()


@app.post("/api/collect/user")
def collect_user(body: dict = Body(...)):
    return _collect(ingest_user, body["username"], int(body.get("limit", 300)))


@app.post("/api/collect/post")
def collect_post(body: dict = Body(...)):
    return _collect(ingest_post, body["ref"], int(body.get("expandCommenters", 10)), int(body.get("historyLimit", 100)))


@app.post("/api/collect/subreddit")
def collect_subreddit(body: dict = Body(...)):
    return _collect(ingest_subreddit, body["name"], int(body.get("limit", 100)), int(body.get("expandAuthors", 10)),
                    int(body.get("historyLimit", 100)), body.get("sort", "new"))


@app.post("/api/links")
def add_link(body: dict = Body(...)):
    a, b = body["a"].strip().lower(), body["b"].strip().lower()
    if ":" not in a or ":" not in b:
        raise HTTPException(400, "Use platform:username ids, e.g. reddit:someone and twitter:someone")
    db.add_manual_link(a, b, body.get("relation", "sameAs"), body.get("note", ""))
    db.ensure_accounts([(x, x.split(":", 1)[0], x.split(":", 1)[1]) for x in (a, b)])
    return {"ok": True}


@app.delete("/api/links")
def delete_link(a: str, b: str, relation: str = "sameAs"):
    db.delete_manual_link(a, b, relation)
    return {"ok": True}


@app.get("/api/links")
def list_links():
    return db.query("SELECT * FROM manual_links ORDER BY created_utc DESC")


# --------------------------------------------------------------------- lookup
@app.get("/api/accounts")
def accounts(q: str = "", limit: int = 30):
    c = cache.get()
    ql = q.lower().lstrip("@").removeprefix("u/")
    rows = []
    for aid, a in c.accounts.items():
        if ql and ql not in aid:
            continue
        rows.append({"id": aid, "label": c.account_label(aid), "platform": a["platform"], "items": len(c.by_account.get(aid, []))})
    rows.sort(key=lambda r: (-r["items"], r["id"]))
    return rows[:limit]


@app.get("/api/communities")
def communities():
    c = cache.get()
    counts = {}
    for it in c.items.values():
        if it.community_id:
            counts[it.community_id] = counts.get(it.community_id, 0) + 1
    rows = [{"id": cid, "label": c.community_label(cid), "platform": cm["platform"], "items": counts.get(cid, 0),
             "subscribers": cm.get("subscribers")} for cid, cm in c.communities.items()]
    return sorted(rows, key=lambda r: -r["items"])


@app.get("/api/nodes")
def nodes(q: str = "", limit: int = 30):
    """Search accounts, communities and external accounts for the graph/route pickers."""
    c = cache.get()
    ql = q.lower().lstrip("@").removeprefix("u/").removeprefix("r/")
    out = []
    for aid in c.accounts:
        if not ql or ql in aid:
            out.append({"id": aid, "label": c.account_label(aid), "kind": "account", "items": len(c.by_account.get(aid, []))})
    for cid in c.communities:
        if not ql or ql in cid:
            out.append({"id": cid, "label": c.community_label(cid), "kind": "community", "items": 0})
    for l in c.external_links:
        eid = f"{l['platform']}:{l['handle']}"
        if eid not in c.accounts and (not ql or ql in eid):
            out.append({"id": eid, "label": f"{l['platform']}:@{l['handle']}", "kind": "external", "items": 0})
    seen, uniq = set(), []
    for o in sorted(out, key=lambda o: (-o["items"], o["id"])):
        if o["id"] not in seen:
            seen.add(o["id"])
            uniq.append(o)
    return uniq[:limit]


# --------------------------------------------------------------------- feature 1: graph + routes
def _edge_list(edges: str | None) -> list[str] | None:
    return [e for e in edges.split(",") if e] if edges else None


@app.get("/api/graph")
def get_graph(edges: str | None = None, min_weight: int = 2, focus: str | None = None, depth: int = 2,
              max_nodes: int = 250, platforms: str | None = None):
    c, _, s = link_scan()
    return graph.build_graph(c, s["pairs"], s["clusters"], _edge_list(edges), min_weight, focus or None, depth, max_nodes,
                             _edge_list(platforms), bots=bot_scores(), coord=coord())


@app.get("/api/route")
def get_route(source: str, target: str, k: int = 3, edges: str | None = None, max_hops: int = 6):
    c, _, s = link_scan()
    return graph.find_routes(c, source, target, k, s["pairs"], s["clusters"], _edge_list(edges), max_hops,
                             bots=bot_scores(), coord=coord())


@app.get("/api/export/ontology.ttl", response_class=PlainTextResponse)
def export_ontology():
    return PlainTextResponse(ontology.ontology_turtle(), media_type="text/turtle",
                             headers={"Content-Disposition": "attachment; filename=social-link-ontology.ttl"})


@app.get("/api/export/graph.ttl", response_class=PlainTextResponse)
def export_graph_ttl(edges: str | None = None, min_weight: int = 2, focus: str | None = None, depth: int = 2, max_nodes: int = 5000):
    g = get_graph(edges, min_weight, focus, depth, max_nodes)
    return PlainTextResponse(ontology.graph_turtle(g), media_type="text/turtle",
                             headers={"Content-Disposition": "attachment; filename=social-graph.ttl"})


@app.get("/api/export/graph.jsonld")
def export_graph_jsonld(edges: str | None = None, min_weight: int = 2, focus: str | None = None, depth: int = 2, max_nodes: int = 5000):
    g = get_graph(edges, min_weight, focus, depth, max_nodes)
    return PlainTextResponse(ontology.graph_jsonld(g), media_type="application/ld+json",
                             headers={"Content-Disposition": "attachment; filename=social-graph.jsonld"})


# --------------------------------------------------------------------- feature 2: traction
@app.get("/api/posts")
def posts(q: str = "", community: str | None = None, limit: int = 100):
    return traction.list_posts(cache.get(), q, community or None, limit)


@app.get("/api/traction")
def post_traction(post_id: str):
    res = analyze(post_id)
    if "error" in res:
        raise HTTPException(404, res["error"])
    res["tracked"] = any(w["post_id"] == post_id for w in db.watched_posts())
    return res


# --------------------------------------------------------------------- feature 3: multi-account linking
@app.get("/api/linking/scan")
def linking_scan(min_score: float = 0.45, cross_platform: bool = False):
    c, m, s = link_scan(round(min_score, 2))
    pairs = [p for p in s["pairs"] if p["crossPlatform"]] if cross_platform else s["pairs"]
    for p in pairs:
        p["labelA"], p["labelB"] = c.account_label(p["a"]), c.account_label(p["b"])
    return {**s, "pairs": pairs}


@app.get("/api/linking/account")
def linking_account(account: str, top: int = 15):
    c, m = model()
    res = linking.similar_to(m, account, top)
    for p in res["matches"]:
        p["labelB"] = c.account_label(p["b"])
    return res


@app.get("/api/linking/pair")
def linking_pair(a: str, b: str):
    c, m = model()
    res = linking.pair_detail(c, m, a, b)
    if "error" in res:
        raise HTTPException(404, res["error"])
    return res


# --------------------------------------------------------------------- feature 4: regular posters
@app.get("/api/activity/regulars")
def regulars(community: str | None = None, basis: str = "posts", min_items: int = 8, min_regularity: float = 0.45):
    res = activity.regular_posters(cache.get(), community or None, basis, min_items, min_regularity)
    b, member_of = bot_scores(), coord()["memberOf"]
    for r in res["posters"]:
        r["botScore"], r["botLabel"] = b.get(r["account"], {}).get("score"), b.get(r["account"], {}).get("label")
        r["network"] = member_of.get(r["account"])
    return res


@app.get("/api/activity/account")
def account_activity(account: str):
    c, m = model()
    res = activity.account_profile(c, account, linking.similar_to(m, account, 10).get("matches"))
    if "error" in res:
        raise HTTPException(404, res["error"])
    for s in res["similarAccounts"]:
        s["labelB"] = c.account_label(s["b"])
    res["bot"] = botmod.score_account(c, account)
    co = coord()
    net_id = co["memberOf"].get(account)
    res["network"] = next(({k: n[k] for k in ("id", "label", "size", "coSharedItems", "medianDelaySec")} for n in co["networks"] if n["id"] == net_id), None)
    return res


# --------------------------------------------------------------------- bots & coordination
@app.get("/api/bots")
def bots_list(min_score: float = 0.0, label: str | None = None, limit: int = 500):
    return botmod.summary(bot_scores(), cache.get(), min_score, label or None, limit)


@app.get("/api/coordination")
def coordination_view(window: int = 600, min_repeats: int = 2):
    window = max(30, min(window, 6 * 3600))
    res = coord(window, max(1, min_repeats))
    return {k: v for k, v in res.items() if k not in ("memberOf", "coShareEdges")}


# --------------------------------------------------------------------- watchlist + score tracking
@app.get("/api/watch/topics")
def topics_list():
    return db.topics()


@app.post("/api/watch/topics")
def topic_save(body: dict = Body(...)):
    if not (body.get("keywords") or body.get("domains") or body.get("communities")):
        raise HTTPException(400, "Give at least one keyword, domain or community.")
    tid = db.save_topic(body)
    return db.topic(tid)


@app.delete("/api/watch/topics/{topic_id}")
def topic_delete(topic_id: int):
    db.delete_topic(topic_id)
    return {"ok": True}


@app.post("/api/watch/topics/{topic_id}/run")
def topic_run(topic_id: int):
    if not db.topic(topic_id):
        raise HTTPException(404, "Unknown topic")
    res = run_due_jobs(force_topic=topic_id)
    if res.get("busy"):
        raise HTTPException(409, "A collection job is already running.")
    return {**res, "topic": db.topic(topic_id)}


@app.get("/api/watch/topics/{topic_id}/report")
def topic_report(topic_id: int):
    t = db.topic(topic_id)
    if not t:
        raise HTTPException(404, "Unknown topic")
    return watch.topic_report(cache.get(), t, analyze, bot_scores(), coord(), cluster_index())


@app.get("/api/watch/posts")
def watched():
    c = cache.get()
    rows = db.watched_posts()
    for r in rows:
        p = c.posts.get(r["post_id"]) or {}
        r["title"], r["community"] = p.get("title"), c.community_label(p.get("community_id"))
        r["snapshots"] = len(db.snapshots(r["post_id"]))
    return rows


@app.post("/api/watch/posts")
def watch_post(body: dict = Body(...)):
    pid = body["post_id"]
    if not pid.startswith("reddit:t3_"):
        raise HTTPException(400, "Score tracking works for Reddit posts (reddit:t3_...).")
    db.watch_post(pid, float(body.get("hours", 48)), int(body.get("interval_min", 5)))
    return {"ok": True}


@app.delete("/api/watch/posts")
def unwatch_post(post_id: str):
    db.unwatch_post(post_id)
    return {"ok": True}


@app.exception_handler(KeyError)
def key_error(_: Request, exc: KeyError):
    return JSONResponse(status_code=400, content={"detail": f"Missing field: {exc}"})
