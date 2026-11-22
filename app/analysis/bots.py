"""Bot likelihood: how automated an account's behaviour looks, with the reasons.

Every signal is behavioural or account metadata; nothing looks at the opinions an account holds.
Signals are combined with a noisy-OR: each can push the score up to its own cap, several weak
signals add up, and one strong signal alone cannot reach "likely automated".

"Automated" is not the same as "malicious": announced utility bots, schedulers and auto-posters
score high too. Coordinated human-run accounts (sock puppets, paid boosters) usually score low here
and are caught by the Multi-account and Coordination analyses instead.
"""
from __future__ import annotations

import re
import statistics
from collections import Counter
from datetime import datetime, timezone

from ..collectors.crossplatform import URL_RE, domain_of
from .corpus import Corpus, Item

DAY = 86400
# Reddit's auto-generated names: Adjective_Noun_1234 / Adjective-Noun-1234
DEFAULT_NAME = re.compile(r"^[A-Z][a-z]+[-_][A-Z][a-z]+[-_]?\d{1,4}$")
INTERNAL = ("reddit.com", "redd.it")

# key: (label, cap)  - cap is the most this signal alone can contribute
SIGNALS = {
    "no_sleep": ("Active around the clock (no daily rest period)", 0.55),
    "clockwork": ("Clockwork timing (fixed intervals or same minute every day)", 0.50),
    "fast_replies": ("Replies faster than a person can read and type", 0.60),
    "repetition": ("Repeats the same or near-identical text", 0.50),
    "link_share": ("Mostly posts links, concentrated on few sites", 0.35),
    "volume": ("Inhuman volume (items per day or per hour)", 0.45),
    "young_burst": ("Brand-new account with heavy activity", 0.30),
    "dormant_burst": ("Old account that suddenly became active", 0.35),
    "new_low_karma": ("New account with almost no karma", 0.25),
    "default_username": ("Reddit-generated default username", 0.12),
    "unverified_email": ("No verified email (Reddit flag)", 0.12),
    "low_engagement": ("Content gets no engagement (score ~1)", 0.15),
    "generic_short": ("Mostly very short, generic comments", 0.15),
    "suspended": ("Suspended by the platform", 0.35),
}
LEVELS = [(0.70, "likely automated"), (0.40, "suspicious")]


def _clip(x: float) -> float:
    return max(0.0, min(1.0, x))


def _norm_text(t: str) -> str:
    t = URL_RE.sub(" ", t.lower())
    t = re.sub(r"[^a-z\u0080-￿ ]+", " ", t)
    return re.sub(r"\s+", " ", t).strip()


def _shingles(t: str) -> set:
    w = t.split()
    return {" ".join(w[i:i + 3]) for i in range(max(1, len(w) - 2))}


def _links(it: Item) -> list[str]:
    out = []
    if it.url:
        d = domain_of(it.url)
        if d and not d.endswith(INTERNAL):
            out.append(d)
    for m in URL_RE.finditer(it.body or ""):
        d = domain_of(m.group(0))
        if d and not d.endswith(INTERNAL):
            out.append(d)
    return out


def account_signals(c: Corpus, aid: str) -> dict:
    items = [i for i in c.by_account.get(aid, []) if i.created_utc]
    acc = c.accounts.get(aid, {})
    n = len(items)
    sig: dict[str, dict] = {}

    def put(key: str, ev: float | None, detail: str) -> None:
        sig[key] = {"key": key, "label": SIGNALS[key][0], "cap": SIGNALS[key][1],
                    "evidence": None if ev is None else round(_clip(ev), 3), "detail": detail}

    ts = [i.created_utc for i in items]
    # --- rest period: humans leave a quiet stretch of several hours every day
    if n >= 40:
        hours = Counter(datetime.fromtimestamp(t, timezone.utc).hour for t in ts)
        empty = [hours.get(h, 0) == 0 for h in range(24)]
        run = best = 0
        for v in empty + empty:  # circular
            run = run + 1 if v else 0
            best = max(best, min(run, 24))
        put("no_sleep", (5 - best) / 4, f"active in {24 - sum(empty)} of 24 hours; longest quiet stretch {best} h (people usually rest 6+ h)")
    else:
        put("no_sleep", None, "needs 40+ timed items")

    # --- clockwork timing
    if n >= 8:
        gaps = [b - a for a, b in zip(ts, ts[1:]) if b > a]
        cv = statistics.pstdev(gaps) / statistics.mean(gaps) if len(gaps) >= 3 and statistics.mean(gaps) > 0 else 9
        posts = [i.created_utc for i in items if i.kind == "post"]
        share = 0.0
        if len(posts) >= 7:
            bins = Counter(int((t % DAY) // 1200) for t in posts)  # 20-minute slots of the day
            share = max(bins.values()) / len(posts)
        ev = max(_clip((0.6 - cv) / 0.5), _clip((share - 0.4) / 0.4))
        put("clockwork", ev, f"interval variation (CV) {cv:.2f}" + (f"; {share:.0%} of posts in the same 20-minute slot of the day" if posts and len(posts) >= 7 else ""))
    else:
        put("clockwork", None, "needs 8+ items")

    # --- reply latency vs. the time needed to read the parent and type the reply
    fast = total = 0
    for it in items:
        if it.kind != "comment" or not it.parent_id:
            continue
        parent = c.items.get(it.parent_id)
        if not parent or not parent.created_utc:
            continue
        delay = it.created_utc - parent.created_utc
        if delay < 0:
            continue
        total += 1
        needed = 20 + len(parent.text) / 25 + len(it.text) / 10
        fast += delay < needed
    if total >= 5:
        put("fast_replies", (fast / total - 0.1) / 0.4, f"{fast} of {total} replies came faster than reading + typing time")
    else:
        put("fast_replies", None, "needs 5+ replies with known parent times")

    # --- repetition
    texts = [_norm_text(i.text) for i in items[-150:]]
    texts = [t for t in texts if len(t.split()) >= 4]
    if len(texts) >= 5:
        sh = [_shingles(t) for t in texts]
        dup = set()
        for a in range(len(texts)):
            for b in range(a + 1, len(texts)):
                if texts[a] == texts[b] or (sh[a] and sh[b] and len(sh[a] & sh[b]) / len(sh[a] | sh[b]) >= 0.7):
                    dup.update((a, b))
        share = len(dup) / len(texts)
        put("repetition", (share - 0.15) / 0.45, f"{share:.0%} of texts duplicate or nearly duplicate another of its own texts")
    else:
        put("repetition", None, "needs 5+ texts of 4+ words")

    # --- link sharing
    if n >= 5:
        links = [_links(i) for i in items]
        with_link = sum(1 for l in links if l)
        doms = Counter(d for l in links for d in set(l))
        ratio = with_link / n
        top = doms.most_common(1)[0] if doms else (None, 0)
        top_share = top[1] / with_link if with_link else 0
        put("link_share", _clip((ratio - 0.4) / 0.4) * (0.5 + 0.5 * top_share),
            f"{ratio:.0%} of items carry external links" + (f"; {top_share:.0%} of them to {top[0]}" if top[0] else ""))
    else:
        put("link_share", None, "needs 5+ items")

    # --- volume
    if n >= 10:
        span = max(1.0, (ts[-1] - ts[0]) / DAY)
        per_day = n / span
        per_hour = max(Counter(int(t // 3600) for t in ts).values())
        put("volume", max(_clip((per_day - 15) / 35), _clip((per_hour - 12) / 18)),
            f"{per_day:.1f} items/day on average; up to {per_hour} in one hour")
    else:
        put("volume", None, "needs 10+ items")

    created = acc.get("created_utc")
    karma = (acc.get("link_karma") or 0) + (acc.get("comment_karma") or 0) if acc.get("link_karma") is not None else None
    if created and n:
        age = (ts[-1] - created) / DAY
        per_day = n / max(1.0, (ts[-1] - max(created, ts[0])) / DAY)
        ev = 1.0 if (age < 30 and n >= 15) else 0.6 if (age < 90 and per_day >= 5) else 0.0
        put("young_burst", ev, f"account {age:.0f} days old at its last item, {n} items collected")
        dormant = (ts[0] - created) / DAY
        span = (ts[-1] - ts[0]) / DAY
        ev = 1.0 if (dormant > 365 and span < 21 and 10 <= n < 900) else 0.5 if (dormant > 180 and span < 21 and 10 <= n < 900) else 0.0
        put("dormant_burst", ev, f"{dormant:.0f} days between creation and first collected item; activity spans {span:.0f} days")
    else:
        put("young_burst", None, "creation date unknown")
        put("dormant_burst", None, "creation date unknown")
    if created and karma is not None:
        age_now = ((ts[-1] if ts else created) - created) / DAY
        put("new_low_karma", 1.0 if (age_now < 30 and karma < 50) else 0.0, f"{age_now:.0f} days old, {karma} karma")
    else:
        put("new_low_karma", None, "age or karma unknown")

    name = acc.get("username") or aid.split(":", 1)[1]
    put("default_username", 1.0 if (aid.startswith("reddit:") and DEFAULT_NAME.match(name)) else 0.0,
        "matches Reddit's Adjective_Noun_1234 pattern (many real users keep it)" if DEFAULT_NAME.match(name) else "custom username")
    ve = acc.get("verified_email")
    put("unverified_email", None if ve is None else (0.0 if ve else 1.0), "unknown" if ve is None else ("verified" if ve else "not verified"))
    scores = [i.score for i in items if i.score is not None]
    if len(scores) >= 10:
        avg = statistics.mean(scores)
        put("low_engagement", (1.5 - avg) / 1.0, f"average score {avg:.1f}")
    else:
        put("low_engagement", None, "needs 10+ scored items")
    comments = [i for i in items if i.kind == "comment"]
    if len(comments) >= 5:
        short = sum(1 for i in comments if len(i.body.split()) <= 6) / len(comments)
        put("generic_short", (short - 0.5) / 0.4, f"{short:.0%} of comments are 6 words or fewer")
    else:
        put("generic_short", None, "needs 5+ comments")
    put("suspended", 1.0 if acc.get("status") == "suspended" else 0.0, "suspended" if acc.get("status") == "suspended" else "not suspended")
    return {"items": n, "signals": sig}


def score_account(c: Corpus, aid: str) -> dict:
    s = account_signals(c, aid)
    keep = 1.0
    for v in s["signals"].values():
        if v["evidence"]:
            keep *= 1 - v["cap"] * v["evidence"]
    score = round(1 - keep, 3)
    n = s["items"]
    label = next((name for t, name in LEVELS if score >= t), None)
    if label is None:
        label = "insufficient data" if n < 5 else "likely human"
    reasons = sorted((v for v in s["signals"].values() if v["evidence"]), key=lambda v: -v["cap"] * v["evidence"])
    return {"account": aid, "score": score, "label": label, "items": n, "confidence": round(min(1.0, n / 50), 2),
            "reasons": [r["label"] for r in reasons[:3]], "signals": list(s["signals"].values())}


def score_all(c: Corpus) -> dict[str, dict]:
    out = {}
    for aid in c.accounts:
        r = score_account(c, aid)
        r.pop("signals")
        out[aid] = r
    return out


def summary(scores: dict[str, dict], c: Corpus, min_score: float = 0.0, label: str | None = None, limit: int = 500) -> dict:
    rows = [r for r in scores.values() if r["score"] >= min_score and (label is None or r["label"] == label) and r["items"] > 0]
    rows.sort(key=lambda r: (-r["score"], -r["items"]))
    counts = Counter(r["label"] for r in scores.values() if r["items"] > 0)
    for r in rows:
        r["labelText"] = c.account_label(r["account"])
    return {"accounts": rows[:limit], "counts": dict(counts)}
