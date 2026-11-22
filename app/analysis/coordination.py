"""Coordinated sharing: different accounts posting the same link or near-identical text within minutes.

This is the co-sharing method used in coordinated-inauthentic-behaviour research (e.g. CooRnet):
one coincidence means little, so a pair of accounts only counts as coordinated when they co-share
*several different* links/texts inside the time window. Pairs are then grouped into networks.
"""
from __future__ import annotations

import hashlib
import re
import statistics
from collections import Counter, defaultdict
from urllib.parse import parse_qsl, urlencode, urlparse

from ..collectors.crossplatform import URL_RE
from .corpus import Corpus

TRACKING = re.compile(r"^(utm_\w+|ref|ref_src|ref_url|fbclid|gclid|igshid|si|share|s|mc_cid|mc_eid|cmpid|smid)$", re.I)
INTERNAL = ("reddit.com", "redd.it")


def normalize_url(url: str) -> str | None:
    try:
        u = urlparse(url if "://" in url else "http://" + url)
    except ValueError:
        return None
    host = (u.hostname or "").lower().removeprefix("www.").removeprefix("m.")
    if not host or host.endswith(INTERNAL):
        return None
    q = urlencode([(k, v) for k, v in parse_qsl(u.query) if not TRACKING.match(k)])
    return host + (u.path.rstrip("/") or "") + (f"?{q}" if q else "")


def _norm_text(t: str) -> str:
    t = URL_RE.sub(" ", t.lower())
    t = re.sub(r"[^a-z0-9\u0080-￿ ]+", " ", t)
    return re.sub(r"\s+", " ", t).strip()


def _shingles(t: str) -> set:
    w = t.split()
    return {" ".join(w[i:i + 3]) for i in range(max(1, len(w) - 2))}


def _shares(c: Corpus) -> tuple[list[tuple], list[tuple]]:
    """(url_shares, text_items). url share = (key, account, ts, item_id, community)."""
    urls, texts = [], []
    for it in c.items.values():
        if not it.account_id or not it.created_utc:
            continue
        if it.kind == "post" and c.posts[it.id].get("is_stub"):
            continue
        found = set()
        if it.url:
            k = normalize_url(it.url)
            if k:
                found.add(k)
        for m in URL_RE.finditer(it.body or ""):
            k = normalize_url(m.group(0))
            if k:
                found.add(k)
        for k in found:
            urls.append((k, it.account_id, it.created_utc, it.id, it.community_id))
        # headlines and longer texts; short "thanks!" style comments would collide by chance
        for raw, min_words in ((it.title, 6), (it.body, 8)):
            if raw:
                t = _norm_text(raw)
                if len(t.split()) >= min_words:
                    texts.append((t, it.account_id, it.created_utc, it.id, it.community_id, raw[:160]))
    return urls, texts


def detect(c: Corpus, window: int = 600, min_repeats: int = 2, bots: dict | None = None) -> dict:
    urls, texts = _shares(c)
    pair_keys: dict[tuple[str, str], set] = defaultdict(set)
    pair_deltas: dict[tuple[str, str], list] = defaultdict(list)
    key_deltas: dict[str, list] = defaultdict(list)
    key_info: dict[str, dict] = {}

    def link(a, b, key, delta):
        if a == b:
            return
        p = (a, b) if a < b else (b, a)
        pair_keys[p].add(key)
        pair_deltas[p].append(delta)
        key_deltas[key].append(delta)

    # same URL within the window
    by_url: dict[str, list] = defaultdict(list)
    for s in urls:
        by_url[s[0]].append(s)
    for k, shares in by_url.items():
        shares.sort(key=lambda s: s[2])
        key = "url:" + k
        key_info[key] = {"kind": "link", "display": k, "shares": shares}
        lo = 0
        for hi in range(len(shares)):
            while shares[hi][2] - shares[lo][2] > window:
                lo += 1
            for j in range(lo, hi):
                link(shares[j][1], shares[hi][1], key, shares[hi][2] - shares[j][2])

    # same or near-identical text within the window
    texts.sort(key=lambda s: s[2])
    sh = [_shingles(t[0]) for t in texts]
    group_of: dict[int, str] = {}
    lo = 0
    for hi in range(len(texts)):
        while texts[hi][2] - texts[lo][2] > window:
            lo += 1
        for j in range(lo, hi):
            if texts[j][1] == texts[hi][1]:
                continue
            a, b = sh[j], sh[hi]
            if texts[j][0] == texts[hi][0] or (a and b and len(a & b) / len(a | b) >= 0.6):
                gid = group_of.get(j) or ("text:" + hashlib.sha1(texts[j][0].encode()).hexdigest()[:12])
                group_of[j] = group_of[hi] = gid
                info = key_info.setdefault(gid, {"kind": "text", "display": texts[j][5], "shares": []})
                for idx in (j, hi):
                    s = texts[idx]
                    entry = (gid, s[1], s[2], s[3], s[4])
                    if entry not in info["shares"]:
                        info["shares"].append(entry)
                link(texts[j][1], texts[hi][1], gid, texts[hi][2] - texts[j][2])

    # pairs -> networks
    edges = {p: ks for p, ks in pair_keys.items() if len(ks) >= min_repeats}
    parent: dict[str, str] = {}

    def find(x):
        parent.setdefault(x, x)
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for a, b in edges:
        parent[find(a)] = find(b)
    groups: dict[str, list] = defaultdict(list)
    for x in list(parent):
        groups[find(x)].append(x)
    bots = bots or {}
    networks = []
    for members in groups.values():
        mset = set(members)
        keys = Counter()
        for (a, b), ks in edges.items():
            if a in mset:
                keys.update(ks)
        shares = [s for k in keys for s in key_info[k]["shares"] if s[1] in mset]
        comms = Counter(s[4] for s in shares if s[4])
        deltas = [d for (a, b), ds in pair_deltas.items() if a in mset and b in mset and (a, b) in edges for d in ds]
        per_member = Counter(s[1] for s in shares)
        bot_scores = [bots[m]["score"] for m in members if m in bots]
        items = []
        for k, _ in keys.most_common(12):
            info = key_info[k]
            ss = [s for s in info["shares"] if s[1] in mset]
            items.append({"key": k, "kind": info["kind"], "display": info["display"], "accounts": len({s[1] for s in ss}),
                          "shares": len(ss), "first": min(s[2] for s in ss), "spreadSec": round(max(s[2] for s in ss) - min(s[2] for s in ss)),
                          "medianGapSec": round(statistics.median(key_deltas[k])) if key_deltas[k] else None,
                          "communities": sorted({c.community_label(s[4]) for s in ss if s[4]})})
        networks.append({
            "members": sorted(members, key=lambda m: -per_member[m]),
            "size": len(members), "coSharedItems": len(keys), "shares": len(shares),
            "medianDelaySec": round(statistics.median(deltas)) if deltas else None,
            "communities": [{"id": k, "label": c.community_label(k), "shares": v} for k, v in comms.most_common(8)],
            "first": min((s[2] for s in shares), default=None), "last": max((s[2] for s in shares), default=None),
            "meanBotScore": round(statistics.mean(bot_scores), 3) if bot_scores else None,
            "automatedMembers": sum(1 for m in members if bots.get(m, {}).get("label") == "likely automated"),
            "items": items,
        })
    networks.sort(key=lambda n: (-n["size"], -n["coSharedItems"]))
    member_of = {}
    for i, n in enumerate(networks, 1):
        n["id"] = f"network:{i}"
        n["label"] = f"Network #{i} ({n['size']} accounts)"
        n["labels"] = {m: c.account_label(m) for m in n["members"]}
        for m in n["members"]:
            member_of[m] = n["id"]

    pairs = sorted(({"a": a, "b": b, "labelA": c.account_label(a), "labelB": c.account_label(b), "coShared": len(ks),
                     "medianDelaySec": round(statistics.median(pair_deltas[(a, b)])), "network": member_of.get(a)}
                    for (a, b), ks in edges.items()), key=lambda p: -p["coShared"])

    # bursts: one link/text pushed by 3+ accounts inside the window (whether or not they repeat)
    bursts = []
    for k, info in key_info.items():
        ss = sorted(info["shares"], key=lambda s: s[2])
        best = []
        lo = 0
        for hi in range(len(ss)):
            while ss[hi][2] - ss[lo][2] > window:
                lo += 1
            if len({s[1] for s in ss[lo:hi + 1]}) > len({s[1] for s in best}):
                best = ss[lo:hi + 1]
        accts = {s[1] for s in best}
        if len(accts) >= 3:
            bursts.append({"key": k, "kind": info["kind"], "display": info["display"], "accounts": len(accts),
                           "accountIds": sorted(accts), "first": best[0][2], "spreadSec": round(best[-1][2] - best[0][2]),
                           "communities": sorted({c.community_label(s[4]) for s in best if s[4]}),
                           "networks": sorted({member_of[a] for a in accts if a in member_of})})
    bursts.sort(key=lambda b: (-b["accounts"], b["spreadSec"]))
    return {"window": window, "minRepeats": min_repeats, "networks": networks, "pairs": pairs[:300],
            "bursts": bursts[:60], "memberOf": member_of,
            "coShareEdges": [{"a": a, "b": b, "weight": len(ks)} for (a, b), ks in edges.items()]}
