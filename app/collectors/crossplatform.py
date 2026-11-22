"""Extract references to accounts on other social networks, plus Reddit user mentions and web domains.

Only public, self-published text is used (profile bio, the account's own posts and comments).
A link in the bio is treated as a self-disclosure; links inside posts/comments are weaker
(a user may be pointing at someone else's profile), so the source is kept with every hit.
"""
from __future__ import annotations

import re
from urllib.parse import urlparse

# platform -> regex with one capture group for the handle
PROFILE_PATTERNS: list[tuple[str, re.Pattern]] = [
    ("twitter", re.compile(r"(?:https?://)?(?:www\.|mobile\.)?(?:twitter|x)\.com/@?([A-Za-z0-9_]{1,15})(?![A-Za-z0-9_])", re.I)),
    ("instagram", re.compile(r"(?:https?://)?(?:www\.)?instagram\.com/([A-Za-z0-9_.]{1,30})", re.I)),
    ("youtube", re.compile(r"(?:https?://)?(?:www\.|m\.)?youtube\.com/(?:@|c/|user/)([A-Za-z0-9_.-]{2,60})", re.I)),
    ("tiktok", re.compile(r"(?:https?://)?(?:www\.)?tiktok\.com/@([A-Za-z0-9_.]{2,24})", re.I)),
    ("github", re.compile(r"(?:https?://)?(?:www\.)?github\.com/([A-Za-z0-9-]{1,39})(?![A-Za-z0-9-])", re.I)),
    ("facebook", re.compile(r"(?:https?://)?(?:www\.|m\.)?facebook\.com/([A-Za-z0-9.]{3,50})", re.I)),
    ("linkedin", re.compile(r"(?:https?://)?(?:[a-z]{2,3}\.)?linkedin\.com/in/([A-Za-z0-9_-]{3,100})", re.I)),
    ("telegram", re.compile(r"(?:https?://)?(?:t\.me|telegram\.me)/([A-Za-z0-9_]{5,32})", re.I)),
    ("twitch", re.compile(r"(?:https?://)?(?:www\.)?twitch\.tv/([A-Za-z0-9_]{3,25})", re.I)),
    ("medium", re.compile(r"(?:https?://)?medium\.com/@([A-Za-z0-9_.]{1,30})", re.I)),
    ("substack", re.compile(r"(?:https?://)?([A-Za-z0-9-]{3,40})\.substack\.com", re.I)),
    ("mastodon", re.compile(r"(?:https?://)?(?:mastodon\.social|fosstodon\.org|mstdn\.social)/@([A-Za-z0-9_]{1,30})", re.I)),
    ("bluesky", re.compile(r"(?:https?://)?bsky\.app/profile/([A-Za-z0-9_.-]{3,60})", re.I)),
    ("discord", re.compile(r"(?:https?://)?(?:discord\.gg|discord\.com/invite)/([A-Za-z0-9-]{2,32})", re.I)),
    # lets profiles imported from other platforms point back at a Reddit account
    ("reddit", re.compile(r"(?:https?://)?(?:www\.|old\.)?reddit\.com/u(?:ser)?/([A-Za-z0-9_-]{3,20})", re.I)),
]

# "my insta: @foo", "twitter @foo", "ig - @foo"
TEXT_HANDLE = re.compile(
    r"\b(twitter|x|insta(?:gram)?|ig|tiktok|tt|twitch|telegram|tg|threads)\s*(?:is|:|-|at)?\s*@([A-Za-z0-9_.]{2,30})",
    re.I,
)
TEXT_HANDLE_PLATFORM = {
    "twitter": "twitter", "x": "twitter", "insta": "instagram", "instagram": "instagram", "ig": "instagram",
    "tiktok": "tiktok", "tt": "tiktok", "twitch": "twitch", "telegram": "telegram", "tg": "telegram",
    "threads": "threads",
}

# Path segments that are site pages, not user handles.
RESERVED = {
    "twitter": {"i", "home", "intent", "share", "search", "hashtag", "explore", "settings", "login", "messages", "notifications", "status"},
    "instagram": {"p", "reel", "reels", "explore", "stories", "accounts", "tv", "direct"},
    "youtube": {"watch", "results", "playlist", "shorts", "feed", "channel"},
    "github": {"orgs", "topics", "features", "about", "pricing", "login", "marketplace", "sponsors", "settings", "search", "explore", "collections", "trending"},
    "facebook": {"groups", "events", "pages", "watch", "share", "sharer", "photo", "photo.php", "story.php", "profile.php", "permalink.php", "marketplace", "login"},
    "telegram": {"joinchat", "share", "addstickers", "proxy", "s"},
    "twitch": {"videos", "directory", "settings", "p"},
}

REDDIT_USER_MENTION = re.compile(r"(?<![A-Za-z0-9_/])/?u/([A-Za-z0-9_-]{3,20})")
SUBREDDIT_MENTION = re.compile(r"(?<![A-Za-z0-9_/])/?r/([A-Za-z0-9_]{2,21})")
URL_RE = re.compile(r"https?://[^\s)\]>\"']+", re.I)

# Domains so common they say nothing about who someone is.
COMMON_DOMAINS = {
    "reddit.com", "redd.it", "i.redd.it", "v.redd.it", "preview.redd.it", "imgur.com", "i.imgur.com",
    "youtube.com", "youtu.be", "google.com", "wikipedia.org", "en.wikipedia.org", "twitter.com", "x.com",
    "amazon.com", "github.com", "instagram.com", "facebook.com", "tiktok.com", "gfycat.com", "giphy.com",
}


def extract_profiles(text: str | None) -> list[tuple[str, str, str]]:
    """Return (platform, handle, url_or_snippet) for every external profile reference in text."""
    if not text:
        return []
    found: dict[tuple[str, str], str] = {}
    for platform, pattern in PROFILE_PATTERNS:
        for m in pattern.finditer(text):
            handle = m.group(1).strip(".").lower()
            if not handle or handle in RESERVED.get(platform, set()):
                continue
            found.setdefault((platform, handle), m.group(0))
    for m in TEXT_HANDLE.finditer(text):
        platform = TEXT_HANDLE_PLATFORM.get(m.group(1).lower())
        handle = m.group(2).strip(".").lower()
        if platform and handle:
            found.setdefault((platform, handle), m.group(0))
    return [(p, h, u) for (p, h), u in found.items()]


def extract_user_mentions(text: str | None) -> list[str]:
    if not text:
        return []
    return sorted({m.group(1).lower() for m in REDDIT_USER_MENTION.finditer(text)})


def extract_subreddit_mentions(text: str | None) -> list[str]:
    if not text:
        return []
    return sorted({m.group(1).lower() for m in SUBREDDIT_MENTION.finditer(text)})


def domain_of(url: str | None) -> str | None:
    if not url:
        return None
    try:
        host = urlparse(url if "://" in url else "http://" + url).hostname or ""
    except ValueError:
        return None
    host = host.lower()
    if host.startswith("www."):
        host = host[4:]
    return host or None


def extract_domains(text: str | None) -> list[str]:
    if not text:
        return []
    out = []
    for m in URL_RE.finditer(text):
        d = domain_of(m.group(0))
        if d:
            out.append(d)
    return out


def links_for_account(account_id: str, bio: str | None, items: list[tuple[str, str, str | None]]) -> list[dict]:
    """Build external_links rows. items = [(evidence_id, 'post'|'comment', text)]."""
    rows = []
    for platform, handle, snippet in extract_profiles(bio):
        rows.append({"account_id": account_id, "platform": platform, "handle": handle, "url": snippet, "source": "bio", "evidence_id": None})
    for evidence_id, kind, text in items:
        for platform, handle, snippet in extract_profiles(text):
            rows.append({"account_id": account_id, "platform": platform, "handle": handle, "url": snippet, "source": kind, "evidence_id": evidence_id})
    return rows
