"""How a post gained traction, who drove it, and who it targets."""
from __future__ import annotations

import math
import re
import statistics
from collections import Counter, defaultdict

from ..collectors.crossplatform import COMMON_DOMAINS, extract_subreddit_mentions, extract_user_mentions
from .coordination import normalize_url
from .corpus import Corpus, Item

DAY = 86400
EARLY = 30 * 60
NEW_ACCOUNT_DAYS = 30
LOW_KARMA = 100

POSITIVE = re.compile(r"\b(great|best|love|amazing|awesome|thanks|thank you|legit|insane|perfect|recommend|reccomend|steal|"
                      r"ordered|grabbed|worth|excellent|fantastic|confirm|impressive|good deal|must buy)\b", re.I)
NEGATIVE = re.compile(r"\b(scam|ad|advert|sus|fake|astroturf\w*|shill\w*|bot|bots|spam|avoid|worse|worst|bad|never heard|"
                      r"misleading|paid|suspicious|overpriced|waste)\b", re.I)
STOP = set("""a an the and or but if then so to of in on at for with from by is are was were be been it its this that these those
i you he she we they me my your our their them his her as not no yes do does did have has had just than too very can will would
should could about into over out up down more most some any all what which who whom when where why how there here also get got
one two like really much many way""".split())


def stance(text: str) -> int:
    return len(POSITIVE.findall(text)) - 2 * len(NEGATIVE.findall(text))


def _words(text: str) -> set:
    return {w for w in re.findall(r"[a-z']+", text.lower()) if w not in STOP and len(w) > 2}


def _depths(post_id: str, comments: list[Item]) -> dict[str, int]:
    by_id = {c.id: c for c in comments}
    depth: dict[str, int] = {}

    def d(cid: str, guard: int = 0) -> int:
        if cid in depth:
            return depth[cid]
        cm = by_id.get(cid)
        if cm is None or guard > 50:
            return 0
        val = 1 if (cm.parent_id == post_id or cm.parent_id not in by_id) else d(cm.parent_id, guard + 1) + 1
        depth[cid] = val
        return val

    for cm in comments:
        d(cm.id)
    return depth


def _early_share(c: Corpus, pid: str) -> float | None:
    post = c.posts.get(pid)
    comments = [x for x in c.by_post.get(pid, []) if x.created_utc]
    if not post or not post["created_utc"] or len(comments) < 5:
        return None
    return sum(1 for x in comments if x.created_utc - post["created_utc"] <= EARLY) / len(comments)


def list_posts(c: Corpus, q: str = "", community: str | None = None, limit: int = 100) -> list[dict]:
    out = []
    ql = q.lower()
    for p in c.posts.values():
        if p.get("is_stub") or p["crosspost_parent"]:
            continue
        if community and p["community_id"] != community:
            continue
        n = len(c.by_post.get(p["id"], []))
        if ql and ql not in (p["title"] or "").lower() and ql not in c.account_label(p["account_id"]).lower() and ql not in p["id"]:
            continue
        out.append({"id": p["id"], "title": p["title"] or (p["body"] or "")[:80], "author": c.account_label(p["account_id"]),
                    "community": c.community_label(p["community_id"]), "created_utc": p["created_utc"], "score": p["score"],
                    "comments": n, "crossposts": len(c.crossposts.get(p["id"], [])), "platform": p["platform"]})
    out.sort(key=lambda x: (-(x["comments"] + 3 * x["crossposts"]), -(x["score"] or 0)))
    return out[:limit]


def _community_ratio(c: Corpus, cid: str | None) -> float | None:
    """Typical comments-per-point in a community (how much discussion one upvote normally brings)."""
    ratios = [p["num_comments"] / p["score"] for p in c.posts.values()
              if p["community_id"] == cid and (p["score"] or 0) >= 10 and p["num_comments"] is not None]
    return statistics.median(ratios) if len(ratios) >= 5 else None


def vote_analysis(snaps: list[dict], t0: float | None, community_ratio: float | None) -> dict:
    """Score over time and intervals where points arrive far faster than discussion (vote bursts)."""
    pts = [s for s in snaps if s.get("score") is not None]
    if len(pts) < 3:
        return {"series": [], "bursts": [], "burstShare": None}
    base = t0 or pts[0]["ts"]
    series = [{"min": round((s["ts"] - base) / 60, 1), "score": s["score"], "comments": s.get("num_comments"),
               "ratio": s.get("upvote_ratio")} for s in pts]
    steps = []
    for a, b in zip(pts, pts[1:]):
        dt = max((b["ts"] - a["ts"]) / 60, 0.1)
        ds = (b["score"] or 0) - (a["score"] or 0)
        dc = (b.get("num_comments") or 0) - (a.get("num_comments") or 0)
        steps.append((a, b, dt, ds, dc, ds / dt))
    pos = [r for *_, r in steps if r > 0]
    med = statistics.median(pos) if pos else 0
    limit = 0.3 * community_ratio if community_ratio else 0.02
    bursts = []
    for a, b, dt, ds, dc, rate in steps:
        if ds >= 30 and rate >= 4 * max(med, 0.5) and dc / max(ds, 1) < limit:
            bursts.append({"fromMin": round((a["ts"] - base) / 60, 1), "toMin": round((b["ts"] - base) / 60, 1),
                           "scoreGain": ds, "commentGain": dc, "pointsPerMin": round(rate, 1)})
    merged: list[dict] = []
    for b in bursts:  # back-to-back intervals form one burst
        if merged and abs(merged[-1]["toMin"] - b["fromMin"]) < 0.01:
            m = merged[-1]
            m["toMin"], m["scoreGain"], m["commentGain"] = b["toMin"], m["scoreGain"] + b["scoreGain"], m["commentGain"] + b["commentGain"]
            m["pointsPerMin"] = round(m["scoreGain"] / max(m["toMin"] - m["fromMin"], 0.1), 1)
        else:
            merged.append(dict(b))
    bursts = merged
    final = max((s["score"] or 0) for s in pts) or 1
    share = sum(b["scoreGain"] for b in bursts) / final
    return {"series": series, "bursts": bursts, "burstShare": round(min(1.0, share), 3), "medianPointsPerMin": round(med, 2)}


def analyze_post(c: Corpus, post_id: str, link_scores: dict[tuple[str, str], dict] | None = None,
                 bots: dict[str, dict] | None = None, coord: dict | None = None, snapshots: list[dict] | None = None,
                 cache: dict | None = None) -> dict:
    post = c.posts.get(post_id)
    if not post:
        return {"error": f"Unknown post {post_id}"}
    link_scores = link_scores or {}
    bots = bots or {}
    member_of = (coord or {}).get("memberOf", {})
    cache = cache if cache is not None else {}
    t0 = post["created_utc"]
    author = post["account_id"]
    comments = [x for x in c.by_post.get(post_id, [])]
    timed = [x for x in comments if x.created_utc and t0]
    n = len(comments)

    # ------------------------------------------------------------ timeline
    timeline = {"bucketMinutes": 0, "buckets": [], "counts": [], "cumulative": []}
    metrics: dict = {"comments": n, "uniqueCommenters": len({x.account_id for x in comments if x.account_id}),
                     "score": post["score"], "upvoteRatio": post["upvote_ratio"], "reportedComments": post["num_comments"]}
    if timed:
        delays = sorted((x.created_utc - t0) / 60 for x in timed)
        span = max(delays[-1], 1)
        size = 5 if span <= 360 else 15 if span <= 1440 else 60 if span <= 4320 else 240
        nb = min(int(span // size) + 1, 200)
        counts = [0] * nb
        for dl in delays:
            counts[min(int(max(dl, 0) // size), nb - 1)] += 1
        cum, run = [], 0
        for k in counts:
            run += k
            cum.append(run)
        timeline = {"bucketMinutes": size, "buckets": [i * size for i in range(nb)], "counts": counts, "cumulative": cum}
        metrics.update({
            "firstCommentMin": round(delays[0], 1),
            "shareFirst30Min": round(sum(1 for d in delays if d <= 30) / len(delays), 3),
            "shareFirstHour": round(sum(1 for d in delays if d <= 60) / len(delays), 3),
            "halfLifeMin": round(delays[len(delays) // 2], 1),
            "peakBucketStartMin": counts.index(max(counts)) * size,
            "peakBucketComments": max(counts),
        })

    # baseline: typical early share in the same community
    key = f"early:{post['community_id']}"
    if key not in cache:
        peers = [s for pid, p in c.posts.items() if p["community_id"] == post["community_id"]
                 for s in [_early_share(c, pid)] if s is not None]
        cache[key] = round(statistics.median(peers), 3) if len(peers) >= 3 else None
    metrics["communityMedianShareFirst30Min"] = cache[key]

    # ------------------------------------------------------------ reply tree
    depth = _depths(post_id, comments)
    desc: Counter = Counter()
    by_id = {x.id: x for x in comments}
    for x in comments:  # credit every ancestor top-level comment
        p = x.parent_id
        guard = 0
        while p in by_id and guard < 60:
            desc[p] += 1
            p = by_id[p].parent_id
            guard += 1
    top_level = [x for x in comments if depth.get(x.id) == 1]
    metrics.update({"topLevel": len(top_level), "maxDepth": max(depth.values(), default=0),
                    "authorReplies": sum(1 for x in comments if x.account_id == author)})
    drivers = sorted(top_level, key=lambda x: -desc[x.id])[:5]

    # ------------------------------------------------------------ engagers
    author_posts = {pid for pid, p in c.posts.items() if p["account_id"] == author and pid != post_id} if author else set()
    first_seen: dict[str, Item] = {}
    per_account: Counter = Counter()
    for x in comments:
        if x.account_id and x.account_id != author:
            per_account[x.account_id] += 1
            first_seen.setdefault(x.account_id, x)
    engagers = []
    for aid, first in sorted(first_seen.items(), key=lambda kv: kv[1].created_utc or 0):
        acc = c.accounts.get(aid, {})
        delay = (first.created_utc - t0) / 60 if (first.created_utc and t0) else None
        age = (first.created_utc - acc["created_utc"]) / DAY if (acc.get("created_utc") and first.created_utc) else None
        karma = (acc.get("link_karma") or 0) + (acc.get("comment_karma") or 0) if acc.get("link_karma") is not None else None
        prior = {it.post_id for it in c.by_account.get(aid, []) if it.post_id in author_posts}
        link = link_scores.get(tuple(sorted((aid, author)))) if author else None
        texts = " ".join(x.body for x in comments if x.account_id == aid)
        flags = []
        if delay is not None and delay <= 30:
            flags.append("early")
        if age is not None and age < NEW_ACCOUNT_DAYS:
            flags.append("new account")
        if karma is not None and karma < LOW_KARMA:
            flags.append("low karma")
        if len(prior) >= 2:
            flags.append("repeat engager")
        if link and link["score"] >= 0.45:
            flags.append(f"possible alt of author ({link['level']})")
        bot = bots.get(aid, {})
        if bot.get("label") in ("likely automated", "suspicious"):
            flags.append(bot["label"] if bot["label"] == "likely automated" else "bot-like (suspicious)")
        if aid in member_of:
            flags.append(f"coordinated ({member_of[aid].replace('network:', 'network #')})")
        engagers.append({
            "account": aid, "label": c.account_label(aid), "firstDelayMin": round(delay, 1) if delay is not None else None,
            "comments": per_account[aid], "accountAgeDays": round(age, 1) if age is not None else None, "karma": karma,
            "priorPostsOfAuthorEngaged": len(prior), "linkScore": link["score"] if link else None,
            "stance": stance(texts), "flags": flags, "historyItems": len(c.by_account.get(aid, [])),
            "botScore": bot.get("score"), "botLabel": bot.get("label"), "network": member_of.get(aid),
        })

    # ------------------------------------------------------------ amplification signals
    early = [e for e in engagers if "early" in e["flags"]]
    suspicious = [e for e in engagers if any(f in e["flags"] for f in ("new account", "low karma")) or (e["linkScore"] or 0) >= 0.45
                  or (e["botScore"] or 0) >= 0.4 or e["network"]]
    early_susp = [e for e in early if e in suspicious]
    early_auto = [e for e in early if (e["botScore"] or 0) >= 0.4 or e["network"]]
    near_dupes = 0
    bodies = [(x.id, _words(x.body)) for x in comments if x.account_id != author]
    dup_ids: set = set()
    for i in range(len(bodies)):
        for j in range(i + 1, len(bodies)):
            a, b = bodies[i][1], bodies[j][1]
            if a and b and len(a & b) / len(a | b) >= 0.6:
                dup_ids.update({bodies[i][0], bodies[j][0]})
    near_dupes = len(dup_ids)
    early_stance = [stance(x.body) for x in timed if x.account_id != author and x.created_utc - t0 <= EARLY]
    late_stance = [stance(x.body) for x in timed if x.account_id != author and x.created_utc - t0 > EARLY]
    crossposts = []
    for xp in c.crossposts.get(post_id, []):
        xl = link_scores.get(tuple(sorted((xp["account_id"], author)))) if author and xp["account_id"] else None
        crossposts.append({
            "id": xp["id"], "community": c.community_label(xp["community_id"]), "communityId": xp["community_id"],
            "author": c.account_label(xp["account_id"]), "sameAuthor": xp["account_id"] == author,
            "authorLinkScore": xl["score"] if xl else None,
            "delayMin": round((xp["created_utc"] - t0) / 60, 1) if xp["created_utc"] and t0 else None,
            "score": xp["score"], "comments": len(c.by_post.get(xp["id"], [])),
            "subscribers": (c.communities.get(xp["community_id"]) or {}).get("subscribers"),
        })
    signals = []
    def add(name: str, value: float, detail: str) -> None:
        signals.append({"name": name, "value": round(min(1.0, max(0.0, value)), 3), "detail": detail})
    if early:
        add("Early engagement from new/low-karma/linked accounts", len(early_susp) / len(early),
            f"{len(early_susp)} of {len(early)} commenters in the first 30 minutes are new, low-karma, or possible alts of the author")
    base = metrics.get("communityMedianShareFirst30Min")
    if metrics.get("shareFirst30Min") is not None:
        excess = metrics["shareFirst30Min"] - (base if base is not None else 0.2)
        add("Unusually front-loaded comments", excess / 0.5,
            f"{metrics['shareFirst30Min']:.0%} of comments in the first 30 min" + (f" vs a community median of {base:.0%}" if base is not None else ""))
    repeat = [e for e in engagers if "repeat engager" in e["flags"]]
    if engagers:
        add("Repeat engagers of this author", len(repeat) / max(1, len(engagers)) * 2,
            f"{len(repeat)} commenters also commented on 2+ other posts by this author")
    if bodies:
        add("Templated / near-duplicate comments", near_dupes / max(1, len(bodies)) * 2, f"{near_dupes} comments are near-duplicates of another comment")
    if early_stance:
        e_pos = sum(1 for s in early_stance if s > 0) / len(early_stance)
        l_pos = sum(1 for s in late_stance if s > 0) / len(late_stance) if late_stance else None
        add("Uniformly positive early reaction", (e_pos - (l_pos if l_pos is not None else 0.4)) / 0.5,
            f"{e_pos:.0%} of early comments are positive" + (f" vs {l_pos:.0%} later" if l_pos is not None else ""))
    linked_xp = [x for x in crossposts if x["sameAuthor"] or (x["authorLinkScore"] or 0) >= 0.45]
    if crossposts:
        add("Crossposted by the author or linked accounts", len(linked_xp) / len(crossposts),
            f"{len(linked_xp)} of {len(crossposts)} crossposts were made by the author or accounts linked to them")
    if early:
        add("Automated or coordinated early engagers", len(early_auto) / len(early),
            f"{len(early_auto)} of {len(early)} early commenters score as bot-like or belong to a coordinated network")
    # the same link pushed by other accounts within minutes (coordinated sharing)
    co_push = []
    if coord and post.get("url"):
        key = normalize_url(post["url"])
        for b in coord.get("bursts", []):
            if key and b["key"] == "url:" + key:
                co_push = [a for a in b["accountIds"] if a != author]
        if co_push:
            add("Same link pushed by other accounts within minutes", len(co_push) / 4,
                f"{len(co_push)} other accounts posted this link within {coord.get('window', 600) // 60} minutes of each other")
    votes = vote_analysis(snapshots or [], t0, cache.setdefault(f"ratio:{post['community_id']}", _community_ratio(c, post["community_id"])))
    if votes["burstShare"] is not None:
        add("Vote bursts without matching discussion", votes["burstShare"] * 1.5,
            f"{len(votes['bursts'])} burst(s) brought {votes['burstShare']:.0%} of the post's points while comments barely moved"
            if votes["bursts"] else "score grew in step with discussion")
    weights = {"Early engagement from new/low-karma/linked accounts": 0.3, "Unusually front-loaded comments": 0.15,
               "Repeat engagers of this author": 0.15, "Templated / near-duplicate comments": 0.15,
               "Uniformly positive early reaction": 0.1, "Crossposted by the author or linked accounts": 0.15,
               "Automated or coordinated early engagers": 0.2, "Same link pushed by other accounts within minutes": 0.2,
               "Vote bursts without matching discussion": 0.25}
    tot_w = sum(weights[s["name"]] for s in signals) or 1
    amp_index = sum(weights[s["name"]] * s["value"] for s in signals) / tot_w

    # ------------------------------------------------------------ targets
    text = f"{post['title'] or ''}\n{post['body'] or ''}"
    if "df" not in cache:
        df: Counter = Counter()
        for p in c.posts.values():
            df.update(_words(f"{p['title'] or ''} {p['body'] or ''}"))
        cache["df"] = df
    df = cache["df"]
    N = max(1, len(c.posts))
    tf = Counter(w for w in re.findall(r"[a-z']+", text.lower()) if w not in STOP and len(w) > 2)
    keywords = sorted(tf, key=lambda w: -tf[w] * math.log(N / (1 + df[w])))[:10]
    domains = sorted({d for d in c.domains(c.items[post_id]) if d not in COMMON_DOMAINS}) if post_id in c.items else []
    author_replied = []
    for x in comments:
        if x.account_id == author and x.parent_id in by_id:
            parent = by_id[x.parent_id]
            author_replied.append({"to": c.account_label(parent.account_id), "theirStance": stance(parent.body),
                                   "theirText": parent.body[:160], "reply": x.body[:160]})
    # audience: where do engagers spend the rest of their time?
    home = post["community_id"]
    thread_ids = {post_id} | {x["id"] for x in crossposts}

    def audience(group: list[dict]) -> list[dict]:
        cnt: Counter = Counter()
        with_history = 0
        for e in group:
            comms = {it.community_id for it in c.by_account.get(e["account"], []) if it.post_id not in thread_ids and it.community_id}
            if comms:
                with_history += 1
                cnt.update(comms - {home})
        return [{"community": c.community_label(k), "id": k, "engagers": v, "share": round(v / with_history, 3)}
                for k, v in cnt.most_common(12)] if with_history else []

    organic = [e for e in engagers if e not in suspicious]
    targets = {
        "homeCommunity": {"id": home, "label": c.community_label(home), "subscribers": (c.communities.get(home) or {}).get("subscribers")},
        "crosspostCommunities": [{"label": x["community"], "subscribers": x["subscribers"], "by": x["author"]} for x in crossposts],
        "mentionedUsers": [f"u/{u}" for u in extract_user_mentions(text)],
        "mentionedCommunities": [f"r/{s}" for s in extract_subreddit_mentions(text)],
        "linkedDomains": domains, "keywords": keywords, "authorRepliedTo": author_replied[:15],
        "audienceAll": audience(engagers), "audienceOrganic": audience(organic), "audienceSuspicious": audience(suspicious),
        "engagersWithHistory": sum(1 for e in engagers if e["historyItems"] > e["comments"]),
    }

    # ------------------------------------------------------------ narrative
    story = []
    if metrics.get("shareFirst30Min") is not None:
        story.append(f"{metrics['shareFirst30Min']:.0%} of the {n} comments arrived in the first 30 minutes; half arrived within "
                     f"{_fmt_min(metrics['halfLifeMin'])}.")
    if early:
        story.append(f"{len(early_susp)} of the {len(early)} earliest commenters are new, low-karma, or behaviourally linked to the author.")
    if linked_xp:
        story.append(f"It was crossposted to {', '.join(x['community'] for x in linked_xp)} by the author or linked accounts, "
                     f"widening reach to {sum(x['subscribers'] or 0 for x in linked_xp):,} subscribers.")
    if targets["mentionedUsers"] or targets["mentionedCommunities"]:
        story.append("The post explicitly addresses " + ", ".join(targets["mentionedUsers"] + targets["mentionedCommunities"]) + ".")
    if targets["audienceOrganic"]:
        top = ", ".join(f"{a['community']} ({a['share']:.0%})" for a in targets["audienceOrganic"][:3])
        story.append(f"Organic commenters mostly also hang out in {top}: that is the audience the post actually reached.")
    if domains:
        story.append(f"It drives traffic to {', '.join(domains)}.")
    if author_replied:
        neg = [r for r in author_replied if r["theirStance"] < 0]
        if neg:
            story.append(f"The author replied to {len(neg)} critical comment(s), defending the post.")
    if early_auto:
        story.append(f"{len(early_auto)} early commenter(s) behave like bots or belong to a coordinated sharing network.")
    if co_push:
        story.append(f"{len(co_push)} other account(s) pushed the same link into other threads within minutes.")
    if votes["bursts"]:
        b0 = votes["bursts"][0]
        story.append(f"Vote burst: +{b0['scoreGain']} points between minute {b0['fromMin']:.0f} and {b0['toMin']:.0f} "
                     f"with only {b0['commentGain']} new comments, a pattern typical of bought or bot votes.")
    level = "high" if amp_index >= 0.5 else "elevated" if amp_index >= 0.3 else "low"
    story.append(f"Amplification index {amp_index:.2f} ({level}).")

    # ------------------------------------------------------------ thread graph
    flagged = {e["account"]: e["flags"] for e in engagers}
    elements = [{"data": {"id": post_id, "label": (post["title"] or "post")[:40], "kind": "post", "author": c.account_label(author)}}]
    for x in comments[:400]:
        elements.append({"data": {"id": x.id, "label": c.account_label(x.account_id), "kind": "author" if x.account_id == author else "comment",
                                  "flags": flagged.get(x.account_id, []), "delayMin": round((x.created_utc - t0) / 60, 1) if x.created_utc and t0 else None,
                                  "text": x.body[:200], "account": x.account_id}})
        parent = x.parent_id if x.parent_id in by_id or x.parent_id == post_id else post_id
        elements.append({"data": {"id": f"e:{x.id}", "source": x.id, "target": parent, "type": "replyTo"}})
    for xp in crossposts:
        elements.append({"data": {"id": xp["id"], "label": f"{xp['community']}", "kind": "crosspost"}})
        elements.append({"data": {"id": f"x:{xp['id']}", "source": post_id, "target": xp["id"], "type": "crosspostedTo"}})

    return {
        "post": {"id": post_id, "title": post["title"], "body": (post["body"] or "")[:1500], "url": post["url"],
                 "permalink": post["permalink"], "author": c.account_label(author), "authorId": author,
                 "community": c.community_label(post["community_id"]), "created_utc": t0, "platform": post["platform"],
                 "stub": bool(post.get("is_stub"))},
        "metrics": metrics, "timeline": timeline,
        "drivers": [{"label": c.account_label(x.account_id), "account": x.account_id, "replies": desc[x.id],
                     "delayMin": round((x.created_utc - t0) / 60, 1) if x.created_utc and t0 else None, "text": x.body[:200]} for x in drivers],
        "engagers": engagers, "signals": signals, "amplificationIndex": round(amp_index, 3), "amplificationLevel": level,
        "votes": votes, "voteBursts": votes["bursts"], "coPushers": [{"account": a, "label": c.account_label(a)} for a in co_push],
        "authorBot": bots.get(author), "authorNetwork": member_of.get(author),
        "crossposts": crossposts, "targets": targets, "narrative": story, "thread": elements,
    }


def _fmt_min(m: float | None) -> str:
    if m is None:
        return "?"
    if m < 90:
        return f"{m:.0f} min"
    if m < 60 * 48:
        return f"{m / 60:.1f} h"
    return f"{m / 1440:.1f} days"
