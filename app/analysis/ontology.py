"""The Social Link Ontology (SLO): classes and relations used by the graph, plus RDF export.

Aligned with SIOC (sioc:UserAccount, sioc:Forum, sioc:Post) and FOAF (foaf:OnlineAccount) so
exports load into standard RDF tooling (Protege, GraphDB, Neo4j n10s, Apache Jena).
"""
from __future__ import annotations

import json
import re

NS = "https://w3id.org/social-link-ontology#"
PREFIXES = {
    "slo": NS,
    "rdf": "http://www.w3.org/1999/02/22-rdf-syntax-ns#",
    "rdfs": "http://www.w3.org/2000/01/rdf-schema#",
    "owl": "http://www.w3.org/2002/07/owl#",
    "xsd": "http://www.w3.org/2001/XMLSchema#",
    "sioc": "http://rdfs.org/sioc/ns#",
    "foaf": "http://xmlns.com/foaf/0.1/",
}

CLASSES: dict[str, dict] = {
    "Agent": {"parent": "foaf:Agent", "label": "Operator (hypothesis)",
              "comment": "A real person or organisation hypothesised to control one or more accounts."},
    "Platform": {"parent": "owl:Thing", "label": "Platform", "comment": "A social network (Reddit, Twitter/X, YouTube, ...)."},
    "Account": {"parent": "sioc:UserAccount", "label": "Account", "comment": "A user account on a platform."},
    "RedditAccount": {"parent": "slo:Account", "label": "Reddit account"},
    "ExternalAccount": {"parent": "slo:Account", "label": "External account",
                        "comment": "An account on a non-Reddit platform, collected or disclosed."},
    "Community": {"parent": "sioc:Forum", "label": "Community", "comment": "A subreddit, hashtag, group or channel."},
    "Subreddit": {"parent": "slo:Community", "label": "Subreddit"},
    "Content": {"parent": "sioc:Item", "label": "Content"},
    "Post": {"parent": "slo:Content", "label": "Post"},
    "Comment": {"parent": "slo:Content", "label": "Comment"},
    "Domain": {"parent": "owl:Thing", "label": "Web domain", "comment": "A website linked from content."},
    "CoordinatedNetwork": {"parent": "foaf:Group", "label": "Coordinated network",
                           "comment": "Accounts that repeatedly post the same links or texts within minutes of each other."},
    "Topic": {"parent": "owl:Thing", "label": "Watched topic", "comment": "Keywords, domains and communities being monitored."},
}

# key: (label, domain, range, inverse/symmetric note, color, line style, description)
RELATIONS: dict[str, dict] = {
    "activeIn": {"domain": "Account", "range": "Community", "label": "active in", "color": "#8a94a6", "style": "solid",
                 "desc": "Account posted or commented in the community (weight = items)."},
    "interactsWith": {"domain": "Account", "range": "Account", "label": "replies to", "color": "#2f7ed8", "style": "solid",
                      "desc": "Account replied to the other account's posts/comments (weight = replies)."},
    "amplifies": {"domain": "Account", "range": "Account", "label": "amplifies", "color": "#e8873a", "style": "solid",
                  "desc": "Account repeatedly commented within 30 minutes of the other account's posts."},
    "mentions": {"domain": "Account", "range": "Account", "label": "mentions", "color": "#9b59b6", "style": "dashed",
                 "desc": "Account wrote u/<name> of the other account."},
    "discloses": {"domain": "Account", "range": "ExternalAccount", "label": "discloses", "color": "#16a085", "style": "solid",
                  "desc": "Account links to a profile on another platform (bio = self-disclosure; posts = weaker)."},
    "linksTo": {"domain": "Account", "range": "Domain", "label": "links to", "color": "#b8a04a", "style": "dotted",
                "desc": "Account shared links to the web domain."},
    "crosspostedTo": {"domain": "Post", "range": "Community", "label": "crossposted to", "color": "#e8873a", "style": "dashed",
                      "desc": "Post was crossposted into another community."},
    "coSharesWith": {"domain": "Account", "range": "Account", "label": "co-shares with", "color": "#eb6834", "style": "dashed",
                     "symmetric": True,
                     "desc": "Both accounts posted the same links/texts within minutes of each other, repeatedly (weight = distinct items)."},
    "memberOfNetwork": {"domain": "Account", "range": "CoordinatedNetwork", "label": "member of network", "color": "#eb6834",
                        "style": "dotted", "desc": "Account belongs to a coordinated sharing network."},
    "matchesTopic": {"domain": "Post", "range": "Topic", "label": "matches topic", "color": "#8a94a6", "style": "dotted",
                     "desc": "Post matches a watched topic's keywords, domains or communities."},
    "possibleSameOperator": {"domain": "Account", "range": "Account", "label": "possible same operator", "color": "#d64545",
                             "style": "dashed", "symmetric": True,
                             "desc": "Behavioural similarity (style, timing, overlap) suggests one operator. A hypothesis, not proof."},
    "sameAs": {"domain": "Account", "range": "Account", "label": "same as (confirmed)", "color": "#c0392b", "style": "solid",
               "symmetric": True, "desc": "Analyst-confirmed identity link (owl:sameAs)."},
    "controlledBy": {"domain": "Account", "range": "Agent", "label": "controlled by", "color": "#d64545", "style": "dotted",
                     "desc": "Account belongs to an operator cluster."},
    "onPlatform": {"domain": "Account", "range": "Platform", "label": "on platform", "color": "#bdc3c7", "style": "dotted",
                   "desc": "Account lives on the platform."},
    "authored": {"domain": "Account", "range": "Content", "label": "authored", "color": "#566573", "style": "solid"},
    "postedIn": {"domain": "Post", "range": "Community", "label": "posted in", "color": "#8a94a6", "style": "solid"},
    "replyTo": {"domain": "Comment", "range": "Content", "label": "reply to", "color": "#2f7ed8", "style": "solid"},
}

NODE_STYLE = {
    "RedditAccount": {"color": "#ff6a3d", "shape": "ellipse"},
    "ExternalAccount": {"color": "#1da1f2", "shape": "ellipse"},
    "Subreddit": {"color": "#6c7a89", "shape": "round-rectangle"},
    "Community": {"color": "#6c7a89", "shape": "round-rectangle"},
    "Domain": {"color": "#b8a04a", "shape": "diamond"},
    "Agent": {"color": "#d64545", "shape": "star"},
    "Platform": {"color": "#95a5a6", "shape": "hexagon"},
    "Post": {"color": "#f5b041", "shape": "round-tag"},
    "Comment": {"color": "#aab7b8", "shape": "ellipse"},
    "CoordinatedNetwork": {"color": "#eb6834", "shape": "triangle"},
}


def schema_json() -> dict:
    return {"namespace": NS, "classes": CLASSES, "relations": RELATIONS, "nodeStyle": NODE_STYLE}


# ------------------------------------------------------------------------ RDF
def _iri(node_id: str) -> str:
    safe = re.sub(r"[^A-Za-z0-9_\-.]", "_", node_id.replace(":", "__"))
    return f"<{NS.rstrip('#')}/id/{safe}>"


def _lit(value) -> str:
    if isinstance(value, bool):
        return f'"{str(value).lower()}"^^xsd:boolean'
    if isinstance(value, int):
        return f'"{value}"^^xsd:integer'
    if isinstance(value, float):
        return f'"{value:.6g}"^^xsd:decimal'
    s = str(value).replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n").replace("\r", "")
    return f'"{s}"'


def ontology_turtle() -> str:
    lines = [f"@prefix {p}: <{u}> ." for p, u in PREFIXES.items()]
    lines += ["", "<https://w3id.org/social-link-ontology> a owl:Ontology ;",
              '  rdfs:label "Social Link Ontology" ;',
              '  rdfs:comment "Accounts, communities, content and cross-platform identity hypotheses." .', ""]
    for name, c in CLASSES.items():
        lines.append(f"slo:{name} a owl:Class ; rdfs:subClassOf {c['parent']} ; rdfs:label {_lit(c['label'])}"
                     + (f" ; rdfs:comment {_lit(c['comment'])}" if c.get("comment") else "") + " .")
    lines.append("")
    for name, r in RELATIONS.items():
        kind = "owl:ObjectProperty" + (", owl:SymmetricProperty" if r.get("symmetric") else "")
        lines.append(f"slo:{name} a {kind} ; rdfs:domain slo:{r['domain']} ; rdfs:range slo:{r['range']} ; "
                     f"rdfs:label {_lit(r['label'])}" + (f" ; rdfs:comment {_lit(r['desc'])}" if r.get("desc") else "") + " .")
    lines += ["", "slo:sameAs rdfs:subPropertyOf owl:sameAs .", "slo:weight a owl:DatatypeProperty .",
              'slo:botScore a owl:DatatypeProperty ; rdfs:domain slo:Account ; rdfs:comment "0..1 likelihood that the account is automated." .',
              "slo:botLabel a owl:DatatypeProperty ; rdfs:domain slo:Account .",
              "slo:confidence a owl:DatatypeProperty .", "slo:handle a owl:DatatypeProperty .", ""]
    return "\n".join(lines)


def graph_turtle(graph: dict) -> str:
    """Instance data for a graph produced by graph.build_graph(); relations with attributes are reified."""
    out = [f"@prefix {p}: <{u}> ." for p, u in PREFIXES.items()] + [""]
    for n in graph["nodes"]:
        d = n["data"]
        props = [f"a slo:{d['cls']}", f"rdfs:label {_lit(d['label'])}"]
        if d.get("platform"):
            props.append(f"slo:onPlatform {_iri('platform:' + d['platform'])}")
        for k in ("created_utc", "items", "karma", "confidence", "botScore", "botLabel"):
            if d.get(k) is not None:
                props.append(f"slo:{k} {_lit(d[k])}")
        out.append(f"{_iri(d['id'])} " + " ;\n    ".join(props) + " .")
    for p in sorted({n["data"]["platform"] for n in graph["nodes"] if n["data"].get("platform")}):
        out.append(f"{_iri('platform:' + p)} a slo:Platform ; rdfs:label {_lit(p)} .")
    out.append("")
    for i, e in enumerate(graph["edges"]):
        d = e["data"]
        s, t, rel = _iri(d["source"]), _iri(d["target"]), d["type"]
        out.append(f"{s} slo:{rel} {t} .")
        if d.get("weight") is not None or d.get("confidence") is not None:
            st = f"_:e{i}"
            out.append(f"{st} a rdf:Statement ; rdf:subject {s} ; rdf:predicate slo:{rel} ; rdf:object {t}"
                       + (f" ; slo:weight {_lit(d['weight'])}" if d.get("weight") is not None else "")
                       + (f" ; slo:confidence {_lit(d['confidence'])}" if d.get("confidence") is not None else "") + " .")
    return "\n".join(out) + "\n"


def graph_jsonld(graph: dict) -> str:
    ctx = {k: v for k, v in PREFIXES.items()}
    ctx.update({"label": "rdfs:label", "weight": "slo:weight", "confidence": "slo:confidence"})
    for rel in RELATIONS:
        ctx[rel] = {"@id": f"slo:{rel}", "@type": "@id"}
    nodes: dict[str, dict] = {}
    for n in graph["nodes"]:
        d = n["data"]
        nodes[d["id"]] = {"@id": f"{NS.rstrip('#')}/id/{d['id']}", "@type": f"slo:{d['cls']}", "label": d["label"],
                          **({"platform": d["platform"]} if d.get("platform") else {})}
    for e in graph["edges"]:
        d = e["data"]
        src = nodes.get(d["source"])
        if src is None:
            continue
        src.setdefault(d["type"], []).append(f"{NS.rstrip('#')}/id/{d['target']}")
    return json.dumps({"@context": ctx, "@graph": list(nodes.values())}, indent=2)
