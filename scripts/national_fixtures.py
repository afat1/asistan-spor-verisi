"""Senior national team entries from official schedules and announcements."""

from datetime import datetime, timezone
import json
import re

from scripts.denizli_fixtures import event, near, text
from scripts.veri_topla import TURKEY_TZ, http_get

FIBA = "https://www.fiba.basketball/en/events/fiba-basketball-world-cup-2027-european-qualifiers"
TVF = "https://tvf.org.tr/icerik/filenin-sultanlarinin-dunya-kupasindaki-rakipleri-belli-oldu"
FIVB = "https://www.fivb.com/2027-fivb-womens-volleyball-world-cup-pools-revealed-during-global-draw/"
COUNTRY = {
    "TUR": "Türkiye", "ITA": "İtalya", "ISL": "İzlanda", "LTU": "Litvanya",
    "SRB": "Sırbistan", "BIH": "Bosna Hersek",
}


def parse_fiba(page, now):
    fixtures = {}
    found_schedule = False
    decoder = json.JSONDecoder()
    for chunk in re.findall(r'self\.__next_f\.push\(\[1,("(?:\\.|[^"\\])*")\]\)', page):
        data = json.loads(chunk)
        for match in re.finditer(r'\{"gameId":\d+,"gameName":', data):
            row, _ = decoder.raw_decode(data, match.start())
            competition = row.get("competition") or {}
            if competition.get("competitionId") != 208943:
                continue
            found_schedule = True
            sides = (row.get("teamA") or {}, row.get("teamB") or {})
            codes = tuple(side.get("code") for side in sides)
            if not all(codes) or "TUR" not in codes:
                continue
            day = row.get("gameDateTime")
            if not day:
                continue
            has_time = bool(row.get("hasTimeGameDateTime") and row.get("gameDateTimeUTC"))
            if has_time:
                start = datetime.fromisoformat(row["gameDateTimeUTC"])
                if start.tzinfo is None:
                    start = start.replace(tzinfo=timezone.utc)
                start = start.astimezone(TURKEY_TZ)
            else:
                start = datetime.fromisoformat(day[:10]).replace(tzinfo=TURKEY_TZ)
            if not near(now, start, 180):
                continue
            names = [COUNTRY.get(code, side["officialName"]) for code, side in zip(codes, sides)]
            status = "POSTPONED" if row.get("isPostponed") else "COMPLETED" if row["statusCode"] == "VALID" else "SCHEDULED"
            match_id = row["gameId"]
            fixtures[match_id] = event(
                f"MILLI_BASKET_{match_id}", "Basketbol", " - ".join(names), start,
                "FIBA 2027 Dünya Kupası Elemeleri", f"{FIBA}/games/{match_id}-{'-'.join(codes)}",
                stage="A Erkek Millî Basketbol", venue=row.get("venueName") or "", status=status, has_time=has_time,
            )
    if not found_schedule:
        raise ValueError("FIBA senior qualifier schedule structure missing")
    return list(fixtures.values())


def fetch_national_basketball(now):
    return parse_fiba(http_get(FIBA + "/games"), now)


def parse_volleyball_draw(page, now):
    announcement = text(page)
    date = re.search(r"Pool Play:\s*August\s+(\d{1,2})\s*[–—-]\s*(\d{1,2}),\s*(2027)", announcement, re.I)
    group = re.search(r"Pool\s+([A-H]):\s*T[üu]rkiye,\s*Dominican Republic,\s*France,\s*Egypt", announcement, re.I)
    if not date or not group:
        raise ValueError("FIVB pool dates or Türkiye opponents changed")
    start = datetime(int(date[3]), 8, int(date[1]), tzinfo=TURKEY_TZ)
    if start.date() < now.date():
        return []
    return [event(
        "MILLI_VOLEYBOL_WWC2027_POOL", "Türkiye Voleybol", "Filenin Sultanları · Dünya Kupası grup aşaması",
        start, "FIVB 2027 Kadınlar Voleybol Dünya Kupası", TVF,
        stage=f"{date[1]}–{date[2]} Ağustos · {group[1]} Grubu · Rakipler Dominik Cumhuriyeti, Fransa ve Mısır",
        venue="Centre Vidéotron, Québec", has_time=False,
    )]


def fetch_national_volleyball(now):
    return parse_volleyball_draw(http_get(FIVB), now)
