"""End-to-end checks on the synthetic demo dataset: every planted pattern must be found."""
from __future__ import annotations

import os
import tempfile

import pytest

os.environ["SLA_DB_PATH"] = os.path.join(tempfile.mkdtemp(), "test.db")
os.environ["SLA_SCHEDULER"] = "0"

from fastapi.testclient import TestClient  # noqa: E402

from app.collectors.crossplatform import extract_profiles  # noqa: E402
from app.collectors.reddit import post_id_from_ref  # noqa: E402
from app.main import app  # noqa: E402

RING = {"reddit:gadget_guru_88", "reddit:techsavvymike_", "reddit:honest_reviewer_22", "twitter:gadgetguru88"}


@pytest.fixture(scope="module")
def client():
    c = TestClient(app)
    r = c.post("/api/demo")
    assert r.status_code == 200 and r.json()["accounts"] > 50
    return c


def test_profile_extraction():
    found = extract_profiles("Reviews at twitter.com/gadgetguru88 | yt: youtube.com/@honestreviews22, my insta: @foo.bar")
    assert ("twitter", "gadgetguru88") in {(p, h) for p, h, _ in found}
    assert ("youtube", "honestreviews22") in {(p, h) for p, h, _ in found}
    assert ("instagram", "foo.bar") in {(p, h) for p, h, _ in found}
    assert not extract_profiles("see twitter.com/home and twitter.com/i/status/1")


def test_post_ref_parsing():
    assert post_id_from_ref("https://www.reddit.com/r/x/comments/abc123/title/") == "abc123"
    assert post_id_from_ref("t3_xyz789") == "xyz789"
    assert post_id_from_ref("https://redd.it/q1w2e3") == "q1w2e3"


def test_graph_and_routes(client):
    g = client.get("/api/graph").json()
    types = g["stats"]["edgeTypes"]
    for rel in ("activeIn", "interactsWith", "amplifies", "discloses", "possibleSameOperator", "controlledBy"):
        assert types.get(rel), rel
    r = client.get("/api/route", params={"source": "reddit:gadget_guru_88", "target": "youtube:honestreviews22"}).json()
    assert r["routes"], r
    assert r["routes"][0]["nodes"][0] == "reddit:gadget_guru_88"
    assert r["routes"][0]["nodes"][-1] == "youtube:honestreviews22"


def test_rdf_exports(client):
    ttl = client.get("/api/export/ontology.ttl").text
    assert "slo:possibleSameOperator a owl:ObjectProperty" in ttl
    g = client.get("/api/export/graph.ttl").text
    assert "slo:discloses" in g and "a slo:RedditAccount" in g
    assert client.get("/api/export/graph.jsonld").json()["@graph"]


def test_sock_puppet_ring_is_one_cluster(client):
    s = client.get("/api/linking/scan").json()
    clusters = [set(c["members"]) for c in s["clusters"]]
    assert RING in clusters, clusters
    strong = {tuple(sorted((p["a"], p["b"]))) for p in s["pairs"] if p["level"] == "strong"}
    assert ("reddit:gadget_guru_88", "twitter:gadgetguru88") in strong
    # nobody outside the ring is a strong match for the ring's main account
    top = client.get("/api/linking/account", params={"account": "reddit:gadget_guru_88"}).json()["matches"]
    assert {m["b"] for m in top if m["level"] == "strong"} <= RING


def test_boosted_post_vs_organic(client):
    posts = client.get("/api/posts", params={"q": "ZetaPhone X is"}).json()
    boosted = client.get("/api/traction", params={"post_id": posts[0]["id"]}).json()
    assert boosted["amplificationLevel"] == "high"
    assert {x["community"] for x in boosted["crossposts"]} == {"r/gadgets", "r/android"}
    assert "zetaphone-store.example" in boosted["targets"]["linkedDomains"]
    assert "u/honest_reviewer_22" in boosted["targets"]["mentionedUsers"]
    organic_id = client.get("/api/posts", params={"q": "first build"}).json()[0]["id"]
    organic = client.get("/api/traction", params={"post_id": organic_id}).json()
    assert organic["amplificationIndex"] < boosted["amplificationIndex"] - 0.3


def test_regular_posters(client):
    r = client.get("/api/activity/regulars").json()
    by = {p["account"]: p for p in r["posters"]}
    assert by["reddit:daily_market_wrap"]["pattern"].startswith("scheduled")
    assert {"r/news", "r/personalfinance"} <= {o["label"] for o in by["reddit:daily_market_wrap"]["otherCommunities"]}
    assert "reddit:chef_marta" in by and "reddit:gamer_kenji" in by
    prof = client.get("/api/activity/account", params={"account": "reddit:chef_marta"}).json()
    assert any(x["platform"] == "instagram" for x in prof["external"])


CAMPAIGN = {"reddit:brave_lynx_4471", "reddit:quiet-river-2290", "reddit:distinct_ad_8812", "reddit:hot-pie-3301",
            "reddit:sea_wolf_7719", "reddit:few_bluebird_5520"}


def test_bot_scores(client):
    rows = {r["account"]: r for r in client.get("/api/bots").json()["accounts"]}
    for a in CAMPAIGN | {"reddit:newsmirrorauto", "reddit:daily_market_wrap"}:
        assert rows[a]["label"] == "likely automated", (a, rows[a])
    # human-run accounts, including the human-operated sock puppets, are not called bots
    for a in ("reddit:quiet_otter", "reddit:chef_marta", "reddit:gamer_kenji", "reddit:gadget_guru_88", "reddit:paper_comet"):
        assert rows[a]["label"] in ("likely human", "insufficient data"), (a, rows[a])
    prof = client.get("/api/activity/account", params={"account": "reddit:newsmirrorauto"}).json()
    assert prof["bot"]["reasons"] and any(s["evidence"] for s in prof["bot"]["signals"])


def test_coordinated_network(client):
    r = client.get("/api/coordination").json()
    nets = [set(n["members"]) for n in r["networks"]]
    assert CAMPAIGN in nets, nets
    assert not any(n & {"reddit:quiet_otter", "reddit:paper_comet", "reddit:cobalt_heron"} for n in nets)
    assert any(b["display"].startswith("dailytruth-news.example/") for b in r["bursts"])
    g = client.get("/api/graph", params={"focus": "network:1", "depth": 1}).json()
    assert any(e["data"]["type"] == "coSharesWith" for e in g["edges"])


def _post_id(client, q):
    return client.get("/api/posts", params={"q": q}).json()[0]["id"]


def test_vote_bursts_and_campaign_traction(client):
    camp = client.get("/api/traction", params={"post_id": _post_id(client, "council hid report")}).json()
    assert camp["voteBursts"] and camp["amplificationLevel"] == "high"
    assert {"Vote bursts without matching discussion", "Same link pushed by other accounts within minutes"} <= {s["name"] for s in camp["signals"]}
    # an organic post that criticises the council is NOT flagged: behaviour, not opinion, drives the score
    critical = client.get("/api/traction", params={"post_id": _post_id(client, "bad deal for Northbridge")}).json()
    assert critical["amplificationLevel"] == "low" and not critical["voteBursts"]


def test_topic_watchlist_report(client):
    topics = client.get("/api/watch/topics").json()
    harbor = next(t for t in topics if t["name"] == "Northbridge Harbor Bill")
    rep = client.get(f"/api/watch/topics/{harbor['id']}/report").json()
    assert rep["summary"]["amplifiedPosts"] >= 5
    assert any(n["id"] == "network:1" for n in rep["summary"]["networks"])
    top = rep["posts"][0]
    assert top["amplificationIndex"] >= 0.5 and top["authorNetwork"] == "network:1"
    pushers = {a["account"] for a in rep["accounts"] if a["flagged"] and a["posts"]}
    assert CAMPAIGN & pushers
    new = client.post("/api/watch/topics", json={"name": "test", "keywords": "ferry", "active": False}).json()
    assert client.get(f"/api/watch/topics/{new['id']}/report").json()["posts"]
    assert client.delete(f"/api/watch/topics/{new['id']}").json()["ok"]


class FakeReddit:
    """Stands in for the Reddit API to exercise the watchlist collector and score polling offline."""
    requests_made = 0

    def __init__(self):
        self.score = 10

    def _post(self, pid, title, sub="news", url="https://example-news.example/a"):
        return {"name": f"t3_{pid}", "id": pid, "author": "watch_tester", "subreddit": sub, "title": title, "selftext": "",
                "url": url, "created_utc": __import__("time").time() - 600, "score": self.score, "num_comments": 1,
                "upvote_ratio": 0.9, "permalink": f"/r/{sub}/comments/{pid}/x/"}

    def search(self, query, subreddit=None, limit=50, time_filter="week"):
        return [self._post("zz1", "Ferry strike announced for Monday"), self._post("zz2", "Unrelated cat picture")]

    def domain_posts(self, domain, limit=50):
        return []

    def subreddit_posts(self, name, limit, sort="new"):
        return []

    def post_with_comments(self, pid, expand_more=5):
        return self._post(pid, "Ferry strike announced for Monday"), [
            {"name": "t1_c1", "id": "c1", "author": "someone_else", "subreddit": "news", "body": "Finally.", "link_id": f"t3_{pid}",
             "parent_id": f"t3_{pid}", "created_utc": __import__("time").time() - 300, "score": 2}]

    def duplicates(self, pid, limit=50):
        return []

    def subreddit_about(self, name):
        return {"subscribers": 1000}

    def user_about(self, name):
        return {"name": name, "created_utc": 1600000000, "link_karma": 5, "comment_karma": 5, "has_verified_email": False}

    def user_submitted(self, name, limit):
        return []

    def user_comments(self, name, limit):
        return []

    def info(self, fullnames):
        self.score += 500
        return [self._post(f.split("_", 1)[1], "Ferry strike announced for Monday") for f in fullnames]


def test_watch_collector_and_score_polling_offline(client):
    from app.collectors.reddit import poll_scores, run_watch_topic
    from app.main import db
    fake = FakeReddit()
    tid = db.save_topic({"name": "ferry", "keywords": "ferry", "active": False})
    res = run_watch_topic(db, fake, db.topic(tid), log=lambda m: None)
    assert res == {"matching": 1, "new": 1, "fetched": 1, "tracked": 1}
    assert db.query("SELECT verified_email FROM accounts WHERE id='reddit:watch_tester'")[0]["verified_email"] == 0
    assert poll_scores(db, fake, ["reddit:t3_zz1"], log=lambda m: None) == 1
    snaps = db.snapshots("reddit:t3_zz1")
    assert len(snaps) >= 2 and snaps[-1]["score"] > snaps[0]["score"]
    assert any(w["post_id"] == "reddit:t3_zz1" for w in db.watched_posts())


def test_generic_import_and_manual_link(client):
    payload = {
        "accounts": [{"platform": "mastodon", "username": "someone", "bio": "also on twitter.com/someone_tw"}],
        "posts": [{"platform": "mastodon", "id": "m1", "author": "someone", "community": "#tech", "body": "hello", "created_utc": 1790000000}],
        "links": [{"a": "mastodon:someone", "b": "reddit:quiet_otter", "relation": "sameAs", "note": "test"}],
    }
    import json
    assert client.post("/api/import", content=json.dumps(payload)).json()["posts"] == 1
    g = client.get("/api/graph", params={"focus": "mastodon:someone", "depth": 1, "min_weight": 1}).json()
    assert any(e["data"]["type"] == "sameAs" for e in g["edges"])
    assert any(e["data"]["type"] == "discloses" for e in g["edges"])
