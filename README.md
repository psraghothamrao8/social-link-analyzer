# Social Link Analyzer

A local web app for working out how Reddit accounts relate to each other and to accounts on other
platforms. It is built for moderation, trust & safety and research work: spotting sock puppets, astroturfed
posts and coordinated accounts in public data.

| # | Requirement | Where in the app | How it works |
|---|---|---|---|
| 1 | Ontology-based graph that shows routes and links | **Graph & routes** tab | Accounts, communities, external profiles, domains and operator hypotheses are typed by an OWL ontology (`slo:`), and so are the relations between them. The route finder ranks the strongest paths between any two nodes and explains each hop. Exports to Turtle / JSON-LD. |
| 2 | How a post built traction and who it targets | **Post traction** tab | Comment velocity against the community baseline, reply tree, the earliest engagers (account age, karma, repeat engagement, possible alts), crossposts, an amplification index, and the targets: communities, mentioned users, linked domains, keywords and the audience it actually reached. |
| 3 | Comment and post patterns when one person runs several accounts | **Multi-account** tab | Pairwise behavioural fingerprinting: topic-masked stylometry, pet phrases and misspellings, typing habits, active hours, shared threads and communities, interactions, usernames, creation dates and cross-links. Each signal is calibrated against the whole dataset. Strong pairs are grouped into "operator" hypotheses. Works across platforms. |
| 4 | Other subreddits that regular posters are active in | **Regular posters** tab | Cadence metrics (active-day ratio, gap regularity, hour entropy) find daily, high-volume and scheduled/bot-like posters, either overall or inside one community. Each one is listed with the other communities they use, plus a profile drawer. |
| 5 | Monitor news topics and see which posts were pushed artificially | **Watchlist** tab | A topic is a set of keywords, websites and (optionally) subreddits. The app collects matching posts on a schedule, tracks their score, and ranks them by amplification index. It also lists the accounts that posted or boosted them early, with bot labels, coordinated networks and operator links. |
| 6 | Bot or legit account | **Bots & coordination** tab, profile drawer, engager tables, graph node colour | A 0–1 bot score for every account with a label (likely automated / suspicious / likely human / insufficient data) and the behaviours behind it. |
| 7 | Accounts that artificially push posts together | **Bots & coordination** tab, `coSharesWith` graph edges | Coordinated-sharing detection: accounts that post the same link or near-identical text within minutes of each other, repeatedly. These pairs are grouped into networks, and bursts are listed. |
| 8 | Vote manipulation | **Post traction** → Score over time | The app records a post's score every 5 minutes. A jump in points with no matching discussion is flagged as a vote burst. |

**Flags are about behaviour, not opinion.** Topics only decide *what* to monitor. Accounts and posts are flagged
for how their traction was produced: automation, coordination, vote bursts and early boosting by new or linked
accounts. Organic posts on the same topic stay unflagged whatever their viewpoint, and the app has no feature
that classifies or lists people by political stance. The demo shows this: a post criticising the (fictional)
council gets amplification index 0.04, while the coordinated campaign's posts score above 0.6.

## Quick start

```powershell
pip install -r requirements.txt
python run.py
```

Open http://127.0.0.1:8000, go to **Data sources** and click **Load demo dataset**. The demo is synthetic.
Every tab has something to find in it:

* a 3-account sock-puppet ring with a matching Twitter account, and a boosted product post
* a 6-account bot network pushing a fake news site about a fictional town, with copy-paste comments and vote bursts
* an auto-posting "mirror" bot, a scheduled "daily market wrap" poster, and about 40 ordinary users
* two organic news posts on the same story for comparison, one of them critical of the council

Two watch topics come with the demo. They are paused, because their terms only exist in the synthetic data.

Run the tests with `python -m pytest -q`. They check that each planted pattern is detected.

## Collecting real Reddit data

Reddit rejects most requests made without API keys. Create a **script** app at
https://www.reddit.com/prefs/apps, copy `.env.example` to `.env` and fill in `REDDIT_CLIENT_ID`,
`REDDIT_CLIENT_SECRET` and a descriptive `REDDIT_USER_AGENT`. Then, in **Data sources**:

* **User**: profile, bio, up to N recent posts and comments. Links in the bio to other platforms are recorded as disclosures.
* **Post**: the full comment tree, including collapsed "more comments", plus crossposts. Set **Commenter histories** to
  fetch the earliest N commenters' histories. The audience and alt-account analysis needs those histories.
* **Subreddit**: recent posts, plus the histories of the N most frequent posters. Use this to feed **Regular posters**.

Reddit only returns about the 1000 most recent items per listing. For older history, import an archive dump.

### Other platforms

The app does not scrape other networks. Instead:

* It reads profile links that accounts publish themselves: Twitter/X, Instagram, YouTube, TikTok, GitHub,
  Facebook, LinkedIn, Telegram, Twitch, Medium, Substack, Mastodon, Bluesky and Discord, plus `@handle`
  phrases such as "my insta: @name". A link in a bio counts as a self-disclosure. Links inside posts count as
  weaker evidence.
* **Import** (Data sources) accepts a generic JSON format for any platform. See
  `samples/other_platforms_example.json`. Imported accounts go through the same stylometry and timing comparison,
  which is how a Reddit account gets matched to a Twitter or Mastodon account.
  `samples/reddit_archive_example.ndjson` shows the Reddit NDJSON dump format (Pushshift / Arctic Shift).
* **Confirmed identity links** let an analyst record links they have verified. They become `sameAs` edges.

## The ontology

`GET /api/export/ontology.ttl` returns the schema, which is aligned with SIOC and FOAF.

* Classes: `Account` (`RedditAccount`, `ExternalAccount`), `Community` (`Subreddit`), `Post`, `Comment`,
  `Domain`, `Platform`, `Agent` (the hypothesised operator behind a group of accounts), `CoordinatedNetwork`,
  `Topic`. Accounts carry `slo:botScore` / `slo:botLabel`.
* Relations:
  * Membership: `activeIn`, `linksTo`
  * Interaction: `interactsWith`, `mentions`
  * Coordination: `amplifies` (2+ comments within 30 min of the other account's posts), `coSharesWith`,
    `memberOfNetwork` (class `CoordinatedNetwork`)
  * Identity: `discloses`, `possibleSameOperator`, `sameAs`, `controlledBy`

In the graph, colour shows the relation family and line style shows the relation. Edge weights and
confidences are reified with `rdf:Statement` in the Turtle export.

Routes are cheapest paths. Identity relations cost the least to traverse. Busy hubs such as large subreddits
cost more, so a route through r/AskReddit ranks below a disclosed profile link.

## How the multi-account score works

For every pair of accounts with 3 or more items:

1. **Identity signals** (these decide the score):
   * character n-grams on topic-masked text (Stamatatos text distortion), so the score reflects style rather than subject
   * word n-grams: pet phrases and misspellings
   * function-word profile
   * typing habits (`!!`, emoji, double spaces, lowercase starts, contractions without apostrophes, sentence length)
   * hour-of-day and weekday fingerprint
   * shared uncommon domains, username similarity, account creation dates
2. **Network signals** (these can only amplify the score): shared communities, shared threads (Jaccard), and
   reply/early-comment intensity. Whole communities share these, so they never create a match on their own.
3. Each signal is converted to evidence by its **percentile among all pairs** in the dataset. A cross-link
   between the two accounts adds a bonus. Accounts with little data are pulled down.

Levels: strong ≥ 0.75, moderate ≥ 0.60, weak ≥ 0.45. Strong pairs are grouped into operator hypotheses.
These thresholds were tuned on the synthetic demo. Validate them on labelled cases from your own data before
relying on them.

## Monitoring topics (Watchlist)

Create a topic with keywords (comma or newline separated, `"quoted phrases"` allowed; non-Latin scripts
such as Hindi work), websites and optionally subreddits. While **Monitor on Reddit** is on, a background job
runs every N minutes:

1. It runs a Reddit search for the keywords (site-wide, or inside the listed subreddits) and reads
   `/domain/<site>` listings, then keeps only posts that really match.
2. It collects the 12 newest unseen posts in full, with the histories of their first 5 commenters.
3. It records the score of every matching post younger than 48 hours every 5 minutes.

The topic report re-analyses every matching post in the collected data. You can also track a single post's
score from its traction page. Monitoring needs Reddit API keys. Set `SLA_SCHEDULER=0` to turn the
background job off.

## How the bot score works

Each behaviour can raise the score up to its own cap, and the caps combine noisy-OR style:
`score = 1 − Π(1 − cap × evidence)`. Several weak signals add up, and a single strong one cannot reach
"likely automated" on its own.

| Signal | Cap | Fires when |
|---|---|---|
| No daily rest period | 0.55 | 40+ items and no quiet stretch of 5+ hours in the day |
| Clockwork timing | 0.50 | Very regular intervals, or most posts in the same 20-minute slot every day |
| Instant replies | 0.60 | Replies arrive faster than reading the parent plus typing the reply allows |
| Repetition | 0.50 | Many of its own texts are identical or near-identical |
| Link sharing | 0.35 | Mostly external links, concentrated on few sites |
| Inhuman volume | 0.45 | More than 15 items/day on average, or 12+ in one hour |
| New account burst / dormant then burst | 0.30 / 0.35 | Heavy activity in the first month, or an old account that suddenly wakes up |
| New with no karma, default username, unverified email, no engagement, short generic comments, suspended | 0.12–0.35 | Weak supporting signals |

Labels: ≥ 0.70 likely automated, ≥ 0.40 suspicious, otherwise likely human (or insufficient data below 5 items).
Automated does not mean malicious: announced utility bots score high. Human-run sock puppets usually score
low here and show up in **Multi-account** instead. The caps were set on the synthetic demo, so validate them
against accounts you already know before relying on the labels.

## How coordinated sharing works

Every link (with tracking parameters such as `utm_*` and `ref` removed) and every headline or comment of 8+
words is a "share". Two accounts that share the same item within the window (default 10 minutes) are
linked. They only count as coordinated when this happens for at least **2 different items**: one coincidence
on a breaking-news link is normal. Linked pairs are grouped into networks. A burst is one item pushed by 3+
accounts inside the window.

## Responsible use

* Only public content is used. Follow Reddit's API terms and the terms of any platform you import from.
* Every link the app suggests is a **lead to review, not proof**. Shared households, coworkers, bots built
  from one template, or fans of the same creator can produce similar fingerprints.
* This tool is for detecting coordinated or deceptive behaviour. Do not use it to expose the identity of
  pseudonymous people or to harass anyone.
* Watch topics decide what is monitored, never who is flagged. Do not use flags as a judgement of anyone's
  opinions: criticism, dissent and unpopular views are not inauthentic behaviour.

## Project layout

```
app/
  main.py                 FastAPI routes (REST API + static UI)
  db.py                   SQLite schema and upserts (platform-scoped ids)
  collectors/
    reddit.py             Reddit API client (OAuth / public) and ingest jobs
    importer.py           generic multi-platform JSON + Reddit NDJSON import
    crossplatform.py      external profile / mention / domain extraction
    demo.py               synthetic dataset with planted patterns
  analysis/
    ontology.py           ontology definition, Turtle + JSON-LD export
    corpus.py             in-memory snapshot used by all analyses (cached per DB version)
    graph.py              account-level knowledge graph + route finding (networkx)
    traction.py           post traction, amplification signals, targets, audience
    linking.py            multi-account / cross-platform similarity model
    activity.py           posting cadence, regular posters, account profiles
    bots.py               bot-likelihood score with explainable signals
    coordination.py       coordinated-sharing networks and bursts
    watch.py              topic matching and topic reports
static/                   single-page UI (vanilla JS, Cytoscape.js from cdnjs)
samples/                  import format examples
tests/                    end-to-end tests on the demo dataset
```

## API (selection)

| Endpoint | Purpose |
|---|---|
| `GET /api/graph?edges=&focus=&depth=&min_weight=` | graph elements (Cytoscape JSON) |
| `GET /api/route?source=&target=&k=` | k best routes with hop explanations |
| `GET /api/traction?post_id=` | traction and target analysis for a post |
| `GET /api/linking/scan?min_score=` | linked pairs and operator clusters |
| `GET /api/linking/account?account=` | most similar accounts (any platform) |
| `GET /api/linking/pair?a=&b=` | side-by-side evidence for two accounts |
| `GET /api/activity/regulars?community=&basis=` | regular posters and their other communities |
| `GET /api/activity/account?account=` | account profile |
| `POST /api/collect/{user,post,subreddit}` | collect from Reddit |
| `POST /api/import` | import JSON / NDJSON (raw body) |
| `GET /api/export/{ontology.ttl,graph.ttl,graph.jsonld}` | RDF exports |
| `GET /api/bots?label=&min_score=` | bot scores and labels |
| `GET /api/coordination?window=&min_repeats=` | coordinated networks, pairs, bursts |
| `GET/POST/DELETE /api/watch/topics`, `POST /api/watch/topics/{id}/run`, `GET /api/watch/topics/{id}/report` | watchlist |
| `GET/POST/DELETE /api/watch/posts` | score tracking for single posts |
