"""Public championship schedules and verified fight-night announcements."""

from datetime import datetime
import json
from pathlib import Path
import re
from urllib.parse import urljoin

from scripts.denizli_fixtures import event, name_key, near, text
from scripts.veri_topla import TURKEY_TZ, http_get

WBA = "https://www.wbaboxing.com/wba-fights-schedule"
WBO = "https://wboboxing.com/"
TURKISH_MMA = "https://mmafederasyonu.org.tr/mma/Organizasyonlar?turu=pro"
DATA = Path(__file__).resolve().parents[1] / "data"
PRICE_BOUT = "BOXING_lauren-price-vs-michaela-kotaskova"
PRICE_SOURCE = "https://www.boxxer.com/events/lauren-price-vs-michaela-kotaskova-20-november-2026-utilita-arena-cardiff/"


def boxing_id(names):
    return "BOXING_" + "_VS_".join(sorted(name_key(name) for name in names))


def parse_wba(page, now):
    records = []
    fixtures = []
    for block in re.findall(r'<script\b[^>]*type="application/ld\+json"[^>]*>(.*?)</script>', page, re.S | re.I):
        try:
            record = json.loads(block)
        except json.JSONDecodeError:
            # Some unrelated WordPress metadata is emitted as malformed JSON.
            if '"startDate"' in block:
                raise
            continue
        if isinstance(record, dict) and record.get("@type") == "Event" and record.get("startDate"):
            records.append(record)
    if not records and "WBA Fights Schedule" not in page:
        raise ValueError("WBA calendar structure missing")

    for record in records:
        sides, separator, belt = record["name"].partition(" - ")
        title_belt = re.split(r"<br\s*/?>", belt, maxsplit=1, flags=re.I)[0].strip()
        if not separator or not re.fullmatch(
            r"(?:UNIFICATION |VACANT )?WBA World(?:\s*/\s*(?:WBC|WBO|IBF|IBO))*", title_belt, re.I
        ):
            continue
        names = tuple(text(name).title() for name in re.split(r"\s+vs\.?\s+", sides, maxsplit=1, flags=re.I))
        if len(names) != 2 or any(not name for name in names):
            continue
        start = datetime.fromisoformat(record["startDate"]).replace(tzinfo=TURKEY_TZ)
        if not near(now, start, 90):
            continue
        bodies = re.findall(r"\b(?:WBA|WBC|IBF|WBO|IBO)\b", title_belt.upper())
        identity, url, stage = boxing_id(names), WBA, "Dünya kemeri"
        if {name_key(name) for name in names} == {"LAUREN_PRICE", "MICHAELA_KOTASKOVA"}:
            names = ("Lauren Price", "Michaela Kotásková")
            identity, url, stage = PRICE_BOUT, PRICE_SOURCE, "Ana maç"
            bodies = ["WBC", "WBA", "IBF"]
        elif {name_key(name) for name in names} == {"GABRIELA_FUNDORA", "JASMINA_ZAPOTOCZNA"}:
            stage = "Yan ana maç"
        fixtures.append(event(
            identity, "Boks", " - ".join(names), start, " / ".join(bodies) + " Dünya Şampiyonluğu",
            url, stage=stage, venue=record.get("location", {}).get("name", ""), has_time=False,
        ))
    return fixtures


def parse_wbo(page, now):
    cards = re.findall(r'upcoming-fights-cards-bg">(.*?)<!-- End Slide -->', page, re.S)
    if not cards:
        raise ValueError("WBO calendar structure missing")
    fixtures = []
    for card in cards:
        if not re.search(r">\s*Championship\s*</span>", card, re.I):
            continue
        sides = re.findall(
            r'class="name d-block">([^<]+)</span>\s*<span class="last-name d-block">([^<]+)</span>', card
        )
        details = re.findall(r'class="text-danger fw-bold">([^<]+)</span>', card)
        if not details:
            continue
        date, *place = details
        if len(sides) != 2 or not re.fullmatch(r"[A-Za-z]+ \d{1,2}(?:, \d{4})?", text(date)):
            continue
        date = text(date)
        value = date if "," in date else f"{date}, {now.year}"
        start = datetime.strptime(value, "%B %d, %Y").replace(tzinfo=TURKEY_TZ)
        if not near(now, start, 90):
            continue
        names = tuple(text(first + " " + last) for first, last in sides)
        url, stage = WBO, "Dünya kemeri"
        if {name_key(name) for name in names} == {"ANTHONY_OLASCUAGA", "YUBERJEN_MARTINEZ"}:
            names = ("Anthony Olascuaga", "Yuberjen Martínez")
            url = "https://www.matchroomboxing.com/news/olascuaga-pens-multi-fight-deal-with-matchroom-and-defends-world-title-against-two-time-olympian-yuberjen-martinez-on-nov-14-in-carson-ca/"
            stage = "Yan ana maç"
        fixtures.append(event(
            boxing_id(names), "Boks", " - ".join(names), start, "WBO Dünya Şampiyonluğu", url,
            stage=stage, venue=text(place[0]).lstrip("| ") if place else "", has_time=False,
        ))
    return fixtures


def fetch_wba(now):
    return parse_wba(http_get(WBA), now)


def fetch_wbo(now):
    return parse_wbo(http_get(WBO), now)


def fetch_featured(now):
    records = json.loads((DATA / "featured_combat_events.json").read_text(encoding="utf-8"))
    fixtures = []
    for record in records:
        stamp = record["start"]
        start = datetime.fromisoformat(stamp).replace(tzinfo=TURKEY_TZ)
        if not near(now, start, 90):
            continue
        fighters = record.get("fighters")
        fixture = event(
            record["id"], record["sport"], " - ".join(fighters) if fighters else record["title"],
            start, record["competition"], record["source"],
            stage="Ana maç" if fighters else record["stage"],
            venue=record["venue"], has_time="T" in stamp,
        )
        fixtures.append(fixture)
    return fixtures


def parse_turkish_mma(page, now):
    if "mmaorganizasyon" not in page:
        raise ValueError("Turkish MMA calendar structure missing")
    fixtures = []
    for path, tile in re.findall(r'<a href="(/Mma/Organizasyon-Detay/[^"]+)">(.*?)</a>', page, re.S | re.I):
        heading = re.search(r'<h6\b[^>]*>(.*?)</h6>', tile, re.S)
        stamp = re.search(r'data-date="([^"]+)"', tile)
        if not heading or not stamp:
            continue
        name = text(heading[1])
        key = name_key(name)
        if not re.search(r"(?:^|_)(?:TURKIYE|ANKARA|ISTANBUL|IZMIR|ANTALYA|BURSA|DENIZLI)(?:_|$)", key):
            continue
        if "VENDETTA" in key:
            competition = "Vendetta Fight Nights"
        elif re.search(r"(?:^|_)(?:CORE|CFC)(?:_|$)", key):
            competition = "Core CFC"
        else:
            continue
        start = datetime.strptime(text(stamp[1]), "%m %d, %Y %H:%M:%S").replace(tzinfo=TURKEY_TZ)
        if near(now, start, 90):
            fixtures.append(event(
                "TURKEY_MMA_" + path.rsplit("/", 1)[-1], "MMA", name, start, competition,
                urljoin(TURKISH_MMA, path), stage="Türkiye · Profesyonel MMA",
            ))
    return fixtures


def fetch_turkish_mma(now):
    return parse_turkish_mma(http_get(TURKISH_MMA), now)
