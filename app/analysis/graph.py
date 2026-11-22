"""Account-level knowledge graph built on the ontology, plus route finding between any two nodes."""
from __future__ import annotations

import math
from collections import Counter, defaultdict, deque

import networkx as nx

from ..collectors.crossplatform import COMMON_DOMAINS
from .corpus import Corpus
from .ontology import RELATIONS

DEFAULT_EDGES = ["activeIn", "interactsWith", "amplifies", "mentions", "discloses", "possibleSameOperator", "sameAs", "controlledBy",
                 "coSharesWith", "memberOfNetwork"]
AMPLIFY_WINDOW = 30 * 60


def _edge(rel: str, s: str, t: str, **data) -> dict:
    return {"data": {"id": f"{rel}|{s}|{t}", "source": s, "target": t, "type": rel,
                     "label": RELATIONS[rel]["label"], **data}}


def raw_relations(c: Corpus, link_pairs: list[dict] | None = None, clusters: list[dict] | None = None,
                  coord: dict | None = None) -> list[dict]:
    """Every relation instance in the corpus at account level (unfiltered)."""
    edges: list[dict] = []

    # activeIn
    active: dict[tuple[str, str], Counter] = defaultdict(Counter)
    for aid, items in c.by_account.items():
        for it in items:
            if it.community_id:
                active[(aid, it.community_id)][it.kind] += 1
    for (aid, cid), cnt in active.items():
        edges.append(_edge("activeIn", aid, cid, weight=sum(cnt.values()), posts=cnt["post"], comments=cnt["comment"]))

    # interactsWith (replies) and amplifies (early comments on someone's posts)
    replies: Counter = Counter()
    early: dict[tuple[str, str], set] = defaultdict(set)
    for it in c.items.values():
        if it.kind != "comment" or not it.account_id:
            continue
        target = c.author_of(it.parent_id)
        if target and target != it.account_id:
            replies[(it.account_id, target)] += 1
        post = c.posts.get(it.post_id)
        if post and post["account_id"] and post["account_id"] != it.account_id and post["created_utc"] and it.created_utc:
            if 0 <= it.created_utc - post["created_utc"] <= AMPLIFY_WINDOW:
                early[(it.account_id, post["account_id"])].add(it.post_id)
    for (a, b), n in replies.items():
        edges.append(_edge("interactsWith", a, b, weight=n))
    for (a, b), posts in early.items():
        if len(posts) >= 2:
            edges.append(_edge("amplifies", a, b, weight=len(posts)))

    # mentions and linksTo
    mentions: Counter = Counter()
    domains: Counter = Counter()
    for aid, items in c.by_account.items():
        for it in items:
            for m in c.mentions(it):
                if m != aid:
                    mentions[(aid, m)] += 1
            for d in set(c.domains(it)):
                if d not in COMMON_DOMAINS and not d.endswith(("reddit.com", "redd.it")):
                    domains[(aid, "domain:" + d)] += 1
    for (a, b), n in mentions.items():
        edges.append(_edge("mentions", a, b, weight=n))
    for (a, d), n in domains.items():
        edges.append(_edge("linksTo", a, d, weight=n))

    # discloses (bio links count more than links in posts/comments)
    disc: dict[tuple[str, str], dict] = {}
    for l in c.external_links:
        key = (l["account_id"], f"{l['platform']}:{l['handle']}")
        cur = disc.setdefault(key, {"weight": 0, "sources": set(), "evidence": l.get("url")})
        cur["weight"] += l.get("hits") or 1
        cur["sources"].add(l["source"])
    for (a, ext), v in disc.items():
        if a == ext:
            continue
        edges.append(_edge("discloses", a, ext, weight=v["weight"], sources=sorted(v["sources"]),
                           strength="self-disclosed (bio)" if "bio" in v["sources"] else "linked in content",
                           evidence=v["evidence"]))

    # identity hypotheses and confirmations
    for p in link_pairs or []:
        edges.append(_edge("possibleSameOperator", p["a"], p["b"], weight=round(p["score"], 3),
                           confidence=round(p["score"], 3), level=p.get("level")))
    for m in c.manual_links:
        rel = m["relation"] if m["relation"] in RELATIONS else "sameAs"
        edges.append(_edge(rel, m["a"], m["b"], weight=1, note=m.get("note"), manual=True))
    for cl in clusters or []:
        for aid in cl["members"]:
            edges.append(_edge("controlledBy", aid, cl["id"], weight=1, confidence=cl["score"]))
    if coord:
        for e in coord.get("coShareEdges", []):
            edges.append(_edge("coSharesWith", e["a"], e["b"], weight=e["weight"]))
        for net in coord.get("networks", []):
            for aid in net["members"]:
                edges.append(_edge("memberOfNetwork", aid, net["id"], weight=1))
    return edges


def _node(c: Corpus, nid: str, clusters_by_id: dict[str, dict], bots: dict | None = None, networks: dict | None = None) -> dict:
    if nid.startswith("domain:"):
        return {"data": {"id": nid, "label": nid[7:], "cls": "Domain"}}
    if nid.startswith("agent:"):
        cl = clusters_by_id.get(nid, {})
        return {"data": {"id": nid, "label": cl.get("label", nid), "cls": "Agent", "confidence": cl.get("score")}}
    if nid.startswith("network:"):
        net = (networks or {}).get(nid, {})
        return {"data": {"id": nid, "label": net.get("label", nid), "cls": "CoordinatedNetwork",
                         "confidence": net.get("meanBotScore")}}
    if nid in c.communities:
        comm = c.communities[nid]
        return {"data": {"id": nid, "label": c.community_label(nid), "cls": "Subreddit" if comm["platform"] == "reddit" else "Community",
                         "platform": comm["platform"], "subscribers": comm.get("subscribers")}}
    platform = nid.split(":", 1)[0]
    acc = c.accounts.get(nid)
    data = {"id": nid, "label": c.account_label(nid), "cls": "RedditAccount" if platform == "reddit" else "ExternalAccount",
            "platform": platform, "items": len(c.by_account.get(nid, [])), "collected": acc is not None and bool(c.by_account.get(nid))}
    if acc:
        data.update({"created_utc": acc.get("created_utc"), "karma": (acc.get("link_karma") or 0) + (acc.get("comment_karma") or 0),
                     "status": acc.get("status"), "bio": acc.get("bio")})
    b = (bots or {}).get(nid)
    if b and b.get("items"):
        data.update({"botScore": b["score"], "botLabel": b["label"]})
    return {"data": data}


def build_graph(
    c: Corpus, link_pairs: list[dict] | None = None, clusters: list[dict] | None = None,
    edge_types: list[str] | None = None, min_weight: int = 2, focus: str | None = None, depth: int = 2,
    max_nodes: int = 250, platforms: list[str] | None = None, include: list[str] | None = None,
    bots: dict | None = None, coord: dict | None = None,
) -> dict:
    edge_types = edge_types or DEFAULT_EDGES
    clusters_by_id = {cl["id"]: cl for cl in clusters or []}
    networks = {n["id"]: n for n in (coord or {}).get("networks", [])}
    edges = []
    for e in raw_relations(c, link_pairs, clusters, coord):
        d = e["data"]
        if d["type"] not in edge_types:
            continue
        if d["type"] in ("activeIn", "interactsWith", "linksTo") and (d.get("weight") or 0) < min_weight and not (focus and focus in (d["source"], d["target"])):
            continue
        if platforms:
            ps = {x.split(":", 1)[0] for x in (d["source"], d["target"]) if not x.startswith(("domain:", "agent:"))}
            if not ps & set(platforms):
                continue
        edges.append(e)

    adj: dict[str, set] = defaultdict(set)
    for e in edges:
        adj[e["data"]["source"]].add(e["data"]["target"])
        adj[e["data"]["target"]].add(e["data"]["source"])

    if focus:
        keep = {focus}
        frontier = deque([(focus, 0)])
        while frontier:
            n, dist = frontier.popleft()
            if dist >= depth:
                continue
            for m in adj.get(n, ()):
                # do not expand through community/domain hubs beyond the first hop: they connect everyone
                if m not in keep:
                    keep.add(m)
                    if not (m in c.communities or m.startswith(("domain:", "network:"))):
                        frontier.append((m, dist + 1))
    else:
        keep = set(adj)
    keep |= set(include or [])

    if len(keep) > max_nodes:
        must = {focus, *(include or [])} - {None}
        strength: Counter = Counter()
        for e in edges:
            w = math.log1p(e["data"].get("weight") or 1) * (3 if e["data"]["type"] not in ("activeIn", "linksTo") else 1)
            strength[e["data"]["source"]] += w
            strength[e["data"]["target"]] += w
        ranked = [n for n, _ in strength.most_common() if n in keep and n not in must]
        keep = must | set(ranked[: max_nodes - len(must)])

    edges = [e for e in edges if e["data"]["source"] in keep and e["data"]["target"] in keep]
    used = {e["data"]["source"] for e in edges} | {e["data"]["target"] for e in edges} | ({focus} if focus else set())
    nodes = [_node(c, n, clusters_by_id, bots, networks) for n in sorted(used)]
    deg: Counter = Counter()
    for e in edges:
        deg[e["data"]["source"]] += 1
        deg[e["data"]["target"]] += 1
    for n in nodes:
        n["data"]["degree"] = deg[n["data"]["id"]]
    return {"nodes": nodes, "edges": edges,
            "stats": {"nodes": len(nodes), "edges": len(edges), "edgeTypes": dict(Counter(e["data"]["type"] for e in edges))}}


# --------------------------------------------------------------------- routes
BASE_COST = {
    "sameAs": 0.2, "possibleSameOperator": 0.6, "discloses": 0.5, "controlledBy": 0.6, "amplifies": 0.9,
    "coSharesWith": 0.7, "memberOfNetwork": 0.7,
    "interactsWith": 1.0, "mentions": 1.2, "activeIn": 2.5, "linksTo": 2.5, "crosspostedTo": 1.5,
}


def _cost(d: dict, hub_degree: int) -> float:
    rel = d["type"]
    base = BASE_COST.get(rel, 2.0)
    w = d.get("weight") or 1
    if rel == "possibleSameOperator":
        return base + (1 - float(d.get("confidence") or 0))
    if rel == "discloses":
        return base if "bio" in (d.get("sources") or []) else base + 0.5
    cost = base / (1 + math.log1p(w) * 0.5)
    if rel in ("activeIn", "linksTo"):
        cost += 0.3 * math.log1p(hub_degree)  # paths through huge hubs (r/AskReddit) explain little
    return cost


def find_routes(c: Corpus, source: str, target: str, k: int = 3, link_pairs: list[dict] | None = None,
                clusters: list[dict] | None = None, edge_types: list[str] | None = None, max_hops: int = 6,
                bots: dict | None = None, coord: dict | None = None) -> dict:
    edge_types = edge_types or DEFAULT_EDGES + ["linksTo"]
    networks = {n["id"]: n for n in (coord or {}).get("networks", [])}
    rels = [e for e in raw_relations(c, link_pairs, clusters, coord) if e["data"]["type"] in edge_types]
    deg: Counter = Counter()
    for e in rels:
        deg[e["data"]["source"]] += 1
        deg[e["data"]["target"]] += 1
    G = nx.Graph()
    for e in rels:
        d = e["data"]
        s, t = d["source"], d["target"]
        hub = max(deg[s], deg[t]) if d["type"] in ("activeIn", "linksTo") else 0
        cost = _cost(d, hub)
        if G.has_edge(s, t):
            G[s][t]["rels"].append(d)
            G[s][t]["cost"] = min(G[s][t]["cost"], cost)
        else:
            G.add_edge(s, t, cost=cost, rels=[d])
    if source not in G or target not in G:
        missing = [n for n in (source, target) if n not in G]
        return {"routes": [], "message": f"No relations recorded for {', '.join(missing)}. Collect its activity first."}
    clusters_by_id = {cl["id"]: cl for cl in clusters or []}
    routes = []
    try:
        for path in nx.shortest_simple_paths(G, source, target, weight="cost"):
            if len(path) - 1 > max_hops:
                break
            hops, total = [], 0.0
            for a, b in zip(path, path[1:]):
                ed = G[a][b]
                total += ed["cost"]
                hops.append({
                    "from": a, "to": b, "cost": round(ed["cost"], 3),
                    "relations": [{
                        "type": r["type"], "label": r["label"], "weight": r.get("weight"), "edgeId": r["id"],
                        "direction": "↔" if RELATIONS[r["type"]].get("symmetric") else ("→" if r["source"] == a else "←"),
                    } for r in sorted(ed["rels"], key=lambda r: BASE_COST.get(r["type"], 2))],
                })
            routes.append({
                "nodes": path, "hops": hops, "cost": round(total, 3), "strength": round(math.exp(-total / 3), 3),
                "sentence": " ".join(
                    [_short(c, path[0])] + [f"—{h['relations'][0]['label']}{h['relations'][0]['direction']} {_short(c, h['to'])}" for h in hops]
                ),
            })
            if len(routes) >= k:
                break
    except nx.NetworkXNoPath:
        return {"routes": [], "message": "No route connects these nodes with the selected relation types."}
    elements_nodes = {n for r in routes for n in r["nodes"]}
    elements_edges = {rel["edgeId"] for r in routes for h in r["hops"] for rel in h["relations"]}
    return {
        "routes": routes,
        "elements": {
            "nodes": [_node(c, n, clusters_by_id, bots, networks) for n in elements_nodes],
            "edges": [e for e in rels if e["data"]["id"] in elements_edges],
        },
        "message": f"{len(routes)} route(s) found" if routes else "No route found within the hop limit.",
    }


def _short(c: Corpus, nid: str) -> str:
    if nid.startswith("domain:"):
        return nid[7:]
    if nid.startswith(("agent:", "network:")):
        return nid
    if nid in c.communities:
        return c.community_label(nid)
    return c.account_label(nid)
