# gridirondata

The official Python client for the [GridIron Data](https://www.gridirondata.com)
fantasy football API: player lookups, weekly projections, stats, snap counts,
projection deviations, matchup history, news and the Pro injury changes feed.

One runtime dependency (`requests`), Python 3.8+.

## Install

The package isn't on PyPI yet. Install it straight from GitHub:

```bash
pip install "git+https://github.com/gridirondata/gridiron-data-python"
```

## Quickstart

Get a free key at <https://www.gridirondata.com/pages/signup.html> (no card),
then:

```bash
export GRIDIRON_API_KEY="your_key_here"
```

```python
from gridirondata import GridIron

gi = GridIron()  # or GridIron(api_key="...")

# Free plan: player lookups and this week's projections
josh = gi.find_player("Josh Allen", "QB")
week = gi.projections(7, position="WR", scoring="half")
for p in week["projections"][:5]:
    print(p["player_name"], p["projected_points"])

# Basic and up
pid = gi.player_id("Malik Nabers", "WR")          # "Malik Nabers#WR"
gi.stats(pid, 2025)
gi.snaps(pid, trend=3)
gi.deviations(pid)
gi.matchups(pid, "DAL")
gi.news(pid, impact="injury")
gi.latest_news(impact="injury,role", position="RB")

# Pro: poll game-day injury status changes
feed = gi.injury_changes(since="2026-10-18T16:00:00Z")

# Pro: probability Drake Maye throws for 246+ yards, and the softest WR defenses
gi.prop_probability("pass_yds", 245.5, player_id="Drake Maye#QB")["lines"][0]["p_over"]
gi.defense_vs_position("WR", last=4)["defenses"][:5]
next_since = feed["next_since"]
```

## Methods

| Method | Route | Plan |
|---|---|---|
| `health()` | `GET /health` | any |
| `player(player_id, season=None)` | `GET /players/{player_id}` | Free (profile only) |
| `find_player(name, position)` | `GET /players?name=&position=` | Free |
| `search_players(name, position=, team=, limit=)` | `GET /players/search` | Free |
| `list_players(position=, limit=, cursor=)` / `iter_players()` | `GET /players` | Free |
| `projections(week, position=, season=, scoring=, team=, source=)` | `GET /projections/{week}` | Free: this week only; Basic: any week; Pro adds `projected_stats` |
| `stats(player_id, season, scoring="ppr")` | `GET /stats/{player_id}/{season}` | Basic |
| `snaps(player_id, season=, trend=)` | `GET /snaps/{player_id}` | Basic |
| `deviations(player_id, season=)` | `GET /deviations/{player_id}` | Basic |
| `matchups(player_id, opponent)` | `GET /matchups/{player_id}/{opponent}` | Basic |
| `news(player_id, days=7, limit=10, impact=)` | `GET /news/{player_id}` | Basic |
| `latest_news(since=, days=, impact=, position=, team=, limit=25, cursor=)` | `GET /news/latest` | Basic |
| `request_news_scrape(player_id)` | `POST /news/{player_id}/request` | Basic |
| `ros_projections(position=, team=, player_id=, scoring=)` | `GET /projections/ros` | Basic |
| `schedule(season=, week=, team=)` | `GET /schedule` | Basic; Pro adds each game's `odds` |
| `byes(season=)` | `GET /byes` | Basic |
| `player_by_espn_id(espn_id)` | `GET /players?espn_id=` | Free |
| `injury_changes(since=, limit=, position=, team=, season=)` | `GET /injuries/changes` | Pro |
| `prop_probability(stat, line, player_id=, espn_id=, week=)` | `GET /props` | Pro |
| `defense_vs_position(position=, last=, team=, scoring=)` | `GET /defense` | Pro |

Every method returns the API's JSON as a `dict`. Field reference:
<https://www.gridirondata.com/pages/docs.html>.

## Errors

Every HTTP error raises a subclass of `gridirondata.APIError` (which is also a
`requests.HTTPError`). `str(err)` and `err.message` carry the API's own
explanation; `err.reason`, `err.upgrade_url` and `err.body` hold the rest.

| Exception | When |
|---|---|
| `AuthenticationError` | 401: key missing or not recognised |
| `PermissionDeniedError` | 403: your plan doesn't include the route (`reason` is `basic_required` or `pro_required`, `upgrade_url` links to pricing, `required_plan` names the plan), or the subscription is paused, canceled or has a failed payment |
| `RateLimitError` | 429: `reason` is `rate_limited` (wait `retry_after` seconds), or `quota_exceeded` / `free_monthly_quota` (`resets_at` is the 1st of next month, 00:00 UTC) |
| `NotFoundError` | 404: no such player, season or opponent |
| `BadRequestError` | 400: an invalid parameter |
| `ServerError` | 5xx after retries |
| `GridIronConnectionError` | network failure or timeout after retries |
| `ConfigurationError` | no API key given |

```python
from gridirondata import GridIron, PermissionDeniedError, RateLimitError

gi = GridIron()
try:
    gi.stats("Josh Allen#QB", 2025)
except PermissionDeniedError as e:
    print(e.message)        # "Player stats is not included in the free plan. ..."
    print(e.upgrade_url)
except RateLimitError as e:
    print(e.reason, e.retry_after, e.resets_at)
```

## Timeouts and retries

```python
GridIron(timeout=15, max_retries=2, backoff=0.5)
```

Each request times out after `timeout` seconds. GET requests that fail with a
500/502/503/504 or a connection error are retried `max_retries` times, waiting
`backoff`, then `2 * backoff`, ... seconds. 429s are never retried (that would
only spend more of your month), and neither is `request_news_scrape` (a POST).

`GRIDIRON_API_BASE_URL` overrides the base URL (default
`https://api.gridirondata.com/api/v1`).

## Development

```bash
pip install -e clients/python pytest
python -m pytest tests/unit/test_python_client.py -q   # mocked HTTP; never calls the API
```

## License

MIT
