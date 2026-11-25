"""World-title main events independently confirmed by the event promoter."""

from datetime import datetime
import json
from pathlib import Path
import re

from scripts.denizli_fixtures import event, name_key, near, text
from scripts.veri_topla import TURKEY_TZ, http_get

WBC = "https://wbcboxing.com/en/events/"
CONFIRMATIONS = Path(__file__).resolve().parents[1] / "data" / "boxing_main_events.json"


def parse_wbc(page, now, confirmations):
    cards = re.findall(r'<article class="cartel">(.*?)</article>', page, re.S)
    if not cards and 'class="cartel' not in page:
        raise ValueError("WBC calendar structure missing")
    fixtures = []
    for card in cards:
        date = re.search(r'<time[^>]*datetime="(\d{4}-\d{2}-\d{2})"', card)
        link = re.search(r'<a href="([^"]+)" class="cartel__link"', card)
        sides = re.findall(r'class="cartel__boxeador">\s*([^<]+)', card)
        venue = re.search(r'class="cartel__lugar">([^<]+)', card)
        if not date or not link or len(sides) != 2:
            continue
        confirmation = confirmations.get(link[1])
        if not confirmation or [name_key(text(name)) for name in sides] != [
            name_key(name) for name in confirmation["fighters"]
        ]:
            continue
        start = datetime.fromisoformat(date[1]).replace(tzinfo=TURKEY_TZ)
        if not near(now, start, 90):
            continue
        title = " - ".join(confirmation["fighters"])
        fixtures.append(event(
            "BOXING_" + link[1].rstrip("/").rsplit("/", 1)[-1],
            "Boks", title, start, confirmation["competition"], confirmation["promoter"],
            stage="Ana maç", venue=text(venue[1]) if venue else "", has_time=False,
        ))
    return fixtures


def fetch_boxing(now):
    confirmations = json.loads(CONFIRMATIONS.read_text(encoding="utf-8"))
    return parse_wbc(http_get(WBC), now, confirmations)
