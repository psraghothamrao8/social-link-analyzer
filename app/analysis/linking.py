"""Multi-account (sock puppet) and cross-platform account linking.

Every account with enough activity gets a behavioural profile. For every pair of profiles we
compute independent signals, calibrate each against the baseline of all pairs in the dataset
(so "similar" means "unusually similar here"), and combine them into a 0..1 score:

  style_char   character 3-gram TF-IDF cosine (spelling, punctuation, casing, pet phrases)
  style_func   function-word usage profile (how someone writes, not what about)
  style_habit  habits: !!, emoji, double spaces, lowercase starts, contractions, sentence length
  temporal     hour-of-day / weekday activity fingerprint (UTC)
  community    overlap of communities (same platform only, rare communities count more)
  cothread     appearing in the same threads (same platform only)
  interaction  replying to / boosting each other (coordination signal)
  domains      sharing the same uncommon web domains
  username     name similarity (weak on its own)
  creation     accounts created close together
  disclosure   one account links to the other (or both link to the same external profile)

The result is a hypothesis with evidence, never proof: shared households, coworkers, bots running
the same template, or fans of the same creator can look alike.
"""
from __future__ import annotations

import math
import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone

import numpy as np

from ..collectors.crossplatform import COMMON_DOMAINS
from .corpus import Corpus

FUNCTION_WORDS = (
    "a about above after again against all am an and any are as at be because been before being below between both but by "
    "can could did do does doing down during each few for from further had has have having he her here hers herself him "
    "himself his how i if in into is it its itself just me more most my myself no nor not now of off on once only or other "
    "our ours out over own same she should so some such than that the their theirs them then there these they this those "
    "through to too under until up very was we were what when where which while who whom why will with would you your "
    "yours yourself actually also anyway basically really maybe pretty quite still though yet even ever never always"
).split()
FW_INDEX = {w: i for i, w in enumerate(FUNCTION_WORDS)}

WEIGHTS = {
    "style_char": 0.20, "style_lex": 0.08, "style_func": 0.06, "style_habit": 0.14, "temporal": 0.18,
    "community": 0.08, "cothread": 0.08, "interaction": 0.08, "domains": 0.05, "username": 0.03, "creation": 0.02,
}
NETWORK_FEATURES = ["community", "cothread", "interaction"]
LEVELS = [(0.75, "strong"), (0.60, "moderate"), (0.45, "weak")]
CLUSTER_THRESHOLD = 0.75
FEATURE_LABELS = {
    "style_char": "Writing style (topic-masked character patterns)", "style_lex": "Pet phrases & spellings",
    "style_func": "Function-word profile", "style_habit": "Typing habits",
    "temporal": "Active hours (UTC)", "community": "Shared communities", "cothread": "Same threads",
    "interaction": "Reply / boost each other", "domains": "Shared uncommon domains", "username": "Username similarity",
    "creation": "Created close together", "disclosure": "Cross-links / disclosed profile",
}

WORD_RE = re.compile(r"[A-Za-z']+")
SENT_SPLIT = re.compile(r"(?<=[.!?])\s+|\n+")
EMOJI_RE = re.compile("[\U0001F300-\U0001FAFF☀-➿]|(?<!\\w)(?:[:;]-?[)D(/P]|xD)(?!\\w)")
FILLER_RE = re.compile(r"\b(tbh|imo|imho|ngl|lol|lmao|idk|fwiw|haha|smh|afaik)\b", re.I)
NOAPOS_RE = re.compile(r"\b(dont|cant|wont|im|ive|didnt|doesnt|isnt|thats|youre|theyre|wouldnt|couldnt|shouldnt)\b", re.I)
HABIT_NAMES = [
    "exclamations / sentence", "'!!' per item", "questions / sentence", "'...' per item", "emoji per item",
    "lowercase sentence starts", "ALL-CAPS words", "double spaces / sentence", "contractions without apostrophe",
    "avg word length", "words / sentence", "commas / word", "fillers (tbh, imo, lol)", "items without final punctuation",
]


@dataclass
class Profile:
    aid: str
    platform: str
    username: str
    created: float | None
    n_items: int
    text: str
    n_chars: int
    n_words: int
    hours: np.ndarray
    weekdays: np.ndarray
    n_timed: int
    communities: Counter
    threads: set
    domains: set
    habits: np.ndarray
    fw: np.ndarray
    samples: list[str] = field(default_factory=list)


def _habits(texts: list[str]) -> tuple[np.ndarray, int]:
    joined = "\n".join(texts)
    sents = [s for s in SENT_SPLIT.split(joined) if s.strip()]
    words = WORD_RE.findall(joined)
    n_s, n_w, n_i = max(1, len(sents)), max(1, len(words)), max(1, len(texts))
    lower_starts = sum(1 for s in sents if s.strip()[:1].islower())
    caps = sum(1 for w in words if len(w) > 2 and w.isupper())
    no_final = sum(1 for t in texts if t.strip() and t.strip()[-1] not in ".!?)\"'")
    vec = np.array([
        joined.count("!") / n_s, joined.count("!!") / n_i, joined.count("?") / n_s, joined.count("...") / n_i,
        len(EMOJI_RE.findall(joined)) / n_i, lower_starts / n_s, caps / n_w, len(re.findall(r"[.!?]  \S", joined)) / n_s,
        len(NOAPOS_RE.findall(joined)) / n_w, sum(len(w) for w in words) / n_w, len(words) / n_s,
        joined.count(",") / n_w, len(FILLER_RE.findall(joined)) / n_w, no_final / n_i,
    ], dtype=np.float64)
    return vec, len(words)


def build_profiles(c: Corpus, min_items: int = 3) -> list[Profile]:
    profiles = []
    for aid, items in c.by_account.items():
        if len(items) < min_items:
            continue
        acc = c.accounts.get(aid, {})
        texts = [it.text for it in items if it.text]
        hours, weekdays = np.zeros(24), np.zeros(7)
        n_timed = 0
        for it in items:
            if it.created_utc:
                dt = datetime.fromtimestamp(it.created_utc, timezone.utc)
                hours[dt.hour] += 1
                weekdays[dt.weekday()] += 1
                n_timed += 1
        text = "\n".join(texts)[:40000]
        habits, n_words = _habits(texts)
        fw = np.zeros(len(FUNCTION_WORDS))
        for w in WORD_RE.findall(text.lower()):
            j = FW_INDEX.get(w)
            if j is not None:
                fw[j] += 1
        fw = fw / max(1, n_words) * 1000
        domains = set()
        for it in items:
            for d in c.domains(it):
                if d not in COMMON_DOMAINS and not d.endswith(("reddit.com", "redd.it")):
                    domains.add(d)
        profiles.append(Profile(
            aid=aid, platform=aid.split(":", 1)[0], username=acc.get("username") or aid.split(":", 1)[1],
            created=acc.get("created_utc"), n_items=len(items), text=text, n_chars=len(text), n_words=n_words,
            hours=hours, weekdays=weekdays, n_timed=n_timed,
            communities=Counter(it.community_id for it in items if it.community_id),
            threads={it.post_id for it in items}, domains=domains, habits=habits, fw=fw,
            samples=[t[:220] for t in texts[-3:]],
        ))
    return profiles


# --------------------------------------------------------------------- matrices
def _l2(m: np.ndarray) -> np.ndarray:
    n = np.linalg.norm(m, axis=1, keepdims=True)
    n[n == 0] = 1
    return m / n


FW_SET = set(FUNCTION_WORDS)


def distort(text: str) -> str:
    """Text distortion (Stamatatos 2013): mask content words so character n-grams measure style
    (punctuation, casing, spacing, word lengths, function words) instead of topic."""
    text = re.sub(r"https?://\S+", " URL ", text)

    def mask(m: re.Match) -> str:
        w = m.group(0)
        if w.lower() in FW_SET:
            return w
        return "".join("X" if ch.isupper() else ("x" if ch.isalpha() else ch) for ch in w)

    return re.sub(r"\d", "0", WORD_RE.sub(mask, text))


def _tfidf(docs: list[Counter], max_vocab: int) -> np.ndarray:
    n = len(docs)
    df: Counter = Counter()
    for cnt in docs:
        df.update(cnt.keys())
    terms = [g for g, d in df.most_common() if d >= 2] if n > 2 else list(df)
    vocab = {g: j for j, g in enumerate(terms[:max_vocab])}
    X = np.zeros((n, len(vocab)), dtype=np.float32)
    idf = np.zeros(len(vocab), dtype=np.float32)
    for g, j in vocab.items():
        idf[j] = math.log((1 + n) / (1 + df[g])) + 1
    for i, cnt in enumerate(docs):
        for g, k in cnt.items():
            j = vocab.get(g)
            if j is not None:
                X[i, j] = 1 + math.log(k)
    return _l2(X * idf)


def _char_matrix(profiles: list[Profile], max_vocab: int = 15000) -> np.ndarray:
    docs = []
    for p in profiles:
        t = distort(p.text)
        cnt = Counter(t[i:i + 3] for i in range(len(t) - 2))
        cnt.update(t[i:i + 4] for i in range(len(t) - 3))
        docs.append(cnt)
    return _tfidf(docs, max_vocab)


def _lex_matrix(profiles: list[Profile], max_vocab: int = 15000) -> np.ndarray:
    """Word unigrams + bigrams: pet phrases, misspellings, idiosyncratic vocabulary."""
    return _tfidf([Counter(_tokens(p.text)) for p in profiles], max_vocab)


def _calibrate(S: np.ndarray, applicable: np.ndarray, floor: float = 0.90, full: float = 0.995) -> np.ndarray:
    """Turn raw similarity into evidence in [0, 1] by its percentile among all applicable pairs.

    Only pairs more similar than `floor` of the baseline get any evidence; ties count as "not more
    similar", so a signal everyone shares (e.g. all regulars of a tiny sub share threads) earns nothing.
    """
    iu = np.triu_indices(S.shape[0], 1)
    vals = np.sort(S[iu][applicable[iu]])
    if len(vals) >= 30:
        pct = np.searchsorted(vals, S, side="left") / len(vals)
        ev = np.clip((pct - floor) / (full - floor), 0, 1)
    else:  # too few pairs for a baseline: fall back to absolute similarity
        ev = np.clip((S - 0.5) / 0.4, 0, 1)
    ev[~applicable] = np.nan
    return ev


def _bigrams(name: str) -> set:
    core = re.sub(r"[^a-z]", "", name.lower())
    return {core[i:i + 2] for i in range(len(core) - 1)}


@dataclass
class LinkModel:
    profiles: list[Profile]
    index: dict[str, int]
    raw: dict[str, np.ndarray]
    evidence: dict[str, np.ndarray]
    score: np.ndarray
    sufficiency: np.ndarray
    disclosure: dict[tuple[int, int], dict]
    interactions: dict[tuple[int, int], Counter]
    cothreads: dict[tuple[int, int], int]
    word_df: Counter
    manual: set


def build_model(c: Corpus, min_items: int = 3) -> LinkModel | None:
    profiles = build_profiles(c, min_items)
    n = len(profiles)
    if n < 2:
        return None
    idx = {p.aid: i for i, p in enumerate(profiles)}
    plat = np.array([p.platform for p in profiles])
    same_platform = plat[:, None] == plat[None, :]
    chars = np.array([p.n_chars for p in profiles])
    words = np.array([p.n_words for p in profiles])
    items = np.array([p.n_items for p in profiles])
    timed = np.array([p.n_timed for p in profiles])
    raw, ev = {}, {}

    # style
    X = _char_matrix(profiles)
    raw["style_char"] = X @ X.T
    ok = (chars[:, None] >= 300) & (chars[None, :] >= 300)
    ev["style_char"] = _calibrate(raw["style_char"], ok)

    L = _lex_matrix(profiles)
    raw["style_lex"] = L @ L.T
    ev["style_lex"] = _calibrate(raw["style_lex"], ok)

    F = np.array([p.fw for p in profiles])
    Fz = _l2((F - F.mean(0)) / (F.std(0) + 1e-9))
    raw["style_func"] = Fz @ Fz.T
    ok = (words[:, None] >= 120) & (words[None, :] >= 120)
    ev["style_func"] = _calibrate(raw["style_func"], ok)

    Hb = np.array([p.habits for p in profiles])
    Hz = (Hb - Hb.mean(0)) / (Hb.std(0) + 1e-9)
    D = np.sqrt(((Hz[:, None, :] - Hz[None, :, :]) ** 2).sum(-1))
    raw["style_habit"] = 1 / (1 + D / math.sqrt(Hz.shape[1]))
    ok = (items[:, None] >= 5) & (items[None, :] >= 5)
    ev["style_habit"] = _calibrate(raw["style_habit"], ok)

    # temporal
    H = np.array([p.hours for p in profiles])
    H = 0.5 * H + 0.25 * np.roll(H, 1, axis=1) + 0.25 * np.roll(H, -1, axis=1)
    W = np.array([p.weekdays for p in profiles])
    Hn, Wn = _l2(H), _l2(W)
    raw["temporal"] = 0.8 * (Hn @ Hn.T) + 0.2 * (Wn @ Wn.T)
    ok = (timed[:, None] >= 5) & (timed[None, :] >= 5)
    ev["temporal"] = _calibrate(raw["temporal"], ok)

    # communities (rare communities weigh more)
    comm_df: Counter = Counter()
    for p in profiles:
        comm_df.update(p.communities.keys())
    cvocab = {cid: j for j, cid in enumerate(comm_df)}
    C = np.zeros((n, len(cvocab)))
    for i, p in enumerate(profiles):
        for cid, k in p.communities.items():
            C[i, cvocab[cid]] = math.log1p(k) * (math.log((1 + n) / (1 + comm_df[cid])) + 1)
    C = _l2(C)
    raw["community"] = C @ C.T
    ok = same_platform & (C.sum(1)[:, None] > 0) & (C.sum(1)[None, :] > 0)
    ev["community"] = _calibrate(raw["community"], ok)

    # co-threads (inverted index; skip mega-threads)
    by_thread: dict[str, list[int]] = defaultdict(list)
    for i, p in enumerate(profiles):
        for t in p.threads:
            by_thread[t].append(i)
    co: Counter = Counter()
    for t, members in by_thread.items():
        if 1 < len(members) <= 300:
            for a in range(len(members)):
                for b in range(a + 1, len(members)):
                    co[(members[a], members[b])] += 1
    sizes = np.array([max(1, len(p.threads)) for p in profiles])
    raw["cothread"] = np.zeros((n, n))
    for (a, b), k in co.items():
        # Jaccard, so commenting on a prolific poster's threads (everyone does) is not mistaken for identity
        raw["cothread"][a, b] = raw["cothread"][b, a] = k / (sizes[a] + sizes[b] - k)
    ev["cothread"] = _calibrate(raw["cothread"], same_platform & (sizes[:, None] >= 3) & (sizes[None, :] >= 3))

    # interaction: replies between the two, and early comments on each other's posts
    inter: dict[tuple[int, int], Counter] = defaultdict(Counter)
    for it in c.items.values():
        if it.kind != "comment" or it.account_id not in idx:
            continue
        a = idx[it.account_id]
        target = c.author_of(it.parent_id)
        if target in idx and idx[target] != a:
            key = tuple(sorted((a, idx[target])))
            inter[key]["replies"] += 1
        post = c.posts.get(it.post_id)
        if post and post["account_id"] in idx and post["account_id"] != it.account_id and post["created_utc"] and it.created_utc:
            if 0 <= it.created_utc - post["created_utc"] <= 1800:
                inter[tuple(sorted((a, idx[post["account_id"]])))]["early_comments"] += 1
    raw["interaction"] = np.zeros((n, n))
    volume = np.zeros((n, n))
    for (a, b), cnt in inter.items():
        volume[a, b] = volume[b, a] = cnt["replies"] + 2 * cnt["early_comments"]
        # intensity relative to how active both are: a popular daily poster gets replies from everyone
        raw["interaction"][a, b] = raw["interaction"][b, a] = volume[a, b] / math.sqrt(items[a] * items[b])
    ev["interaction"] = _calibrate(raw["interaction"], same_platform)
    ev["interaction"][(volume < 3) & same_platform] = 0.0

    # shared uncommon domains (idf-weighted Jaccard)
    dom_df: Counter = Counter()
    for p in profiles:
        dom_df.update(p.domains)
    didf = {d: math.log((1 + n) / (1 + k)) + 1 for d, k in dom_df.items()}
    tot = np.array([sum(didf[d] for d in p.domains) for p in profiles])
    raw["domains"] = np.zeros((n, n))
    by_dom: dict[str, list[int]] = defaultdict(list)
    for i, p in enumerate(profiles):
        for d in p.domains:
            by_dom[d].append(i)
    inter_d: Counter = Counter()
    for d, members in by_dom.items():
        if len(members) <= 50:
            for a in range(len(members)):
                for b in range(a + 1, len(members)):
                    inter_d[(members[a], members[b])] += didf[d]
    for (a, b), w in inter_d.items():
        raw["domains"][a, b] = raw["domains"][b, a] = w / max(1e-9, tot[a] + tot[b] - w)
    ev["domains"] = np.where((tot[:, None] > 0) & (tot[None, :] > 0), np.clip(raw["domains"] / 0.5, 0, 1), np.nan)

    # username similarity (bigram Dice, order-insensitive)
    grams = [_bigrams(p.username) for p in profiles]
    U = np.zeros((n, n))
    for a in range(n):
        for b in range(a + 1, n):
            ga, gb = grams[a], grams[b]
            if ga and gb:
                U[a, b] = U[b, a] = 2 * len(ga & gb) / (len(ga) + len(gb))
    raw["username"] = U
    ev["username"] = np.clip((U - 0.5) / 0.35, 0, 1)

    # creation proximity
    cr = np.array([p.created if p.created else np.nan for p in profiles], dtype=float)
    days = np.abs(cr[:, None] - cr[None, :]) / 86400
    raw["creation"] = days
    ev["creation"] = np.select([days <= 3, days <= 14, days <= 45], [1.0, 0.6, 0.25], 0.0)
    ev["creation"][np.isnan(days)] = np.nan

    # combine: behavioural fingerprint (style, timing, metadata) decides; being in the same
    # communities/threads only amplifies it, because whole communities share those.
    def wmean(features: list[str]) -> tuple[np.ndarray, np.ndarray]:
        num, den = np.zeros((n, n)), np.zeros((n, n))
        for f in features:
            e = ev[f]
            app = ~np.isnan(e)
            num += np.where(app, e, 0) * WEIGHTS[f]
            den += app * WEIGHTS[f]
        return np.divide(num, den, out=np.zeros_like(num), where=den > 0), den > 0

    identity, _ = wmean([f for f in WEIGHTS if f not in NETWORK_FEATURES])
    network, has_network = wmean(NETWORK_FEATURES)
    score = identity * np.where(has_network, 0.8 + 0.2 * network, 0.9)

    # explicit cross-links
    ext = defaultdict(dict)  # profile idx -> {(platform, handle): source}
    for l in c.external_links:
        if l["account_id"] in idx:
            key = (l["platform"], l["handle"])
            prev = ext[idx[l["account_id"]]].get(key)
            ext[idx[l["account_id"]]][key] = "bio" if "bio" in (prev, l["source"]) else l["source"]
    disclosure: dict[tuple[int, int], dict] = {}
    for a, links in ext.items():
        for (plat_, handle), src in links.items():
            b = idx.get(f"{plat_}:{handle}")
            if b is not None and b != a:
                key = tuple(sorted((a, b)))
                strength = 1.0 if src == "bio" else 0.7
                if strength > disclosure.get(key, {}).get("strength", 0):
                    disclosure[key] = {"strength": strength, "kind": "direct", "detail": f"{profiles[a].aid} links to {plat_}:{handle} ({src})"}
    for a in ext:
        for b in ext:
            if a >= b:
                continue
            shared = set(ext[a]) & set(ext[b])
            for key in shared:
                strength = 0.8 if ext[a][key] == "bio" and ext[b][key] == "bio" else 0.5
                if strength > disclosure.get((a, b), {}).get("strength", 0):
                    disclosure[(a, b)] = {"strength": strength, "kind": "shared",
                                          "detail": f"both link to {key[0]}:{key[1]} ({ext[a][key]} / {ext[b][key]})"}
    for (a, b), d in disclosure.items():
        bonus = 0.7 * d["strength"]
        score[a, b] = score[b, a] = score[a, b] + (1 - score[a, b]) * bonus

    # less data -> pull scores down
    suff = np.minimum(1.0, np.minimum(items[:, None], items[None, :]) / 15)
    score = score * (0.55 + 0.45 * suff)
    manual = {tuple(sorted((m["a"], m["b"]))) for m in c.manual_links if m["relation"] == "sameAs"}
    for a_id, b_id in manual:
        if a_id in idx and b_id in idx:
            score[idx[a_id], idx[b_id]] = score[idx[b_id], idx[a_id]] = 1.0
    np.fill_diagonal(score, 0)

    word_df: Counter = Counter()
    for p in profiles:
        word_df.update(set(_tokens(p.text)))
    return LinkModel(profiles, idx, raw, ev, score, suff, disclosure, inter, dict(co), word_df, manual)


def _tokens(text: str) -> list[str]:
    ws = [w.lower().strip("'") for w in WORD_RE.findall(re.sub(r"https?://\S+", " ", text))]
    ws = [w for w in ws if len(w) > 1]
    return ws + [f"{a} {b}" for a, b in zip(ws, ws[1:])]


def _level(s: float) -> str | None:
    for t, name in LEVELS:
        if s >= t:
            return name
    return None


def _features(m: LinkModel, a: int, b: int) -> dict:
    out = {}
    for f, w in WEIGHTS.items():
        e = m.evidence[f][a, b]
        out[f] = {"label": FEATURE_LABELS[f], "weight": w, "applicable": not math.isnan(e),
                  "evidence": None if math.isnan(e) else round(float(e), 3), "raw": round(float(m.raw[f][a, b]), 3)}
    d = m.disclosure.get(tuple(sorted((a, b))))
    out["disclosure"] = {"label": FEATURE_LABELS["disclosure"], "weight": "bonus", "applicable": d is not None,
                         "evidence": d["strength"] if d else None, "raw": d["detail"] if d else None}
    return out


def _pair(m: LinkModel, a: int, b: int, with_features: bool = True) -> dict:
    pa, pb = m.profiles[a], m.profiles[b]
    s = float(m.score[a, b])
    out = {"a": pa.aid, "b": pb.aid, "score": round(s, 3), "level": _level(s),
           "crossPlatform": pa.platform != pb.platform, "dataSufficiency": round(float(m.sufficiency[a, b]), 2),
           "confirmed": tuple(sorted((pa.aid, pb.aid))) in m.manual}
    if with_features:
        feats = _features(m, a, b)
        out["features"] = feats
        top = sorted(((k, v) for k, v in feats.items() if v["evidence"]), key=lambda kv: -kv[1]["evidence"] * (kv[1]["weight"] if isinstance(kv[1]["weight"], float) else 0.3))
        out["topSignals"] = [v["label"] for k, v in top[:3]]
    return out


def scan(m: LinkModel | None, min_score: float = 0.45, cross_platform_only: bool = False, limit: int = 300) -> dict:
    if m is None:
        return {"pairs": [], "clusters": [], "profiles": 0}
    n = len(m.profiles)
    iu = np.triu_indices(n, 1)
    vals = m.score[iu]
    order = np.argsort(-vals)
    pairs = []
    for k in order:
        if vals[k] < min_score or len(pairs) >= limit:
            break
        a, b = int(iu[0][k]), int(iu[1][k])
        if cross_platform_only and m.profiles[a].platform == m.profiles[b].platform:
            continue
        pairs.append(_pair(m, a, b))
    return {"pairs": pairs, "clusters": clusters(m), "profiles": n, "pairsCompared": int(len(vals))}


def clusters(m: LinkModel | None, threshold: float = CLUSTER_THRESHOLD) -> list[dict]:
    """Group accounts whose pairwise scores pass the threshold (union-find) into operator hypotheses."""
    if m is None:
        return []
    n = len(m.profiles)
    parent = list(range(n))

    def find(x: int) -> int:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    a_idx, b_idx = np.where(np.triu(m.score, 1) >= threshold)
    for a, b in zip(a_idx, b_idx):
        parent[find(int(a))] = find(int(b))
    groups: dict[int, list[int]] = defaultdict(list)
    for i in range(n):
        groups[find(i)].append(i)
    out = []
    for members in groups.values():
        if len(members) < 2:
            continue
        sub = m.score[np.ix_(members, members)]
        iu = np.triu_indices(len(members), 1)
        mean = float(sub[iu].mean())
        members.sort(key=lambda i: -m.profiles[i].n_items)
        out.append({"members": [m.profiles[i].aid for i in members], "score": round(mean, 3),
                    "platforms": sorted({m.profiles[i].platform for i in members})})
    out.sort(key=lambda c: (-len(c["members"]), -c["score"]))
    for i, cl in enumerate(out, 1):
        cl["id"] = f"agent:{i}"
        cl["label"] = f"Operator #{i} ({len(cl['members'])} accounts)"
    return out


def similar_to(m: LinkModel | None, aid: str, top: int = 15) -> dict:
    if m is None or aid not in m.index:
        return {"account": aid, "matches": [], "message": "Not enough activity collected for this account (needs 3+ posts/comments)."}
    a = m.index[aid]
    order = np.argsort(-m.score[a])
    matches = [_pair(m, a, int(b)) for b in order[:top] if int(b) != a]
    return {"account": aid, "matches": matches}


def pair_detail(c: Corpus, m: LinkModel | None, a_id: str, b_id: str) -> dict:
    if m is None or a_id not in m.index or b_id not in m.index:
        return {"error": "Both accounts need 3+ collected posts/comments."}
    a, b = m.index[a_id], m.index[b_id]
    pa, pb = m.profiles[a], m.profiles[b]
    out = _pair(m, a, b)
    n = len(m.profiles)
    # distinctive vocabulary both use (rare across the dataset)
    ta, tb = Counter(_tokens(pa.text)), Counter(_tokens(pb.text))
    shared = []
    for tok in set(ta) & set(tb):
        df = m.word_df.get(tok, 1)
        if df <= max(3, int(0.12 * n)):
            shared.append((tok, ta[tok], tb[tok], df))
    shared.sort(key=lambda x: (x[3], -min(x[1], x[2])))
    out["sharedPhrases"] = [{"text": t, "countA": ca, "countB": cb, "accountsUsing": df} for t, ca, cb, df in shared[:20]]
    out["habits"] = [{"name": nm, "a": round(float(pa.habits[i]), 3), "b": round(float(pb.habits[i]), 3)} for i, nm in enumerate(HABIT_NAMES)]
    out["hours"] = {"a": pa.hours.astype(int).tolist(), "b": pb.hours.astype(int).tolist()}
    out["weekdays"] = {"a": pa.weekdays.astype(int).tolist(), "b": pb.weekdays.astype(int).tolist()}
    comms = set(pa.communities) & set(pb.communities)
    out["sharedCommunities"] = sorted(
        [{"id": cid, "label": c.community_label(cid), "a": pa.communities[cid], "b": pb.communities[cid]} for cid in comms],
        key=lambda x: -(x["a"] + x["b"]))
    threads = pa.threads & pb.threads
    out["sharedThreads"] = [{"id": t, "title": (c.posts.get(t) or {}).get("title") or t,
                             "community": c.community_label((c.posts.get(t) or {}).get("community_id"))} for t in list(threads)[:12]]
    out["sharedThreadCount"] = len(threads)
    ex = []
    for it in c.by_account.get(a_id, []) + c.by_account.get(b_id, []):
        if it.kind == "comment":
            target = c.author_of(it.parent_id)
            if {it.account_id, target} == {a_id, b_id}:
                ex.append({"id": it.id, "from": c.account_label(it.account_id), "to": c.account_label(target),
                           "created_utc": it.created_utc, "text": it.body[:200]})
    out["interactions"] = {"counts": dict(m.interactions.get(tuple(sorted((a, b))), {})), "examples": sorted(ex, key=lambda e: e["created_utc"] or 0)[:8]}
    out["sharedDomains"] = sorted(pa.domains & pb.domains)
    out["created"] = {"a": pa.created, "b": pb.created}
    out["samples"] = {"a": pa.samples, "b": pb.samples}
    out["items"] = {"a": pa.n_items, "b": pb.n_items}
    out["labels"] = {"a": c.account_label(a_id), "b": c.account_label(b_id)}
    return out
