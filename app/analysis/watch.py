"""Topic watchlist: which posts match a topic, which of them were pushed artificially, and by whom.

A topic only decides *what to monitor* (keywords, domains, communities). Accounts in the report are
ranked and flagged purely by behaviour (automation, coordination, early boosting), never by the
opinion they express, so organic posts critical of anyone come out as organic.
"""
from __future__ import annotations

import re
import statistics
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone

from ..collectors.crossplatform import domain_of
from ..db import community_id
from .corpus import Corpus


def parse_terms(text: str | None) -> list[str]:
    """Comma/newline separated terms; "quoted phrases" stay together."""
    if not text:
        return []
    out = []
    for m in re.finditer(r'"([^"]+)"|([^,\n]+)', text):
        term = (m.group(1) or m.group(2) or "").strip()
        if term:
            out.append(term)
    return out


@dataclass
class TopicMatcher:
    keywords: list[re.Pattern] = field(default_factory=list)
    keyword_text: list[str] = field(default_factory=list)
    domains: list[str] = field(default_factory=list)
    communities: set[str] = field(default_factory=set)

    def reasons(self, title: str | None, body: str | None, url: str | None, community: str | None) -> list[str]:
        if self.communities and community not in self.communities and (self.keywords or self.domains):
            return []
        text = f"{title or ''}\n{body or ''}\n{url or ''}"
        hits = [kw for kw, rx in zip(self.keyword_text, self.keywords) if rx.search(text)]
        if self.domains:
            hosts = {domain_of(u) for u in re.findall(r"https?://\S+", text) + ([url] if url else []) if u}
            hits += [f"domain:{d}" for d in self.domains if any(h and (h == d or h.endswith("." + d)) for h in hosts)]
        if not self.keywords and not self.domains and community in self.communities:
            hits.append("community")
        return hits

    def match_post(self, title, body, url, community) -> bool:
        return bool(self.reasons(title, body, url, community))


def compile_topic(topic: dict) -> TopicMatcher:
    m = TopicMatcher()
    for kw in parse_terms(topic.get("keywords")):
        # word boundaries for Latin text; plain substring for other scripts (Devanagari etc.)
        rx = re.compile(rf"(?<!\w){re.escape(kw)}(?!\w)", re.I) if kw.isascii() else re.compile(re.escape(kw), re.I)
        m.keywords.append(rx)
        m.keyword_text.append(kw)
    m.domains = [d.lower().removeprefix("www.") for d in parse_terms(topic.get("domains"))]
    m.communities = {community_id("reddit", s) for s in parse_terms(topic.get("communities"))}
    return m


def search_query(topic: dict) -> str:
    terms = parse_terms(topic.get("keywords"))
    return " OR ".join(f'"{t}"' if " " in t else t for t in terms)


def topic_report(c: Corpus, topic: dict, analyze, bots: dict, coord: dict, cluster_of: dict, limit: int = 150) -> dict:
    """analyze(post_id) -> traction result (cached by the caller)."""
    m = compile_topic(topic)
    matched = []
    for p in c.posts.values():
        if p.get("is_stub") or not p["created_utc"]:
            continue
        why = m.reasons(p["title"], p["body"], p["url"], p["community_id"])
        if why:
            matched.append((p, why))
    matched.sort(key=lambda x: -(x[0]["created_utc"] or 0))
    member_of = coord.get("memberOf", {})
    rows, accounts = [], defaultdict(lambda: {"posts": 0, "earlyEngagements": 0, "comments": 0, "roles": set()})
    per_day: Counter = Counter()
    comments_day: Counter = Counter()
    comms: Counter = Counter()
    doms: Counter = Counter()
    for p, why in matched[:limit]:
        r = analyze(p["id"])
        day = datetime.fromtimestamp(p["created_utc"], timezone.utc).date().isoformat()
        per_day[day] += 1
        comments_day[day] += r["metrics"]["comments"]
        comms[p["community_id"]] += 1
        d = domain_of(p["url"]) if p["url"] else None
        if d and "reddit.com" not in d:
            doms[d] += 1
        author = p["account_id"]
        if author:
            accounts[author]["posts"] += 1
            accounts[author]["roles"].add("author")
        for e in r["engagers"]:
            accounts[e["account"]]["comments"] += e["comments"]
            if "early" in e["flags"]:
                accounts[e["account"]]["earlyEngagements"] += 1
                accounts[e["account"]]["roles"].add("early engager")
        ab = bots.get(author, {})
        rows.append({
            "id": p["id"], "title": p["title"], "community": c.community_label(p["community_id"]), "created_utc": p["created_utc"],
            "author": c.account_label(author), "authorId": author, "authorBot": ab.get("label"), "authorBotScore": ab.get("score"),
            "authorNetwork": member_of.get(author), "matched": why, "score": p["score"], "comments": r["metrics"]["comments"],
            "crossposts": len(r["crossposts"]), "amplificationIndex": r["amplificationIndex"], "amplificationLevel": r["amplificationLevel"],
            "voteBursts": len(r.get("voteBursts") or []),
        })
    rows.sort(key=lambda r: (-r["amplificationIndex"], -r["comments"], -(r["created_utc"] or 0)))
    acct_rows = []
    for aid, a in accounts.items():
        b = bots.get(aid, {})
        acct_rows.append({
            "account": aid, "label": c.account_label(aid), "posts": a["posts"], "earlyEngagements": a["earlyEngagements"],
            "comments": a["comments"], "roles": sorted(a["roles"]), "bot": b.get("label"), "botScore": b.get("score"),
            "network": member_of.get(aid), "operator": cluster_of.get(aid),
            "flagged": (b.get("score") or 0) >= 0.4 or aid in member_of or aid in cluster_of,
        })
    acct_rows.sort(key=lambda a: (-(a["posts"] * 3 + a["earlyEngagements"] * 2), -a["comments"]))
    pushers = [a for a in acct_rows if a["posts"] or a["earlyEngagements"]]
    amplified = [r for r in rows if r["amplificationIndex"] >= 0.5]
    flagged_pushers = [a for a in pushers if a["flagged"]]
    nets = Counter(a["network"] for a in pushers if a["network"])
    days = sorted(per_day)
    story = []
    if rows:
        story.append(f"{len(rows)} posts match this topic; {len(amplified)} show strong signs of artificial amplification (index ≥ 0.5).")
        if pushers:
            story.append(f"{len(flagged_pushers)} of the {len(pushers)} accounts that posted or engaged early are automated, "
                         f"suspicious, coordinated or part of a multi-account operator.")
        if nets:
            story.append("Coordinated networks involved: " + ", ".join(f"{k.replace('network:', 'Network #')} ({v} accounts)"
                                                                     for k, v in nets.most_common()) + ".")
        organic = [r for r in rows if r["amplificationIndex"] < 0.3]
        if organic:
            story.append(f"{len(organic)} matching posts look organic (index < 0.3): their traction came from ordinary engagement.")
    return {
        "topic": topic, "matchedPosts": len(matched), "posts": rows, "accounts": acct_rows[:200],
        "summary": {
            "posts": len(rows), "comments": sum(r["comments"] for r in rows), "accounts": len(acct_rows),
            "amplifiedPosts": len(amplified), "pushers": len(pushers), "flaggedPushers": len(flagged_pushers),
            "medianAmplification": round(statistics.median([r["amplificationIndex"] for r in rows]), 3) if rows else None,
            "networks": [{"id": k, "accounts": v} for k, v in nets.most_common()],
        },
        "timeline": [{"day": d, "posts": per_day[d], "comments": comments_day[d]} for d in days],
        "communities": [{"label": c.community_label(k), "posts": v} for k, v in comms.most_common(10)],
        "domains": [{"label": k, "posts": v} for k, v in doms.most_common(10)],
        "narrative": story,
    }
