"""Public calendars for upcoming events the user can attend in Denizli."""

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
import html
import json
import re
import unicodedata
from urllib.parse import urlsplit, urlencode
from urllib.request import Request, urlopen

from scripts.veri_topla import MasterEvent, TURKEY_TZ, http_get, slugify

ASKF = "https://denizliaskf.org.tr/takim/denizlispor"
TVF = "https://tvf.org.tr/lig-fikstur/kadinlar-1-ligi"
MUNICIPAL = "https://denizli.bel.tr/etkinlikler"
TICKETS = "https://iticket.com.tr/etkinlikler/tumu/denizli"
CITY_TYPES = {
    "festival": "Festival", "konser": "Konser", "tiyatro": "Tiyatro",
    "sergi": "Sergi", "soylesi": "Söyleşi", "sinema": "Sinema",
    "MusicEvent": "Konser", "TheaterEvent": "Tiyatro", "Festival": "Festival",
    "ComedyEvent": "Stand-up",
}


def name_key(name):
    decomposed = unicodedata.normalize("NFKD", name)
    return slugify("".join(char for char in decomposed if not unicodedata.combining(char)))


def text(fragment):
    return " ".join(html.unescape(re.sub(r"<[^>]*>", "", fragment)).split())


def near(now, start, days=45):
    return now.date() <= start.date() <= now.date() + timedelta(days=days)


def event(identity, sport, title, start, competition, source, *, stage="", venue="", status="SCHEDULED", has_time=True):
    return MasterEvent(
        identity, sport, title, start.date().isoformat(), start.strftime("%H:%M"), has_time,
        competition, stage, venue, status, source,
    )


def parse_denizlispor(page, now):
    rows = re.findall(r'<article\b([^>]*class="club-match-row[^"]*"[^>]*)>(.*?)</article>', page, re.S)
    if not rows or 'id="clubMatchList"' not in page:
        raise ValueError("Denizlispor senior fixture structure missing")
    fixtures = []
    for attrs, row in rows:
        if 'data-state="upcoming"' not in attrs or 'data-venue="home"' not in attrs:
            continue
        stamp = re.search(r'\bdata-time="(\d+)"', attrs)
        if not stamp or int(stamp[1]) == 0:
            continue
        seconds = int(stamp[1])
        if seconds > 100_000_000_000:
            seconds /= 1000
        start = datetime.fromtimestamp(seconds, TURKEY_TZ)
        teams = [
            text(team) for team in re.findall(
                r'<div\s+class="club-match-team\s[^"]*"[^>]*>.*?<span>(.*?)</span>', row, re.S
            )
        ]
        match = re.search(r'href="/mac-detay\.php\?id=(\d+)"', row)
        info = re.search(r'<div class="club-match-context"><span>(.*?)</span><span>(.*?)</span>', row, re.S)
        if len(teams) != 2 or name_key(teams[0]) != "DENIZLISPOR" or not match or not info:
            continue
        if not near(now, start):
            continue
        ground = re.search(r'bi-geo-alt[^>]*></i>\s*([^<]+)', row)
        venue = text(ground[1]) if ground else ""
        if venue == "Saha henüz açıklanmadı":
            venue = ""
        confirmed = bool(start.hour or start.minute)
        fixtures.append(event(
            f"DENIZLISPOR_ASKF_{match[1]}", "Denizli Futbol", f"Denizlispor - {teams[1]}",
            start, text(info[2]), f"https://denizliaskf.org.tr/mac-detay.php?id={match[1]}",
            stage=text(info[1]), venue=venue, status="SCHEDULED" if confirmed else "DATE_TENTATIVE",
            has_time=confirmed,
        ))
    return fixtures


def fetch_football(now):
    return parse_denizlispor(http_get(ASKF), now)


def parse_volleyball(page, now):
    for encoded in re.findall(r'wire:snapshot="([^"]+)"', page):
        data = json.loads(html.unescape(encoded))["data"]
        if "leagueMatches" in data:
            matches = data["leagueMatches"][0]
            break
    else:
        raise ValueError("TVF fixture structure missing")
    fixtures = []
    for raw, _ in (matches.values() if isinstance(matches, dict) else matches):
        if (not slugify(raw["homeTeamName"]).startswith("DENIZLI_BUYUKSEHIR")
                or slugify(raw["hallCity"] or "") != "DENIZLI" or raw["isMatchEnded"]):
            continue
        start = datetime.fromisoformat(raw["date"][0]).astimezone(TURKEY_TZ)
        if not near(now, start):
            continue
        confirmed = bool(start.hour or start.minute)
        status = "POSTPONED" if raw["isPostponed"] else "SCHEDULED" if confirmed else "DATE_TENTATIVE"
        fixtures.append(event(
            f"DENIZLI_TVF_{raw['matchId']}", "Denizli Voleybol",
            f"{raw['homeTeamName']} - {raw['guestTeamName']}", start, "Kadınlar Voleybol 1. Ligi", TVF,
            stage="Voleybol", venue=raw["hallName"], status=status, has_time=confirmed,
        ))
    return fixtures


def fetch_volleyball(now):
    return parse_volleyball(http_get(TVF), now)


def jsonld(page):
    for block in re.findall(r'<script\b[^>]*type="application/ld\+json"[^>]*>(.*?)</script>', page, re.S | re.I):
        data = json.loads(block)
        if isinstance(data, dict):
            yield from data.get("@graph", [data])


def public_link(url, host):
    parsed = urlsplit(url)
    return parsed.scheme == "https" and parsed.hostname == host


def fetch_tickets(now):
    listing = next((doc for doc in jsonld(http_get(TICKETS)) if doc.get("@type") == "ItemList"), None)
    if listing is None:
        raise ValueError("Denizli ticket listing structure missing")
    urls = list(dict.fromkeys(
        item["url"] for item in listing["itemListElement"]
        if public_link(item.get("url", ""), "iticket.com.tr") and "/denizli/" in item["url"]
    ))[:16]

    def fetch_detail(url):
        fixtures = []
        for data in jsonld(http_get(url)):
            category = CITY_TYPES.get(data.get("@type")) if isinstance(data.get("@type"), str) else None
            if not category or not data.get("startDate"):
                continue
            place = data.get("location") or {}
            address = place.get("address") or {}
            if not isinstance(address, dict) or slugify(address.get("addressLocality") or "") != "DENIZLI":
                continue
            start = datetime.fromisoformat(data["startDate"]).astimezone(TURKEY_TZ)
            if not near(now, start, 30):
                continue
            status = data.get("eventStatus", "EventScheduled").rsplit("/", 1)[-1]
            if status not in ("EventScheduled", "EventRescheduled"):
                continue
            name = data["name"]
            identity = f"DENIZLI_CITY_{slugify(name)}_{start:%Y%m%d%H%M}"
            fixtures.append(event(identity, "Denizli Etkinlik", name, start, category, url, venue=place.get("name", "")))
        return fixtures

    with ThreadPoolExecutor(max_workers=3) as pool:
        return [fixture for batch in pool.map(fetch_detail, urls) for fixture in batch]


def fetch_municipal(now):
    # The city's public events page uses this read-only client header. Read it at runtime.
    config = http_get("https://denizli.bel.tr/js/api-config.js")
    key = re.search(r'ONE_KEY\s*=[^;]*"([a-f0-9]{32})"', config)
    if not key:
        raise ValueError("Public calendar client config changed")
    rows = {}
    for category in ("etkinlikler", "etkinlikler-one-cikan"):
        for page in range(1, 5):
            query = urlencode({"kategori": category, "sayfaBoyutu": 30, "sayfa": page})
            url = "https://web1.denizli.bel.tr/api/v1/icerikler?" + query
            with urlopen(Request(url, headers={"X-Api-Key": key[1]}), timeout=10) as response:
                payload = json.load(response)
            for row in payload["items"]:
                rows[row["id"]] = row
            if payload["toplam"] <= page * payload["sayfaBoyutu"]:
                break
        else:
            raise ValueError("Incomplete municipal event listing")

    fixtures = []
    for row in rows.values():
        if not row.get("etkinlikTarihi"):
            continue
        start = datetime.fromisoformat(row["etkinlikTarihi"]).astimezone(TURKEY_TZ)
        if not near(now, start, 30):
            continue
        settings = json.loads(row.get("ayarlarJson") or "{}")
        tags = row.get("etiketler") or []
        category = next((CITY_TYPES[t["slug"]] for t in tags if t.get("slug") in CITY_TYPES), "Kültür ve sanat")
        venue = settings.get("yer") or ""
        price = settings.get("fiyat") or []
        stage = "Ücretsiz giriş" if "ucretsiz" in price else "Ücretli giriş" if "ucretli" in price else ""
        name = row["baslik"]
        source = MUNICIPAL
        fixtures.append(event(
            f"DENIZLI_CITY_{slugify(name)}_{start:%Y%m%d%H%M}", "Denizli Etkinlik",
            name, start, category, source, stage=stage, venue=venue,
            has_time=bool(start.hour or start.minute),
        ))
    return fixtures
