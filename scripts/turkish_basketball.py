"""Fixture adapters for Beşiktaş and Denizli men's basketball."""

from datetime import datetime, timedelta
from html.parser import HTMLParser
import json
import re

from scripts.veri_topla import MasterEvent, TURKEY_TZ, http_get, slugify


def team_key(name):
    slug = slugify(name)
    if "BESIKTAS" in slug:
        return "BESIKTAS"
    if "DENIZLI" in slug or "MERKEZEFENDI" in slug:
        return "DENIZLI"
    if "FENERBAHCE" in slug:
        return "FENERBAHCE"
    return slug


class LeagueSchedule(HTMLParser):
    def __init__(self):
        super().__init__()
        self.rows = []
        self.week = ""
        self.current = None
        self.capture = None

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        classes = attrs.get("class", "").split()
        if tag == "div" and "data-week-name" in attrs:
            self.week = attrs["data-week-name"].split(".", 1)[0]
        if tag == "div" and "citem" in classes:
            self.current = {"week": self.week, "teamName": [], "citem01A": [], "score": []}
            self.rows.append(self.current)
        if self.current is not None and tag in ("span", "h3"):
            for field in ("teamName", "citem01A", "score"):
                if field in classes:
                    self.capture = (tag, field)
                    self.current[field].append("")
                    break

    def handle_data(self, data):
        if self.capture is not None:
            self.current[self.capture[1]][-1] += data

    def handle_endtag(self, tag):
        if self.capture is not None and tag == self.capture[0]:
            self.capture = None


def parse_bsl(content, season, source_url, now):
    page = LeagueSchedule()
    page.feed(content)
    fixtures = []
    for row in page.rows:
        if len(row["teamName"]) != 2 or not row["week"].isdigit():
            continue
        home, away = map(str.strip, row["teamName"])
        home_id, away_id = team_key(home), team_key(away)
        if not {"BESIKTAS", "DENIZLI"}.intersection((home_id, away_id)):
            continue
        if len(row["citem01A"]) != 1:
            continue
        try:
            start = datetime.strptime(row["citem01A"][0].strip(), "%d.%m.%Y-%H:%M").replace(tzinfo=TURKEY_TZ)
        except ValueError:
            continue
        has_time = bool(start.hour or start.minute)
        played = any(re.search(r"\d+\s*-\s*\d+", value) for value in row["score"])
        if start.date() < now.date() - timedelta(days=1):
            continue
        fixtures.append(MasterEvent(
            event_id=f"BSL_{season}_{row['week']}_{home_id}_{away_id}",
            sport="Basketbol", tournament="Basketbol Süper Ligi",
            title=f"{home} - {away}", date_str=start.date().isoformat(),
            time_str=start.strftime("%H:%M") if has_time else None, has_time=has_time,
            session_or_stage=f"{row['week']}. Hafta",
            status="COMPLETED" if played else "SCHEDULED" if has_time else "DATE_TENTATIVE",
            source_url=source_url,
        ))
    if not page.rows or not any(
        {"BESIKTAS", "DENIZLI"}.intersection(team_key(team) for team in row["teamName"])
        for row in page.rows
    ):
        raise ValueError("BSL fixture structure missing")
    return fixtures


def basketball_season(now):
    return now.year if now.month >= 7 else now.year - 1


def fetch_bsl(now):
    season = basketball_season(now)
    url = f"https://www.turktelekombasketbol.com.tr/turkiye-sigorta-basketbol-super-ligi-{season}-{(season + 1) % 100:02}"
    return parse_bsl(http_get(url), season, url, now)


def parse_euroleague(payload, now):
    games = payload["data"]
    if not games or payload.get("total", len(games)) != len(games):
        raise ValueError("Incomplete EuroLeague schedule")
    fixtures = []
    for game in games:
        home, away = (game[side]["club"] for side in ("local", "road"))
        if not any(team_key(team["name"]) == "BESIKTAS" for team in (home, away)):
            continue
        if not game.get("utcDate"):
            continue
        start = datetime.fromisoformat(game["utcDate"]).astimezone(TURKEY_TZ)
        if start.date() < now.date() - timedelta(days=1):
            continue
        has_time = bool(game.get("confirmedDate") and game.get("confirmedHour"))
        status = game["gameStatus"].upper()
        if status == "CANCELED":
            status = "CANCELLED"
        if status not in ("CANCELLED", "POSTPONED"):
            status = "COMPLETED" if game.get("played") else "SCHEDULED" if has_time else "DATE_TENTATIVE"
        season = game["season"]["code"]
        venue = (game.get("venue") or {}).get("name")
        fixtures.append(MasterEvent(
            event_id=f"EL_{game['identifier']}", sport="Basketbol", tournament="EuroLeague",
            title=f"{home['name']} - {away['name']}", date_str=start.date().isoformat(),
            time_str=start.strftime("%H:%M") if has_time else None, has_time=has_time,
            session_or_stage=" • ".join(filter(None, [f"{game['round']}. Hafta", venue])),
            status=status,
            source_url=f"https://www.euroleaguebasketball.net/euroleague/game-center/{season}/{game['gameCode']}/",
        ))
    return fixtures


def fetch_euroleague(now):
    season = basketball_season(now)
    url = f"https://api-live.euroleague.net/v2/competitions/E/seasons/E{season}/games"
    return parse_euroleague(json.loads(http_get(url)), now)
