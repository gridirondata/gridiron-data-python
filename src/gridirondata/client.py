"""The GridIron Data API client."""

from __future__ import annotations

import datetime
import os
import time
from typing import Any, Callable, Dict, Iterator, Optional, Sequence, Union
from urllib.parse import quote

import requests

from . import __version__
from .exceptions import (
    APIError,
    AuthenticationError,
    BadRequestError,
    ConfigurationError,
    GridIronConnectionError,
    NotFoundError,
    PermissionDeniedError,
    RateLimitError,
    ServerError,
)

DEFAULT_BASE_URL = "https://api.gridirondata.com/api/v1"
SIGNUP_URL = "https://www.gridirondata.com/pages/signup.html"
RETRY_STATUSES = frozenset({500, 502, 503, 504})
QUOTA_REASONS = frozenset({"quota_exceeded", "free_monthly_quota"})

JSON = Dict[str, Any]
Week = Union[int, str]
Season = Union[int, str]
Since = Union[str, int, float, datetime.datetime]


def _player_path(player_id: str) -> str:
    """URL-encode a player_id ("Josh Allen#QB" -> "Josh%20Allen%23QB")."""
    return quote(str(player_id), safe="")


def _clean(params: Dict[str, Any]) -> Dict[str, Any]:
    """Drop unset (None) query parameters; stringify the rest."""
    out = {}
    for key, value in params.items():
        if value is None:
            continue
        if isinstance(value, bool):
            value = str(value).lower()
        out[key] = value
    return out


def _since_param(since: Optional[Since]) -> Optional[Union[str, int, float]]:
    if isinstance(since, datetime.datetime):
        if since.tzinfo is None:
            since = since.replace(tzinfo=datetime.timezone.utc)
        return since.astimezone(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    return since


def _next_month_utc(now: datetime.datetime) -> datetime.datetime:
    """Monthly quotas reset on the 1st at 00:00 UTC."""
    year, month = (now.year + 1, 1) if now.month == 12 else (now.year, now.month + 1)
    return datetime.datetime(year, month, 1, tzinfo=datetime.timezone.utc)


class GridIron:
    """Client for the GridIron Data fantasy football API.

    >>> from gridirondata import GridIron
    >>> gi = GridIron()                     # reads GRIDIRON_API_KEY
    >>> gi.projections(7, position="WR")

    Every method returns the API's JSON as a dict and raises a subclass of
    :class:`gridirondata.APIError` on an HTTP error. Requests time out after
    ``timeout`` seconds; GET requests that fail with a 5xx or a connection
    error are retried ``max_retries`` times with exponential backoff. 429s are
    never retried (a retry would only spend more of the month).
    """

    def __init__(self, api_key: Optional[str] = None, *,
                 base_url: Optional[str] = None,
                 timeout: float = 15.0,
                 max_retries: int = 2,
                 backoff: float = 0.5,
                 session: Optional[requests.Session] = None,
                 sleep: Callable[[float], None] = time.sleep) -> None:
        self.api_key = api_key or os.environ.get("GRIDIRON_API_KEY")
        if not self.api_key:
            raise ConfigurationError(
                "No API key: pass GridIron(api_key=...) or set the GRIDIRON_API_KEY "
                f"environment variable. Get a free key at {SIGNUP_URL}")
        self.base_url = (base_url or os.environ.get("GRIDIRON_API_BASE_URL")
                         or DEFAULT_BASE_URL).rstrip("/")
        self.timeout = timeout
        self.max_retries = max(0, int(max_retries))
        self.backoff = backoff
        self._sleep = sleep
        self._session = session or requests.Session()
        self._session.headers.update({
            "x-api-key": self.api_key,
            "Accept": "application/json",
            "User-Agent": f"gridirondata-python/{__version__}",
        })

    def __repr__(self) -> str:
        return f"GridIron(base_url={self.base_url!r})"  # never print the key

    def __enter__(self) -> "GridIron":
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()

    def close(self) -> None:
        self._session.close()

    # -- transport -----------------------------------------------------------
    def request(self, method: str, path: str,
                params: Optional[Dict[str, Any]] = None) -> JSON:
        """Send a request to ``base_url + path`` and return the decoded JSON."""
        url = f"{self.base_url}/{path.lstrip('/')}"
        params = _clean(params or {})
        retryable = method.upper() == "GET"
        attempt = 0
        while True:
            try:
                response = self._session.request(method, url, params=params or None,
                                                 timeout=self.timeout)
            except (requests.ConnectionError, requests.Timeout) as exc:
                if retryable and attempt < self.max_retries:
                    self._sleep(self.backoff * (2 ** attempt))
                    attempt += 1
                    continue
                raise GridIronConnectionError(
                    f"Could not reach {self.base_url}: {exc}") from exc
            if (retryable and response.status_code in RETRY_STATUSES
                    and attempt < self.max_retries):
                self._sleep(self.backoff * (2 ** attempt))
                attempt += 1
                continue
            return self._handle(response)

    def _get(self, path: str, params: Optional[Dict[str, Any]] = None) -> JSON:
        return self.request("GET", path, params)

    @staticmethod
    def _handle(response: requests.Response) -> JSON:
        try:
            body = response.json()
        except ValueError:
            body = None
        status = response.status_code
        if status < 400:
            if not isinstance(body, dict):
                raise APIError("Response was not a JSON object",
                               status_code=status, response=response)
            return body
        data = body if isinstance(body, dict) else {}
        message = (data.get("error") or data.get("message")
                   or (response.text or "").strip() or f"HTTP {status}")
        kwargs = {"status_code": status, "body": data, "response": response}
        if status == 400:
            raise BadRequestError(message, **kwargs)
        if status == 401:
            raise AuthenticationError(
                f"{message}. Check your API key (x-api-key / GRIDIRON_API_KEY).", **kwargs)
        if status == 403:
            raise PermissionDeniedError(message, **kwargs)
        if status == 404:
            raise NotFoundError(message, **kwargs)
        if status == 429:
            retry_after = None
            header = response.headers.get("Retry-After")
            if header:
                try:
                    retry_after = float(header)
                except ValueError:
                    retry_after = None
            resets_at = None
            reason = data.get("reason")
            if reason in QUOTA_REASONS:
                resets_at = _next_month_utc(datetime.datetime.now(datetime.timezone.utc))
            elif retry_after is None:
                retry_after = 60.0  # per-minute limits: wait out the minute
            raise RateLimitError(message, retry_after=retry_after,
                                 resets_at=resets_at, **kwargs)
        if status >= 500:
            raise ServerError(message, **kwargs)
        raise APIError(message, **kwargs)

    # -- helpers -------------------------------------------------------------
    @staticmethod
    def player_id(name: str, position: str) -> str:
        """Build a player_id from a name and position: ("Malik Nabers", "WR")
        -> "Malik Nabers#WR"."""
        return f"{name}#{position.upper()}"

    # -- health --------------------------------------------------------------
    def health(self) -> JSON:
        """GET /health: status, version and the default season."""
        return self._get("/health")

    # -- players -------------------------------------------------------------
    def player(self, player_id: str, season: Optional[Season] = None) -> JSON:
        """GET /players/{player_id}: one player's full record (profile only on
        the free plan). ``season`` limits ``seasons`` to that year."""
        return self._get(f"/players/{_player_path(player_id)}", {"season": season})

    def player_by_espn_id(self, espn_id: int, season: Optional[Season] = None) -> JSON:
        """GET /players?espn_id=: the player with this ESPN athlete id (the
        numeric key sportsbook feeds use)."""
        return self._get("/players", {"espn_id": int(espn_id), "season": season})

    def find_player(self, name: str, position: str) -> JSON:
        """GET /players?name=&position=: exact name + position match."""
        return self._get("/players", {"name": name, "position": position})

    def search_players(self, name: Optional[str] = None, *,
                       position: Optional[str] = None, team: Optional[str] = None,
                       limit: Optional[int] = None) -> JSON:
        """GET /players/search: partial-name search, optional position/team."""
        return self._get("/players/search", {"name": name, "position": position,
                                             "team": team, "limit": limit})

    def list_players(self, position: Optional[str] = None, limit: int = 100,
                     cursor: Optional[str] = None) -> JSON:
        """GET /players: one page (limit 1-100). Pass ``next_cursor`` from the
        previous page as ``cursor`` to continue."""
        return self._get("/players", {"position": position, "limit": limit,
                                      "cursor": cursor})

    def iter_players(self, position: Optional[str] = None,
                     page_size: int = 100) -> Iterator[JSON]:
        """Yield every player, following ``next_cursor`` (one request per page)."""
        cursor = None
        while True:
            page = self.list_players(position=position, limit=page_size, cursor=cursor)
            yield from page.get("players", [])
            cursor = page.get("next_cursor")
            if not cursor:
                return

    # -- projections ---------------------------------------------------------
    def projections(self, week: Week, position: Optional[str] = None,
                    season: Optional[Season] = None, scoring: Optional[str] = None,
                    *, team: Optional[str] = None,
                    source: Optional[str] = None) -> JSON:
        """GET /projections/{week}. ``scoring`` is ppr (default), half or
        standard; ``source="weekly"`` drops season-prorated fallbacks. Pro
        keys also get ``projected_stats`` on each row."""
        return self._get(f"/projections/{week}", {
            "position": position, "season": season, "scoring": scoring,
            "team": team, "source": source})

    def ros_projections(self, position: Optional[str] = None, *,
                        team: Optional[str] = None, player_id: Optional[str] = None,
                        scoring: Optional[str] = None) -> JSON:
        """GET /projections/ros: each player's projected points over the rest of
        the current season (bye and played weeks skipped). The response's
        ``method`` says whether weekly projections or a per-game rate was used.
        Basic plan."""
        return self._get("/projections/ros", {
            "position": position, "team": team, "player_id": player_id,
            "scoring": scoring})

    # -- schedule and bye weeks (Basic+) ---------------------------------------
    def schedule(self, season: Optional[Season] = None, *, week: Optional[Week] = None,
                 team: Optional[str] = None) -> JSON:
        """GET /schedule: games with kickoff (UTC), status and final scores.
        With ``team`` the response also carries that team's ``bye_week``.
        Pro keys also get each game's ``odds`` (spread, total, moneylines,
        implied team points)."""
        return self._get("/schedule", {"season": season, "week": week, "team": team})

    def byes(self, season: Optional[Season] = None) -> JSON:
        """GET /byes: each team's bye week, plus ``by_week``."""
        return self._get("/byes", {"season": season})

    # -- stats, snaps, deviations, matchups (Basic+) ---------------------------
    def stats(self, player_id: str, season: Season, scoring: Optional[str] = "ppr") -> JSON:
        """GET /stats/{player_id}/{season}: weekly actuals plus season totals."""
        return self._get(f"/stats/{_player_path(player_id)}/{season}", {"scoring": scoring})

    def snaps(self, player_id: str, season: Optional[Season] = None,
              trend: Optional[int] = None) -> JSON:
        """GET /snaps/{player_id}: weekly snap share and a recent-vs-season
        trend over the last ``trend`` weeks."""
        return self._get(f"/snaps/{_player_path(player_id)}", {"season": season,
                                                              "trend": trend})

    def deviations(self, player_id: str, season: Optional[Season] = None) -> JSON:
        """GET /deviations/{player_id}: weekly actual vs projected (2025 on)."""
        return self._get(f"/deviations/{_player_path(player_id)}", {"season": season})

    def matchups(self, player_id: str, opponent: str) -> JSON:
        """GET /matchups/{player_id}/{opponent}: history vs one team."""
        return self._get(f"/matchups/{_player_path(player_id)}/{quote(opponent, safe='')}")

    # -- news (Basic+) ---------------------------------------------------------
    def news(self, player_id: str, days: Optional[int] = 7, limit: Optional[int] = 10,
             impact: Optional[str] = None) -> JSON:
        """GET /news/{player_id}: recent news for one player. ``impact``
        filters by injury, role, transaction, etc."""
        return self._get(f"/news/{_player_path(player_id)}", {
            "days": days, "limit": limit, "impact": impact})

    def latest_news(self, since: Optional[Since] = None, days: Optional[int] = None,
                    impact: Optional[str] = None, position: Optional[str] = None,
                    team: Optional[str] = None, limit: Optional[int] = 25,
                    cursor: Optional[str] = None) -> JSON:
        """GET /news/latest: league-wide feed, newest first. ``impact`` and
        ``position`` accept comma-separated lists."""
        return self._get("/news/latest", {
            "since": _since_param(since), "days": days, "impact": impact,
            "position": position, "team": team, "limit": limit, "cursor": cursor})

    def request_news_scrape(self, player_id: str) -> JSON:
        """POST /news/{player_id}/request: queue an on-demand scrape (202).
        Not retried automatically."""
        return self.request("POST", f"/news/{_player_path(player_id)}/request")

    # -- injury changes (Pro) --------------------------------------------------
    def injury_changes(self, since: Optional[Since] = None, *,
                       limit: Optional[int] = None, position: Optional[str] = None,
                       team: Optional[str] = None,
                       season: Optional[Season] = None) -> JSON:
        """GET /injuries/changes: status changes detected since ``since``
        (ISO-8601 string, unix seconds or a datetime). Pass the response's
        ``next_since`` back in to poll for only what is new. Pro plan."""
        return self._get("/injuries/changes", {
            "since": _since_param(since), "limit": limit, "position": position,
            "team": team, "season": season})

    # -- props and defense vs position (Pro) -------------------------------------
    def prop_probability(self, stat: str, line: Union[float, Sequence[float]], *,
                         player_id: Optional[str] = None, espn_id: Optional[int] = None,
                         week: Optional[Week] = None,
                         season: Optional[Season] = None) -> JSON:
        """GET /props: P(over/under) and fair odds for a player stat line,
        e.g. ``prop_probability("pass_yds", 245.5, player_id="Drake Maye#QB")``.
        ``line`` may be a list (up to 10). Pass ``player_id`` or ``espn_id``.
        Pro plan."""
        if player_id is None and espn_id is None:
            raise ValueError("pass player_id or espn_id")
        lines = ",".join(str(x) for x in line) if isinstance(line, (list, tuple)) else line
        return self._get("/props", {"stat": stat, "line": lines, "player_id": player_id,
                                    "espn_id": espn_id, "week": week, "season": season})

    def defense_vs_position(self, position: Optional[str] = None, *,
                            last: Optional[int] = None, team: Optional[str] = None,
                            scoring: Optional[str] = None,
                            season: Optional[Season] = None) -> JSON:
        """GET /defense: what each defense allows per game to QB/RB/WR/TE
        (fantasy points, yards, TDs, receptions), ranked; ``last`` = trailing
        weeks. Pro plan."""
        return self._get("/defense", {"position": position, "last": last, "team": team,
                                      "scoring": scoring, "season": season})
