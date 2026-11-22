"""Runtime configuration (environment variables or a local .env file)."""
from __future__ import annotations

import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent


def _load_dotenv(path: Path) -> None:
    if not path.exists():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


_load_dotenv(BASE_DIR / ".env")

DATA_DIR = Path(os.environ.get("SLA_DATA_DIR", BASE_DIR / "data"))
DB_PATH = Path(os.environ.get("SLA_DB_PATH", DATA_DIR / "social_graph.db"))

# Reddit API. With a client id/secret (a "script" or "web" app from
# https://www.reddit.com/prefs/apps) the collector uses OAuth (100 req/min).
# Without them it falls back to the public .json endpoints, which are heavily
# rate limited and may be blocked.
REDDIT_CLIENT_ID = os.environ.get("REDDIT_CLIENT_ID", "")
REDDIT_CLIENT_SECRET = os.environ.get("REDDIT_CLIENT_SECRET", "")
REDDIT_USER_AGENT = os.environ.get(
    "REDDIT_USER_AGENT", "python:social-link-analyzer:0.1 (account relationship research)"
)
