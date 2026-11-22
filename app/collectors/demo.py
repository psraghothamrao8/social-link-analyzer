"""Synthetic demo dataset (all names, accounts and content are invented).

It plants patterns every analysis should find:
  * a sock-puppet ring (gadget_guru_88, TechSavvyMike_, honest_reviewer_22) sharing a writing
    style, active hours and creation window, plus a matching Twitter account and a YouTube
    channel they disclose;
  * a boosted post (ZetaPhone X deal) whose early comments come from the ring and from
    brand-new low-karma accounts, crossposted into other subs;
  * regular posters (a scheduled market-wrap account, a daily cook, a gamer) active in
    several other subs;
  * ~40 ordinary users with their own styles and time zones as a baseline.
The output uses the generic import format (see importer.py), so it doubles as a format example.
"""
from __future__ import annotations

import math
import random
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone

END = datetime(2026, 11, 15, tzinfo=timezone.utc).timestamp()
DAY = 86400.0
SPAN_DAYS = 60
START = END - SPAN_DAYS * DAY

SUBS = {
    "techdeals": 410_000, "buildapc": 6_900_000, "gadgets": 21_000_000, "android": 4_100_000,
    "personalfinance": 20_000_000, "investing": 2_800_000, "cryptomoonshots": 2_200_000,
    "cooking": 3_900_000, "mealprep": 1_200_000, "fitness": 12_000_000, "running": 3_300_000,
    "askreddit": 48_000_000, "news": 29_000_000, "gaming": 44_000_000,
}
CLUSTERS = {
    "tech": ["techdeals", "buildapc", "gadgets", "android"],
    "finance": ["personalfinance", "investing", "cryptomoonshots"],
    "life": ["cooking", "mealprep", "fitness", "running"],
    "general": ["askreddit", "news", "gaming"],
}

TOPICS: dict[str, dict[str, list[str]]] = {
    "tech": {
        "things": ["this monitor deal", "the SSD sale", "that headset", "the 4K TV discount", "this laptop bundle",
                   "the router deal", "that mechanical keyboard", "the new GPU", "this power bank", "that webcam"],
        "claims": ["is a solid price", "has been cheaper before", "is worth it if you need one", "looks like a price trap",
                   "beats last month's sale", "runs hotter than people say", "is fine for the money", "is overkill for most people"],
        "details": ["check the warranty before you buy", "the coupon only works in the app", "shipping took forever last time",
                    "the price tracker shows it hit lower in spring", "returns are easy at that store", "the firmware update fixed the bugs",
                    "make sure your case has the clearance", "the 1TB version is the sweet spot"],
        "titles": ["{thing} for under ${n}", "Is {thing} worth it?", "PSA: {thing} is back in stock", "Thoughts on {thing}?",
                   "Upgraded my setup with {thing}", "Price drop on {thing}"],
    },
    "finance": {
        "things": ["an index fund", "the emergency fund", "your Roth IRA", "high yield savings", "that dividend stock",
                   "the bond ladder", "dollar cost averaging", "this altcoin", "the 401k match"],
        "claims": ["is the boring but right answer", "should come first", "is not a get rich quick plan", "makes sense at your age",
                   "is riskier than it looks", "is basically free money", "only works if you stay consistent"],
        "details": ["pay off the credit card first", "fees eat more than you think", "nobody can time the market",
                    "keep six months of expenses aside", "read the prospectus before buying", "taxes matter more than returns here"],
        "titles": ["Should I move money into {thing}?", "Is {thing} a good idea at 25?", "Question about {thing}",
                   "My plan for {thing}, feedback?", "Finally started with {thing}"],
    },
    "life": {
        "things": ["the sourdough starter", "this chili recipe", "meal prepping chicken", "a 5k training plan",
                   "the cast iron pan", "overnight oats", "the deadlift form", "that protein bowl", "my long run pace"],
        "claims": ["takes a few tries to get right", "is easier than it looks", "saved me a lot of time this week",
                   "needs more salt than you think", "is a game changer", "gets better with practice"],
        "details": ["rest the dough longer", "salt the water properly", "warm up before heavy sets", "prep on Sunday afternoon",
                    "keep the pan dry after washing", "slow runs build the base", "use thighs instead of breasts"],
        "titles": ["First attempt at {thing}", "Tips for {thing}?", "My take on {thing}", "Week 3 of {thing}",
                   "How I finally nailed {thing}"],
    },
    "general": {
        "things": ["that new game", "the patch notes", "this news story", "the season finale", "the old console",
                   "the city council vote", "that open world game", "the remaster"],
        "claims": ["is getting way too much hype", "is better than I expected", "deserves more attention",
                   "is missing important context", "brought back a lot of memories", "was handled badly"],
        "details": ["read the full article not just the headline", "the sequel fixed most of it",
                    "the community mods made it playable", "wait for a sale", "the source is pretty reliable"],
        "titles": ["What do you think about {thing}?", "Anyone else playing {thing}?", "Unpopular opinion about {thing}",
                   "Discussion: {thing}", "Can we talk about {thing}?"],
    },
    "zeta": {
        "things": ["the ZetaPhone X", "this ZetaPhone deal", "the new ZetaPhone"],
        "claims": ["is the best budget phone this year", "has insane battery life", "beats phones twice the price",
                   "is definitely worth it", "is a steal at this price"],
        "details": ["I've been using it for two weeks", "the camera is definitely better than expected",
                    "I recommend it to everyone who asks", "a lot of reviewers are sleeping on it", "grab it before the price goes back up"],
        "titles": ["ZetaPhone X is ${n} today - best budget phone of the year", "ZetaPhone X honest review after 2 weeks"],
    },
    "civic": {
        "things": ["the Harbor Bill", "the council vote", "the water price change", "this article", "the harbor clean-up plan"],
        "claims": ["deserves a proper public debate", "is more complicated than the headline", "looks like a bad deal for residents",
                   "has some good parts", "needs an independent audit", "was rushed through"],
        "details": ["the full text of the bill is on the council site", "the herald has been reliable on local news",
                    "check who funds that website", "the vote is next Tuesday", "there is a public hearing on Thursday"],
        "titles": ["What does {thing} mean for us?"],
    },
}
GENERIC = [
    "I'd definitely recommend reading a few reviews first", "a lot of people overlook this", "I don't think it's worth the hype",
    "it's been working great for me so far", "can't argue with that", "that's probably the best option right now",
    "I had the same question last year", "it depends on what you need", "this is a good point", "I'm not sure about that",
    "thanks for sharing this", "I wouldn't trust that source", "it's a lot better than the alternatives",
]
SUB_CLUSTER = {s: c for c, subs in CLUSTERS.items() for s in subs}

FILLERS = ["tbh", "imo", "honestly", "ngl", "lol", "fwiw", "idk", "basically", "literally", "to be fair", "imho", "haha"]
OPENERS = ["So ", "Well, ", "Honestly ", "Ok so ", "Yeah ", "Hmm, ", "Look, ", ""]
EMOJI = [":)", "😂", "🙏", "xD", ":D", "👍", "🔥", ":/"]
SIGNATURES = ["Hope this helps.", "Cheers", "YMMV", "Just my opinion", "Good luck!", "Source: experience", "edit: typo"]
MISSPELLINGS = [("definitely", "definately"), ("a lot", "alot"), ("recommend", "reccomend"), ("probably", "probly"),
                ("because", "cuz"), ("though", "tho"), ("really", "realy"), ("you", "u")]
# Personal vocabulary: every ordinary user gets a few of these so voices differ like real people's.
SUBSTITUTIONS = {
    "I think": ["I reckon", "I feel like", "Personally I think", "In my view", "My take is that", "I'd say"],
    "worth it": ["worth the money", "worth every penny", "a good buy", "worth a look"],
    "people": ["folks", "y'all", "most people", "everyone"],
    "a lot of": ["tons of", "lots of", "plenty of", "a ton of", "loads of"],
    "really": ["super", "pretty", "seriously", "genuinely"],
    "probably": ["likely", "most likely", "almost certainly"],
    "great": ["brilliant", "awesome", "fantastic", "excellent"],
    "first": ["beforehand", "up front", "first of all"],
}
PET_PHRASES = [
    "for real", "no joke", "in my experience", "as far as I can tell", "if you ask me", "from what I've seen",
    "just saying", "mind you", "all things considered", "for sure", "at the end of the day", "if that makes sense",
    "long story short", "for the record", "believe it or not", "as usual", "in the long run", "by the way",
    "to put it simply", "on the other hand", "last I checked", "not gonna lie", "funnily enough", "real talk",
]


@dataclass
class Style:
    lower: bool = False
    apostrophes: bool = True
    fillers: list[str] = field(default_factory=list)
    filler_rate: float = 0.3
    endings: list[str] = field(default_factory=lambda: ["."])
    emoji: str | None = None
    emoji_rate: float = 0.3
    signature: str | None = None
    sig_rate: float = 0.2
    opener: str = ""
    misspell: list[tuple[str, str]] = field(default_factory=list)
    double_space: bool = False
    n_sentences: tuple[int, int] = (1, 3)
    subs: list[tuple[str, str]] = field(default_factory=list)
    pets: list[str] = field(default_factory=list)
    pet_rate: float = 0.35

    def render(self, sentences: list[str], rng: random.Random) -> str:
        out = []
        for i, s in enumerate(sentences):
            question = s.endswith("?")
            s = s.rstrip(".!?")
            for good, mine in self.subs:
                s = re.sub(rf"\b{re.escape(good)}\b", mine, s)
            if self.pets and rng.random() < self.pet_rate:
                s = f"{s}, {rng.choice(self.pets)}"
            if i == 0 and self.opener and rng.random() < 0.5:
                s = self.opener + s[0].lower() + s[1:]
            if self.fillers and rng.random() < self.filler_rate:
                f = rng.choice(self.fillers)
                s = f"{s}, {f}" if rng.random() < 0.5 else f"{f.capitalize()} {s[0].lower() + s[1:]}"
            out.append(s + ("?" if question else rng.choice(self.endings)))
        sep = "  " if self.double_space else " "
        text = sep.join(out)
        for good, bad in self.misspell:
            text = re.sub(rf"\b{re.escape(good)}\b", bad, text, flags=re.I)
        if not self.apostrophes:
            text = re.sub(r"(\w)'(\w)", r"\1\2", text)
        if self.lower:
            text = text.lower()
        if self.emoji and rng.random() < self.emoji_rate:
            text += " " + self.emoji
        if self.signature and rng.random() < self.sig_rate:
            text += sep + self.signature
        return text


def random_style(rng: random.Random) -> Style:
    return Style(
        lower=rng.random() < 0.25,
        apostrophes=rng.random() > 0.3,
        fillers=rng.sample(FILLERS, rng.choice([0, 1, 2])),
        filler_rate=rng.uniform(0.15, 0.5),
        endings=rng.choice([["."], ["."], ["", "."], ["!", "."], ["..."], [".", "?"]]),
        emoji=rng.choice([None, None, None] + EMOJI),
        emoji_rate=rng.uniform(0.1, 0.4),
        signature=rng.choice([None, None, None] + SIGNATURES),
        opener=rng.choice(OPENERS),
        misspell=rng.sample(MISSPELLINGS, rng.choice([0, 0, 1])),
        double_space=rng.random() < 0.08,
        n_sentences=rng.choice([(1, 2), (1, 3), (2, 4)]),
        subs=[(k, rng.choice(v)) for k, v in rng.sample(list(SUBSTITUTIONS.items()), rng.randint(2, 5))],
        pets=rng.sample(PET_PHRASES, rng.randint(1, 3)),
        pet_rate=rng.uniform(0.15, 0.45),
    )


# The operator behind the sock-puppet ring writes like this on every account.
OPERATOR_STYLE = Style(
    lower=False, apostrophes=True, fillers=["tbh", "imo"], filler_rate=0.55, endings=["!!", "!!", "."],
    emoji=";)", emoji_rate=0.45, signature="Just my 2 cents", sig_rate=0.3, opener="Ok so ",
    misspell=[("definitely", "definately"), ("a lot", "alot"), ("recommend", "reccomend")],
    double_space=True, n_sentences=(2, 3),
)
BOOSTER_LINES = [
    "Just ordered one, thanks for posting!", "Great find, been waiting for this deal!", "Best phone at this price for sure!",
    "Been using it for a week, battery is insane!", "Thanks OP, just grabbed one!", "Can confirm, this phone is amazing!",
    "Wow great price, ordered!", "This is the best deal I've seen all year!",
]
SKEPTIC_LINES = [
    "Never heard of this brand, this reads like an ad.", "Why do all the first comments sound the same?",
    "Account history on some of these replies is kind of sus.", "Wait for real reviews before buying, this feels astroturfed.",
]


@dataclass
class User:
    name: str
    platform: str
    created: float
    style: Style
    subs: dict[str, float]
    window: tuple[int, int]  # (start hour UTC, length in hours)
    rate: float  # baseline items per day
    post_frac: float
    karma: tuple[int, int] = (0, 0)
    bio: str | None = None
    verified: bool | None = True

    def active_at(self, ts: float) -> bool:
        h = datetime.fromtimestamp(ts, timezone.utc).hour
        return (h - self.window[0]) % 24 < self.window[1]


class Builder:
    def __init__(self, seed: int):
        self.rng = random.Random(seed)
        self.users: dict[str, User] = {}
        self.posts: list[dict] = []
        self.comments: list[dict] = []
        self.thread: dict[str, list[dict]] = {}
        self._n = 0

    def nid(self, prefix: str) -> str:
        self._n += 1
        return f"{prefix}{self._n:05d}"

    # ---------------------------------------------------------------- text
    def sentences(self, topic: str, n: int) -> list[str]:
        t, rng = TOPICS[topic], self.rng
        forms = [
            lambda: f"{rng.choice(t['things'])} {rng.choice(t['claims'])}",
            lambda: f"I think {rng.choice(t['things'])} {rng.choice(t['claims'])}",
            lambda: rng.choice(t["details"]),
            lambda: rng.choice(GENERIC),
            lambda: f"has anyone else tried {rng.choice(t['things'])}?",
        ]
        out = []
        for _ in range(n):
            s = rng.choice(forms)()
            out.append(s[0].upper() + s[1:])
        return out

    def text(self, user: User, topic: str) -> str:
        n = self.rng.randint(*user.style.n_sentences)
        return user.style.render(self.sentences(topic, n), self.rng)

    def title(self, topic: str) -> str:
        t = TOPICS[topic]
        return self.rng.choice(t["titles"]).format(thing=self.rng.choice(t["things"]), n=self.rng.choice([49, 89, 129, 199, 249]))

    # ---------------------------------------------------------------- time
    @staticmethod
    def into_window(user: User, ts: float) -> float:
        """Move a timestamp forward to the user's next waking window (people sleep)."""
        h = datetime.fromtimestamp(ts, timezone.utc).hour
        offset = (h - user.window[0]) % 24
        if offset < user.window[1]:
            return ts
        return ts + (24 - offset) * 3600

    def time_in_window(self, user: User, day: int) -> float:
        h = (user.window[0] + self.rng.uniform(0, user.window[1])) % 24
        return START + day * DAY + h * 3600 + self.rng.uniform(0, 59)

    # ---------------------------------------------------------------- records
    def post(self, user: User, sub: str, ts: float, title: str, body: str, url: str | None = None,
             score: int | None = None, crosspost_of: str | None = None) -> dict:
        p = {
            "platform": user.platform, "id": self.nid("t3_" if user.platform == "reddit" else "tw_"), "author": user.name,
            "community": sub, "title": title, "body": body, "url": url, "created_utc": round(ts),
            "score": score if score is not None else int(self.rng.lognormvariate(3.2, 1.3)),
            "upvote_ratio": round(self.rng.uniform(0.75, 0.98), 2), "crosspost_parent": crosspost_of,
        }
        self.posts.append(p)
        self.thread[p["id"]] = []
        return p

    def comment(self, user: User, post: dict, ts: float, body: str, parent: dict | None = None) -> dict:
        c = {
            "platform": user.platform, "id": self.nid("t1_" if user.platform == "reddit" else "twr_"), "author": user.name,
            "post_id": post["id"], "parent_id": (parent or post)["id"], "community": post["community"],
            "body": body, "created_utc": round(ts), "score": max(1, int(self.rng.lognormvariate(1.5, 1.1))),
        }
        self.comments.append(c)
        self.thread[post["id"]].append(c)
        return c

    def members(self, sub: str, platform: str = "reddit") -> list[User]:
        return [u for u in self.users.values() if sub in u.subs and u.platform == platform]


ORDINARY = [
    "quiet_otter", "PixelPilgrim", "maple_circuit", "NorthernLlama", "grumpy_kettle", "solar_finch", "Byte_Barista",
    "midnight_mango", "TrailMixTom", "velvet_lantern", "rustbucket_42", "OatmealSamurai", "paper_comet", "LoFi_Lemur",
    "cobalt_heron", "SaltyPretzel9", "nimbus_nomad", "kettlebell_kat", "AnalogAndy", "fern_and_fjord", "tofu_titan",
    "Copper_Crane", "drowsy_dynamo", "WanderingWok", "zesty_quokka", "IronIvy", "cinder_sloth", "PolarPancake",
    "hazel_hacker", "BriskBadger", "lunar_lentil", "Gravel_Grinder", "teal_tortoise", "ScrappyScone", "amber_axolotl",
    "QuantumQuiche", "mossy_marmot", "Retro_Raccoon", "pepper_pixel", "WillowWhisk",
]
BOOSTERS = ["brightdeal_fan_4821", "phonefan_2291", "lucky_buyer_77", "shopper_jx9", "newtech_lover_03"]

# A coordinated network pushing a fictional "news" site about a fictional town. Content is invented;
# what matters for detection is the behaviour (same links within minutes, copy-paste, no sleep).
EXTRA_SUBS = {"worldnews": 32_000_000, "politics": 8_500_000}
CAMPAIGN = ["Brave_Lynx_4471", "Quiet-River-2290", "Distinct_Ad_8812", "Hot-Pie-3301", "Sea_Wolf_7719", "Few_Bluebird_5520"]
CAMPAIGN_DOMAIN = "dailytruth-news.example"
ARTICLES = [
    ("council-hid-harbor-report", "BREAKING: Northbridge council hid report on harbor contamination"),
    ("harbor-bill-vote-decided", "Leaked memo: Harbor Bill vote was decided before the debate"),
    ("mayor-offshore-accounts", "Northbridge mayor linked to offshore accounts, documents show"),
    ("water-prices-triple", "Water prices in Northbridge set to triple under Harbor Bill"),
    ("protest-blackout", "Media silent as thousands protest Harbor Bill in Northbridge"),
]
TEMPLATE_COMMENTS = [
    "This needs to be seen by everyone. Share before it gets deleted.",
    "Why is nobody talking about this? Share it everywhere.",
    "The media will never report this. Spread the word.",
    "Everyone needs to read this before the vote.",
    "They are hiding the truth from Northbridge residents. Share this.",
]
BACKGROUND_LINES = ["Wake up people, read the article.", "Do your own research, the truth is out there.",
                    "Share this before it disappears."]
SKEPTIC_NEWS = ["Who runs dailytruth-news? I can't find any editor or address on that site.",
                "Every one of these replies was posted within a minute. Feels coordinated.",
                "Is there a second source for this? The herald has nothing on it."]
MIRROR_HEADLINES = [
    "Northbridge library extends weekend hours", "Regional rail timetable changes next month",
    "Storm advisory issued for the coast", "City opens new cycling lane on Harbor Road",
    "Local team wins the regional final", "Ferry service resumes after repairs",
    "Council publishes next year's budget draft", "New bakery opens in the old station building",
]
TWITTER_ORDINARY = ["devon_codes", "ramen_rachel", "the_daily_byte", "sk8r_sam", "budget_beth"]


def generate(seed: int = 42) -> dict:
    b = Builder(seed)
    rng = b.rng

    # ------------------------------------------------------------ ordinary reddit users
    for name in ORDINARY:
        primary = rng.choice(list(CLUSTERS))
        subs = {s: rng.uniform(0.6, 1.0) for s in rng.sample(CLUSTERS[primary], rng.randint(2, len(CLUSTERS[primary])))}
        for s in rng.sample([s for s in SUBS if s not in subs], rng.randint(1, 2)):
            subs[s] = rng.uniform(0.1, 0.4)
        b.users[name] = User(
            name, "reddit", END - rng.uniform(1.2, 9) * 365 * DAY, random_style(rng), subs,
            (rng.randrange(24), rng.randint(5, 9)), rng.uniform(0.2, 1.0), rng.uniform(0.08, 0.25),
            (rng.randint(200, 40_000), rng.randint(500, 90_000)),
        )

    # ------------------------------------------------------------ the sock-puppet ring
    ring_window = (13, 5)
    tech = {s: 1.0 for s in CLUSTERS["tech"]}
    gg = b.users["gadget_guru_88"] = User(
        "gadget_guru_88", "reddit", START + 6 * DAY, OPERATOR_STYLE, {**tech, "techdeals": 1.6}, ring_window, 0.8, 0.3,
        (1_840, 2_210), bio="Tech deals hunter. More reviews at twitter.com/gadgetguru88",
    )
    tm = b.users["TechSavvyMike_"] = User(
        "TechSavvyMike_", "reddit", START + 8 * DAY, OPERATOR_STYLE, {**tech, "android": 1.5}, ring_window, 0.6, 0.15, (420, 980),
    )
    hr = b.users["honest_reviewer_22"] = User(
        "honest_reviewer_22", "reddit", START + 10 * DAY, OPERATOR_STYLE, {**tech, "gadgets": 1.5}, ring_window, 0.6, 0.2, (650, 1_120),
    )
    ring = [gg, tm, hr]

    # ------------------------------------------------------------ regular posters
    mw = b.users["daily_market_wrap"] = User(
        "daily_market_wrap", "reddit", END - 3.1 * 365 * DAY,
        Style(endings=["."], n_sentences=(2, 3)), {"investing": 1.0, "cryptomoonshots": 0.3, "news": 0.3, "personalfinance": 0.3},
        (9, 1), 0.3, 0.0, (52_000, 3_100),
    )
    marta = b.users["chef_marta"] = User(
        "chef_marta", "reddit", END - 4.5 * 365 * DAY,
        Style(endings=["!", "."], emoji="🙏", emoji_rate=0.2, n_sentences=(2, 3), pets=["trust me on this", "happy cooking"],
              subs=[("I think", "In my kitchen"), ("great", "delicious")]),
        {"cooking": 1.0, "mealprep": 0.7, "fitness": 0.4, "running": 0.4}, (18, 4), 0.8, 0.0, (23_400, 8_100),
        bio="Home cook, daily recipes. instagram.com/marta_cooks_daily",
    )
    kenji = b.users["gamer_kenji"] = User(
        "gamer_kenji", "reddit", END - 2.2 * 365 * DAY,
        Style(lower=True, apostrophes=False, fillers=["lol", "ngl"], filler_rate=0.4, endings=["", "."], n_sentences=(1, 2)),
        {"gaming": 1.0, "buildapc": 0.6, "askreddit": 0.5, "techdeals": 0.3}, (1, 5), 0.9, 0.0, (9_800, 15_300),
        bio="streaming most nights - twitch.tv/kenji_plays",
    )

    # ------------------------------------------------------------ boosters (new, low karma)
    boost_day = SPAN_DAYS - 10
    boosters = []
    for name in BOOSTERS:
        u = b.users[name] = User(
            name, "reddit", START + (boost_day - rng.uniform(2, 6)) * DAY, Style(endings=["!"], n_sentences=(1, 1)),
            {"techdeals": 1.0, "gadgets": 0.5}, (rng.randrange(24), 3), 0.0, 0.0, (1, rng.randint(1, 15)),
        )
        boosters.append(u)

    # ------------------------------------------------------------ organic + regular posts
    for u in list(b.users.values()):
        if u.post_frac == 0:
            continue
        subs, weights = list(u.subs), list(u.subs.values())
        for day in range(SPAN_DAYS):
            for _ in range(_poisson(rng, u.rate * u.post_frac)):
                ts = b.time_in_window(u, day)
                if ts < u.created:
                    continue
                sub = rng.choices(subs, weights)[0]
                topic = SUB_CLUSTER[sub]
                if u in ring and rng.random() < 0.25:
                    topic = "zeta"
                b.post(u, sub, ts, b.title(topic), b.text(u, topic))
    for day in range(SPAN_DAYS):
        date = datetime.fromtimestamp(START + day * DAY, timezone.utc)
        ts = START + day * DAY + 9 * 3600 + rng.uniform(0, 240)
        b.post(mw, "investing", ts, f"Daily Market Wrap - {date:%b %d, %Y}",
               "Indexes closed mixed. Bond yields moved slightly higher. Full summary and calendar for tomorrow below.",
               score=rng.randint(40, 400))
        if date.weekday() == 0:
            b.post(mw, "cryptomoonshots", ts + 600, f"Weekly crypto movers - {date:%b %d}",
                   "Top gainers and losers of the week with volume notes. Not financial advice.")
        if rng.random() < 0.82:
            b.post(marta, "cooking", b.time_in_window(marta, day), b.title("life"), b.text(marta, "life"), score=rng.randint(80, 3000))
        if rng.random() < 0.9:
            b.post(kenji, "gaming", b.time_in_window(kenji, day), b.title("general"), b.text(kenji, "general"))

    # ------------------------------------------------------------ organic comments on every post so far
    for p in list(b.posts):
        author = b.users[p["author"]]
        members = [u for u in b.members(p["community"]) if u.rate > 0]
        n = max(1, int(rng.lognormvariate(1.4, 0.7)))
        for _ in range(n):
            ts = p["created_utc"] + min(rng.expovariate(1 / 5400), 2 * DAY)
            awake = [u for u in members if u.active_at(ts) and u.created < ts]
            pool = awake or [u for u in members if u.created < ts]
            if not pool:
                continue
            u = rng.choices(pool, [x.rate * x.subs.get(p["community"], 0.1) for x in pool])[0]
            if u.name == author.name and rng.random() > 0.15:
                continue
            existing = b.thread[p["id"]]
            parent = rng.choice(existing) if existing and rng.random() < 0.4 else None
            b.comment(u, p, ts, b.text(u, SUB_CLUSTER.get(p["community"], "general")), parent)
        # the ring always shows up quickly on each other's posts
        if author in ring:
            for other in ring:
                if other is not author and other.created < p["created_utc"] and rng.random() < 0.7:
                    b.comment(other, p, p["created_utc"] + rng.uniform(180, 1200), b.text(other, "zeta" if "Zeta" in p["title"] else "tech"))

    # chef_marta and gamer_kenji comment around their other subs
    for u, extra in ((marta, ["mealprep", "fitness", "running"]), (kenji, ["buildapc", "askreddit", "techdeals"])):
        targets = [p for p in b.posts if p["community"] in extra and p["author"] != u.name]
        for p in rng.sample(targets, min(len(targets), 45)):
            b.comment(u, p, b.into_window(u, p["created_utc"] + rng.uniform(600, 20000)), b.text(u, SUB_CLUSTER[p["community"]]))
    for p in rng.sample([p for p in b.posts if p["community"] in ("news", "personalfinance")], 20):
        b.comment(mw, p, b.into_window(mw, p["created_utc"] + rng.uniform(1800, 3600)), b.text(mw, "finance"))

    # ------------------------------------------------------------ the boosted post and its crossposts
    t0 = START + boost_day * DAY + 14 * 3600 + 300
    boosted = b.post(
        gg, "techdeals", t0, "ZetaPhone X is $199 today - best budget phone of the year",
        OPERATOR_STYLE.render([
            "The ZetaPhone X is definitely the best budget phone this year",
            "u/honest_reviewer_22 did a full review last week and I recommend it to everyone",
            "r/android people will love this one",
        ], rng) + "  Deal: https://zetaphone-store.example/deal?ref=gg88",
        url="https://zetaphone-store.example/deal?ref=gg88", score=1850,
    )
    boosted["upvote_ratio"] = 0.71
    b.comment(tm, boosted, t0 + 180, OPERATOR_STYLE.render(b.sentences("zeta", 2), rng))
    b.comment(hr, boosted, t0 + 420, OPERATOR_STYLE.render(b.sentences("zeta", 2), rng))
    for i, u in enumerate(boosters):
        b.comment(u, boosted, t0 + 240 + i * rng.uniform(60, 150), rng.choice(BOOSTER_LINES))
    tech_members = [u for u in b.members("techdeals") + b.members("gadgets") if u.rate > 0 and u not in ring]
    skeptics = rng.sample(tech_members, 3)
    for i, u in enumerate(skeptics):
        c = b.comment(u, boosted, t0 + 3600 * (1 + i * 1.5) + rng.uniform(0, 900), SKEPTIC_LINES[i])
        b.comment(gg, boosted, c["created_utc"] + rng.uniform(240, 600),
                  OPERATOR_STYLE.render(["It's definitely legit, I bought two", "a lot of people here own one"], rng), parent=c)
    for u in rng.sample(tech_members, 6):
        ts = t0 + rng.uniform(1.5, 8) * 3600
        b.comment(u, boosted, ts, b.text(u, "tech"))
    x1 = b.post(hr, "gadgets", t0 + 1200, boosted["title"], "Crossposting because this deal is too good!!  ;)",
                url=boosted["url"], score=640, crosspost_of=boosted["id"])
    x2 = b.post(tm, "android", t0 + 1560, boosted["title"], "Saw this on r/techdeals, ZetaPhone X for $199!!",
                url=boosted["url"], score=310, crosspost_of=boosted["id"])
    for x in (x1, x2):
        for u in rng.sample(boosters, 3):
            b.comment(u, x, x["created_utc"] + rng.uniform(60, 600), rng.choice(BOOSTER_LINES))
        for u in rng.sample(tech_members, 2):
            b.comment(u, x, x["created_utc"] + rng.uniform(3600, 14000), b.text(u, "tech"))

    # an earlier "review" by another ring member, boosted the same way
    t1 = START + (boost_day - 8) * DAY + 15 * 3600
    review = b.post(hr, "gadgets", t1, "ZetaPhone X honest review after 2 weeks",
                    OPERATOR_STYLE.render(b.sentences("zeta", 3), rng) + "  Full video review: https://youtube.com/@honestreviews22",
                    url="https://youtube.com/@honestreviews22", score=920)
    for u in [gg, tm] + rng.sample(boosters, 3):
        body = OPERATOR_STYLE.render(b.sentences("zeta", 2), rng) if u in ring else rng.choice(BOOSTER_LINES)
        b.comment(u, review, t1 + rng.uniform(120, 900), body)
    for u in rng.sample(tech_members, 4):
        b.comment(u, review, t1 + rng.uniform(2, 9) * 3600, b.text(u, "tech"))

    # an organic post with normal, slower traction for comparison
    t2 = START + (boost_day + 1) * DAY + 20 * 3600
    organic = b.post(b.users["quiet_otter"], "buildapc", t2, "Finally finished my first build",
                     "Took me three weekends but it posts. Cable management could be better. Any tips on fan curves?", score=2300)
    bpc = [u for u in b.members("buildapc") if u.rate > 0 and u.name != "quiet_otter"]
    for _ in range(14):
        u = rng.choice(bpc)
        b.comment(u, organic, t2 + min(rng.expovariate(1 / 9000), DAY), b.text(u, "tech"))

    # ------------------------------------------------------------ twitter side
    tw_gg = User("gadgetguru88", "twitter", START + 5 * DAY, OPERATOR_STYLE, {"#techdeals": 1, "#gadgets": 1}, ring_window, 0.6, 1.0,
                 bio="Gadget reviews & deals | yt: youtube.com/@honestreviews22")
    tw_mike = User("mike_techsavvy", "twitter", END - 6 * 365 * DAY, random_style(rng), {"#gaming": 1, "#tech": 1}, (2, 5), 0.6, 1.0,
                   bio="Gamer, dad, coffee.")
    tw_users = [tw_gg, tw_mike] + [
        User(n, "twitter", END - rng.uniform(1, 8) * 365 * DAY, random_style(rng), {rng.choice(["#tech", "#gadgets", "#cooking", "#finance"]): 1},
             (rng.randrange(24), rng.randint(5, 8)), rng.uniform(0.3, 0.8), 1.0)
        for n in TWITTER_ORDINARY
    ]
    tag_topic = {"#techdeals": "tech", "#gadgets": "tech", "#tech": "tech", "#gaming": "general", "#cooking": "life", "#finance": "finance"}
    tweets = []
    for u in tw_users:
        b.users["tw:" + u.name] = u
        for day in range(SPAN_DAYS):
            for _ in range(_poisson(rng, u.rate)):
                ts = b.time_in_window(u, day)
                if ts < u.created:
                    continue
                tag = rng.choice(list(u.subs))
                topic = "zeta" if u is tw_gg and rng.random() < 0.4 else tag_topic[tag]
                tweets.append(b.post(u, tag, ts, "", b.text(u, topic)))
    for t in rng.sample(tweets, min(60, len(tweets))):
        u = rng.choice([x for x in tw_users if x.name != t["author"]])
        b.comment(u, t, t["created_utc"] + rng.uniform(300, 7200), b.text(u, tag_topic.get(t["community"], "tech")))

    # ------------------------------------------------------------ news campaign (fictional town, fictional websites)
    snapshots: list[dict] = []
    r2 = random.Random(seed + 7)  # separate stream so everything above stays identical
    for u in boosters:
        u.verified = False
    campaign = []
    for name in CAMPAIGN:
        u = b.users[name] = User(name, "reddit", START + (SPAN_DAYS - 22) * DAY + r2.uniform(0, 2 * DAY),
                                 Style(endings=["."], n_sentences=(1, 1)), {"news": 1, "worldnews": 1, "politics": 1},
                                 (0, 24), 0.0, 0.0, (r2.randint(20, 120), r2.randint(10, 200)), verified=False)
        campaign.append(u)
    news_members = [u for u in b.members("news") if u.rate > 0]

    def organic_comments(post: dict, k: int, mean_delay: float) -> None:
        for _ in range(k):
            u = r2.choice(news_members)
            ts = b.into_window(u, post["created_utc"] + min(r2.expovariate(1 / mean_delay), DAY))
            line = r2.choice(SKEPTIC_NEWS) if r2.random() < 0.25 else u.style.render(b.sentences("civic", r2.randint(1, 2)), r2)
            b.comment(u, post, ts, line)

    for k, (slug, headline) in enumerate(ARTICLES):
        t = START + (SPAN_DAYS - 16 + 3 * k) * DAY + r2.uniform(0, 24) * 3600
        url = f"https://{CAMPAIGN_DOMAIN}/{slug}?utm_source=share"
        order = r2.sample(campaign, len(campaign))
        main = b.post(order[0], "news", t, headline, "", url=url, score=r2.randint(900, 1600))
        main["upvote_ratio"] = round(r2.uniform(0.62, 0.72), 2)
        for i, u in enumerate(order[1:]):  # every account answers within seconds
            b.comment(u, main, t + r2.uniform(10, 45) + i * 4, r2.choice(TEMPLATE_COMMENTS))
        for u in order[1:4]:  # and pushes the same link into other subs within minutes
            variant = r2.choice([headline, headline + "!!", "MUST READ: " + headline, headline.replace("BREAKING: ", "")])
            x = b.post(u, r2.choice(["worldnews", "politics", "news"]), t + r2.uniform(60, 420), variant, "",
                       url=f"https://{CAMPAIGN_DOMAIN}/{slug}", score=r2.randint(40, 300))
            for v in r2.sample([w for w in campaign if w is not u], 2):
                b.comment(v, x, x["created_utc"] + r2.uniform(10, 45), r2.choice(TEMPLATE_COMMENTS))
        organic_comments(main, r2.randint(4, 7), 7200)
        snapshots += _snapshots(b, main, burst=True)

    # background chatter around the clock: copy-paste lines on any news thread
    news_posts = [p for p in b.posts if p["community"] in ("news", "worldnews", "politics", "askreddit")]
    for u in campaign:
        for day in range(SPAN_DAYS - 21, SPAN_DAYS):
            for _ in range(r2.randint(3, 6)):
                ts = START + day * DAY + r2.uniform(0, DAY)
                older = [p for p in news_posts if p["created_utc"] < ts]
                if older and ts > u.created:
                    b.comment(u, r2.choice(older[-80:]), ts, r2.choice(TEMPLATE_COMMENTS + BACKGROUND_LINES))

    # an auto-posting mirror bot: same site, every ~37 minutes, day and night
    mirror = b.users["NewsMirrorAuto"] = User("NewsMirrorAuto", "reddit", END - 200 * DAY, Style(), {"news": 1}, (0, 24),
                                              0.0, 0.0, (310, 4), verified=False)
    ts = END - 4 * DAY
    i = 0
    while ts < END:
        b.post(mirror, "news", ts, f"[Mirror] {MIRROR_HEADLINES[i % len(MIRROR_HEADLINES)]}", "",
               url=f"https://headline-mirror.example/item/{1000 + i}", score=1)
        ts += 37 * 60 + r2.uniform(-60, 60)
        i += 1

    # organic political news for comparison: real discussion, including criticism of the council
    herald = "https://northbridge-herald.example"
    p1 = b.post(b.users["paper_comet"], "news", START + (SPAN_DAYS - 12) * DAY + 17 * 3600,
                "Harbor Bill passes first reading: what it changes for Northbridge residents", "",
                url=f"{herald}/harbor-bill-explained", score=1450)
    organic_comments(p1, 16, 6000)
    p2 = b.post(b.users["cobalt_heron"], "news", START + (SPAN_DAYS - 8) * DAY + 19 * 3600,
                "Opinion: the Harbor Bill is a bad deal for Northbridge", "",
                url=f"{herald}/opinion-harbor-bill-bad-deal", score=880)
    organic_comments(p2, 12, 7000)
    for p in (p1, p2, organic):
        snapshots += _snapshots(b, p, burst=False)
    snapshots += _snapshots(b, boosted, burst=True)

    # ------------------------------------------------------------ assemble
    counts: dict[str, int] = {}
    for c in b.comments:
        counts[c["post_id"]] = counts.get(c["post_id"], 0) + 1
    for p in b.posts:
        p["num_comments"] = counts.get(p["id"], 0)
    accounts = [{
        "platform": u.platform, "username": u.name, "created_utc": round(u.created), "link_karma": u.karma[0],
        "comment_karma": u.karma[1], "bio": u.bio, "status": "active", "verified_email": u.verified,
    } for u in b.users.values()]
    communities = [{"platform": "reddit", "name": s, "subscribers": n} for s, n in {**SUBS, **EXTRA_SUBS}.items()]
    return {"accounts": accounts, "communities": communities, "posts": b.posts, "comments": b.comments, "links": [],
            "snapshots": snapshots}


def _snapshots(b: Builder, post: dict, burst: bool) -> list[dict]:
    """Score every 5 min for 6 h, then every 30 min to 24 h. Bursts add points without comments."""
    t0, final = post["created_utc"], post["score"]
    times = sorted(t0 + m * 60 for m in list(range(0, 361, 5)) + list(range(390, 1441, 30)))
    comment_times = sorted(c["created_utc"] for c in b.thread[post["id"]])
    out = []
    for ts in times:
        m = (ts - t0) / 60
        if burst:
            score = 2 + 0.65 * final * min(1.0, max(0.0, (m - 6) / 14)) + 0.35 * final * (1 - math.exp(-m / 300))
            ratio = 0.97 - 0.27 * min(1.0, m / 120)
        else:
            score = 1 + final * (1 - math.exp(-m / 200))
            ratio = 0.9
        out.append({"platform": "reddit", "post_id": post["id"], "ts": round(ts), "score": round(score),
                    "num_comments": sum(1 for x in comment_times if x <= ts), "upvote_ratio": round(ratio, 2)})
    return out


def _poisson(rng: random.Random, lam: float) -> int:
    L, k, p = math.exp(-lam), 0, 1.0
    while True:
        p *= rng.random()
        if p <= L:
            return k
        k += 1
