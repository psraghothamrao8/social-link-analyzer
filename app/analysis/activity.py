"""Posting regularity, and where regular posters are active besides their home community."""
from __future__ import annotations

import math
import statistics
from collections import Counter, defaultdict
from datetime import datetime, timezone

from .corpus import Corpus, Item

DAY = 86400


def cadence(items: list[Item]) -> dict | None:
    ts = sorted(i.created_utc for i in items if i.created_utc)
    if len(ts) < 2:
        return None
    span_days = max(1.0, (ts[-1] - ts[0]) / DAY)
    days = sorted({datetime.fromtimestamp(t, timezone.utc).date() for t in ts})
    streak = best = 1
    for a, b in zip(days, days[1:]):
        streak = streak + 1 if (b - a).days == 1 else 1
        best = max(best, streak)
    gaps = [b - a for a, b in zip(ts, ts[1:])]
    mean_gap = statistics.mean(gaps)
    cv = statistics.pstdev(gaps) / mean_gap if mean_gap > 0 else 0.0
    hours = Counter(datetime.fromtimestamp(t, timezone.utc).hour for t in ts)
    entropy = -sum((k / len(ts)) * math.log(k / len(ts)) for k in hours.values()) / math.log(24)
    active_ratio = min(1.0, len(days) / math.ceil(span_days + 1e-9))
    per_day = len(ts) / span_days
    score = 0.35 * active_ratio + 0.25 / (1 + cv) + 0.2 * min(1.0, per_day) + 0.2 * (1 - entropy)
    peak_hour = hours.most_common(1)[0][0]
    if len(ts) >= 10 and entropy < 0.35 and cv < 0.6:
        pattern = "scheduled (same time every day: bot or automation likely)"
    elif active_ratio >= 0.6:
        pattern = "daily regular"
    elif per_day >= 1:
        pattern = "high-volume"
    elif active_ratio >= 0.3:
        pattern = "frequent"
    else:
        pattern = "occasional"
    return {
        "items": len(ts), "first": ts[0], "last": ts[-1], "spanDays": round(span_days, 1), "activeDays": len(days),
        "activeDayRatio": round(active_ratio, 3), "perDay": round(per_day, 2), "gapCV": round(cv, 2),
        "medianGapHours": round(statistics.median(gaps) / 3600, 1), "hourEntropy": round(entropy, 3), "peakHourUTC": peak_hour,
        "longestStreakDays": best, "regularity": round(score, 3), "pattern": pattern,
    }


def _breakdown(c: Corpus, items: list[Item]) -> list[dict]:
    rows: dict[str, dict] = defaultdict(lambda: {"posts": 0, "comments": 0, "first": None, "last": None, "score": 0})
    for it in items:
        if not it.community_id:
            continue
        r = rows[it.community_id]
        r["posts" if it.kind == "post" else "comments"] += 1
        r["score"] += it.score or 0
        if it.created_utc:
            r["first"] = min(r["first"] or it.created_utc, it.created_utc)
            r["last"] = max(r["last"] or it.created_utc, it.created_utc)
    total = sum(r["posts"] + r["comments"] for r in rows.values()) or 1
    out = []
    for cid, r in rows.items():
        n = r["posts"] + r["comments"]
        out.append({"id": cid, "label": c.community_label(cid), "posts": r["posts"], "comments": r["comments"], "total": n,
                    "share": round(n / total, 3), "first": r["first"], "last": r["last"], "avgScore": round(r["score"] / n, 1),
                    "subscribers": (c.communities.get(cid) or {}).get("subscribers")})
    out.sort(key=lambda x: -x["total"])
    return out


def regular_posters(c: Corpus, community: str | None = None, basis: str = "posts", min_items: int = 8,
                    min_regularity: float = 0.45, limit: int = 100) -> dict:
    """Accounts that post regularly (optionally: regularly *in one community*) and their other communities."""
    rows = []
    for aid, items in c.by_account.items():
        scope = [i for i in items if (basis == "all" or i.kind == "post") and (community is None or i.community_id == community)]
        if len(scope) < min_items:
            continue
        cad = cadence(scope)
        if not cad or cad["regularity"] < min_regularity:
            continue
        comms = _breakdown(c, items)
        home = community or (max(((r["id"], r["posts"]) for r in comms), key=lambda x: x[1])[0] if comms else None)
        others = [r for r in comms if r["id"] != home]
        rows.append({
            "account": aid, "label": c.account_label(aid), "platform": aid.split(":", 1)[0], **cad,
            "home": c.community_label(home) if home else None, "homeId": home,
            "otherCommunities": [{"label": r["label"], "id": r["id"], "total": r["total"], "share": r["share"]} for r in others[:8]],
            "otherCount": len(others), "totalItems": len(items),
            "external": sorted({f"{l['platform']}:{l['handle']}" for l in c.ext_by_account.get(aid, [])}),
        })
    rows.sort(key=lambda r: -r["regularity"])
    rows = rows[:limit]
    # Which other communities do this group of regulars share?
    agg: Counter = Counter()
    for r in rows:
        agg.update(o["id"] for o in r["otherCommunities"])
    co = [{"id": k, "label": c.community_label(k), "regulars": v, "share": round(v / len(rows), 3)} for k, v in agg.most_common(15)] if rows else []
    return {"community": c.community_label(community) if community else None, "basis": basis, "posters": rows, "sharedOtherCommunities": co}


def account_profile(c: Corpus, aid: str, similar: list[dict] | None = None) -> dict:
    items = c.by_account.get(aid, [])
    acc = c.accounts.get(aid)
    if not acc and not items:
        return {"error": f"Unknown account {aid}"}
    heat = [[0] * 24 for _ in range(7)]
    daily: Counter = Counter()
    for it in items:
        if it.created_utc:
            dt = datetime.fromtimestamp(it.created_utc, timezone.utc)
            heat[dt.weekday()][dt.hour] += 1
            daily[dt.date().isoformat()] += 1
    comms = _breakdown(c, items)
    home = comms[0]["id"] if comms else None
    replied_to: Counter = Counter()
    replied_by: Counter = Counter()
    for it in items:
        if it.kind == "comment":
            t = c.author_of(it.parent_id)
            if t and t != aid:
                replied_to[t] += 1
    for it in c.items.values():
        if it.kind == "comment" and it.account_id and it.account_id != aid and c.author_of(it.parent_id) == aid:
            replied_by[it.account_id] += 1
    posts_cad = cadence([i for i in items if i.kind == "post"])
    return {
        "account": aid, "label": c.account_label(aid), "platform": aid.split(":", 1)[0],
        "info": {k: acc.get(k) for k in ("username", "created_utc", "link_karma", "comment_karma", "bio", "status", "source")} if acc else {},
        "counts": {"posts": sum(1 for i in items if i.kind == "post"), "comments": sum(1 for i in items if i.kind == "comment")},
        "cadenceAll": cadence(items), "cadencePosts": posts_cad,
        "communities": comms, "home": c.community_label(home) if home else None,
        "otherCommunities": comms[1:],
        "heatmap": heat, "daily": sorted(daily.items()),
        "external": [{"platform": l["platform"], "handle": l["handle"], "source": l["source"], "evidence": l["url"], "hits": l["hits"]}
                     for l in c.ext_by_account.get(aid, [])],
        "repliesTo": [{"account": k, "label": c.account_label(k), "count": v} for k, v in replied_to.most_common(10)],
        "repliedBy": [{"account": k, "label": c.account_label(k), "count": v} for k, v in replied_by.most_common(10)],
        "similarAccounts": [s for s in (similar or []) if s["score"] >= 0.45][:8],
        "recent": [{"id": i.id, "kind": i.kind, "community": c.community_label(i.community_id), "created_utc": i.created_utc,
                    "text": i.text[:240], "score": i.score} for i in items[-15:][::-1]],
    }
