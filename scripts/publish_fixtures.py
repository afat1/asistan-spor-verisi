"""Collect public fixtures and publish one versioned snapshot to Firestore Spark."""

import argparse
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from functools import partial
import hashlib
import json
import os
from pathlib import Path
import re
from urllib.error import HTTPError
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

from scripts import veri_topla as legacy
from scripts.boxing_fixtures import fetch_boxing
from scripts.combat_fixtures import DATA as COMBAT_DATA
from scripts.combat_fixtures import fetch_featured, fetch_turkish_mma, fetch_wba, fetch_wbo
from scripts.denizli_fixtures import fetch_football, fetch_municipal, fetch_tickets, fetch_volleyball, name_key
from scripts.national_fixtures import fetch_national_basketball, fetch_national_volleyball
from scripts.turkish_basketball import fetch_bsl, fetch_euroleague


FOOTBALL = {
    "besiktas": ("besiktas", "Süper Lig"),
    "galatasaray": ("galatasaray", "Süper Lig"),
    "fenerbahce": ("fenerbahce", "Süper Lig"),
    "trabzonspor": ("trabzonspor", "Süper Lig"),
    "turkiye": ("tr", "Türkiye Millî Takımı"),
    "turkey_cup": ("league/turkish-cup", "Türkiye Kupası"),
    "super_cup": ("league/super-kupa", "Süper Kupa"),
    "ucl": ("league/champions-league", "UEFA Şampiyonlar Ligi"),
    "uel": ("league/europa-league", "UEFA Avrupa Ligi"),
    "uecl": ("league/uefa-conference-league", "UEFA Konferans Ligi"),
}
EUROPEAN_TAGS = {
    "CL": "UEFA Şampiyonlar Ligi", "EL": "UEFA Avrupa Ligi", "ECL": "UEFA Konferans Ligi",
    "TC": "Türkiye Kupası",
}
FEATURED_CLUBS = {
    "REAL_MADRID", "MANCHESTER_CITY", "BAYERN", "BARCELONA", "PARIS_SAINT_GERMAIN",
    "PSG", "ARSENAL", "LIVERPOOL", "INTER", "MILAN", "JUVENTUS", "ATLETICO_MADRID",
    "BORUSSIA_DORTMUND", "CHELSEA", "BENFICA", "SPORTING", "PORTO",
}
TURKISH_CLUBS = {
    "BESIKTAS", "GALATASARAY", "FENERBAHCE", "TRABZONSPOR", "BASAKSEHIR", "SAMSUNSPOR",
    "GOZTEPE", "KONYASPOR", "GAZIANTEP", "ALANYASPOR", "RIZESPOR", "KASIMPASA",
    "EYUPSPOR", "GENCLERBIRLIGI", "ANTALYASPOR", "KAYSERISPOR", "KOCAELISPOR",
    "ERZURUMSPOR", "AMED", "CORUM", "BODRUM", "BURSASPOR", "SIVASSPOR", "ISTANBULSPOR",
}
NBA_CLUBS = {
    "LAKERS", "CELTICS", "WARRIORS", "NUGGETS", "BUCKS", "MAVERICKS", "KNICKS", "HEAT",
    "76ERS", "SUNS", "TIMBERWOLVES", "THUNDER", "CLIPPERS", "CAVALIERS",
}


def matches_team(title, names):
    words = f"_{legacy.slugify(title)}_"
    return any(f"_{name}_" in words for name in names)


def calendar_start(value):
    if not value:
        return None, False
    key, delimiter, stamp = value.partition(":")
    if not delimiter:
        stamp = key
        key = ""
    if re.fullmatch(r"\d{8}", stamp):
        return datetime.strptime(stamp, "%Y%m%d").replace(tzinfo=legacy.TURKEY_TZ), False
    if stamp.endswith("Z"):
        utc = datetime.strptime(stamp, "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc)
        return utc.astimezone(legacy.TURKEY_TZ), True
    zone = re.search(r'(?:^|;)TZID="?([^;"]+)', key, re.IGNORECASE)
    tz = ZoneInfo(zone.group(1)) if zone else legacy.TURKEY_TZ
    start = datetime.strptime(stamp, "%Y%m%dT%H%M%S").replace(tzinfo=tz)
    return start.astimezone(legacy.TURKEY_TZ), True


def ufc_main_event(item, title):
    announced = re.search(r":\s*(.+?)\s+vs\.?\s+(.+)$", title, re.IGNORECASE)
    main = re.search(r"^•\s+([^\n]+?)\s+vs\.?\s+([^\n]+)", item.get("DESCRIPTION", ""), re.MULTILINE)
    if not announced or not main:
        return None
    sides = tuple(re.split(r"\s+(?:\(|@)", name.strip(), maxsplit=1)[0] for name in main.groups())
    if all(
        name_key(name).endswith("_" + name_key(short))
        or name_key(name) == name_key(short)
        for name, short in zip(sides, announced.groups())
    ):
        return sides
    return None


def calendar_events(url, now, sport, tournament, select=lambda _: True, days=365):
    content = legacy.http_get(url)
    if "BEGIN:VCALENDAR" not in content:
        raise ValueError("Invalid iCalendar response")
    events = {}
    for item in legacy.parse_ics_events(content):
        raw_title = item.get("SUMMARY", "")
        if not raw_title or not select(raw_title):
            continue
        raw_start = item.get("_DTSTART_RAW", item.get("DTSTART", ""))
        start, has_time = calendar_start(raw_start)
        if not start or not now.date() - timedelta(days=1) <= start.date() <= now.date() + timedelta(days=days):
            continue
        score = re.search(r"\s+\(\d+\s*[-:]\s*\d+\)$", raw_title)
        title = raw_title[:score.start()].strip() if score else raw_title.strip()
        tags = re.findall(r"\[(CL|EL|ECL|TC)\]", title)
        league = EUROPEAN_TAGS.get(tags[-1], tournament) if tags else tournament
        title = re.sub(r"\s*\[(CL|EL|ECL|TC)\]", "", title).strip()
        if sport == "NBA":
            title = title.removeprefix("NBA: ").strip()
        stable_key = item.get("UID") or f"{title}:{start.date()}"
        suffix = hashlib.sha256(stable_key.encode("utf-8")).hexdigest()[:24]
        status = item.get("STATUS", "").upper()
        if status not in ("CANCELLED", "POSTPONED"):
            status = "COMPLETED" if score else "SCHEDULED"
        main = ufc_main_event(item, title) if sport == "UFC" else None
        stage = "Ana maç" if main else "Ana kart" if sport == "UFC" else ""
        source = None
        if sport == "UFC":
            official = re.search(
                r"https://(?:www\.)?ufc\.com/event/[a-z0-9-]+(?=$|[\s?/#])",
                " ".join((item.get("URL", ""), item.get("DESCRIPTION", ""))), re.I,
            )
            source = official[0] if official else "https://www.ufc.com/events"
        events[suffix] = legacy.MasterEvent(
            f"{sport.upper()}_{suffix}", sport, title, start.date().isoformat(),
            start.strftime("%H:%M") if has_time else None, has_time, league, stage,
            item.get("LOCATION", ""), status, source or url,
        )
        if main:
            events[suffix].main_event = main
    return list(events.values())


def football_source(now, key, route, tournament):
    def select(title):
        if key == "besiktas" or key == "turkiye":
            return True
        if key in ("turkey_cup", "super_cup"):
            return matches_team(title, {"BESIKTAS"})
        if key in ("ucl", "uel", "uecl"):
            return matches_team(title, TURKISH_CLUBS) or (
                key == "ucl" and matches_team(title, FEATURED_CLUBS)
            )
        return bool(re.search(r"\[(?:CL|EL|ECL)\]", title))

    events = calendar_events(f"https://ics.fixtur.es/v2/{route}.ics", now, "Futbol", tournament, select)
    if key == "turkiye":
        confirmed = {"2026-11-12": "Gürsel Aksel Stadyumu, İzmir",
                     "2026-11-15": "Stade Matmut Atlantique, Bordeaux"}
        for event in events:
            if event.date_str in confirmed:
                event.venue = confirmed[event.date_str]
                event.source_url = "https://www.tff.org/Default.aspx?ftxtId=51843&pageId=200"
    return events


def sources(now):
    jobs = {
        key: partial(football_source, now, key, route, tournament)
        for key, (route, tournament) in FOOTBALL.items()
    }
    jobs.update({
        "bsl": partial(fetch_bsl, now),
        "national_basketball": partial(fetch_national_basketball, now),
        "national_volleyball": partial(fetch_national_volleyball, now),
        "denizli_askf": partial(fetch_football, now),
        "denizli_tvf": partial(fetch_volleyball, now),
        "denizli_belediye": partial(fetch_municipal, now),
        "denizli_bilet": partial(fetch_tickets, now),
        "boxing_wbc": partial(fetch_boxing, now),
        "boxing_wba": partial(fetch_wba, now),
        "boxing_wbo": partial(fetch_wbo, now),
        "combat_featured": partial(fetch_featured, now),
        "combat_turkey_registry": partial(fetch_turkish_mma, now),
        "euroleague": partial(fetch_euroleague, now),
        "f1": partial(legacy.fetch_f1_events, now),
        "motogp": partial(legacy.fetch_motogp_events, now),
        "nba": partial(calendar_events, "https://ics.fixtur.es/v2/league/nba.ics",
                       now, "NBA", "NBA", lambda title: matches_team(title, NBA_CLUBS), 10),
        "ufc": partial(calendar_events, "https://raw.githubusercontent.com/clarencechaan/ufc-cal/ics/UFC.ics",
                       now, "UFC", "UFC", days=120),
    })
    return jobs


def event_row(event):
    start = datetime.fromisoformat(f"{event.date_str}T{event.time_str or '00:00'}").replace(tzinfo=legacy.TURKEY_TZ)
    team_sports = ("Futbol", "Basketbol", "NBA", "Denizli Futbol", "Denizli Voleybol", "Boks")
    teams = event.title.split(" - ", 1) if event.sport in team_sports else event.main_event
    home, away = teams if len(teams) == 2 else ("", "")
    return {
        "id": event.event_id, "sport": event.sport, "competition": event.tournament,
        "title": event.title, "home": home, "away": away, "hasTime": event.has_time,
        "start": int(start.timestamp() * 1000), "status": event.status,
        "stage": event.session_or_stage, "venue": event.venue, "url": event.source_url,
    }


def collect(now, previous, jobs):
    timestamp = int(now.timestamp() * 1000)
    cutoff = int((now - timedelta(days=2)).timestamp() * 1000)
    updates = {}
    with ThreadPoolExecutor(max_workers=5) as pool:
        pending = {key: pool.submit(fetch) for key, fetch in jobs.items()}
        for key, future in pending.items():
            old = previous.get(key, {})
            try:
                rows = list({event.event_id: event_row(event) for event in future.result()}.values())
                updates[key] = {"checked": timestamp, "ok": timestamp, "events": rows}
                print(f"{key}: {len(rows)} fixtures")
            except Exception as error:
                updates[key] = {
                    "checked": timestamp, "ok": old.get("ok", 0),
                    "events": [row for row in old.get("events", []) if row["start"] >= cutoff],
                }
                print(f"{key}: {type(error).__name__}; kept previous fixtures")
    # Keep one-time imported records until an upstream source supersedes them or
    # their date expires. Migration must not silently discard unmatched future data.
    def identity(row):
        title = re.sub(r"\s*\[(?:CL|EL|ECL|TC)]", "", row.get("title", row["id"]), flags=re.I)
        return row.get("sport", ""), row["start"] // 86_400_000, name_key(title)
    current = {identity(row) for source in updates.values() for row in source["events"]}
    for key, source in previous.items():
        if key.endswith("_migration"):
            kept = [row for row in source.get("events", []) if row["start"] >= cutoff and identity(row) not in current]
            if kept:
                updates[key] = {"checked": source["checked"], "ok": source["ok"], "events": kept}
    return updates


def snapshot(groups):
    reviewed_men = set(json.loads((COMBAT_DATA / "reviewed_mens_boxing.json").read_text(encoding="utf-8")))
    merged = {}
    for source in groups.values():
        for row in source["events"]:
            identity = row["id"]
            if row.get("sport") == "Boks" and identity not in reviewed_men:
                continue
            existing = merged.get(identity)
            if existing is None or source["ok"] > existing[0]:
                merged[identity] = (source["ok"], row)
    return {
        "version": 1,
        "events": sorted((row for _, row in merged.values()), key=lambda row: (row["start"], row["id"])),
        "sources": {key: {field: source[field] for field in ("checked", "ok")} for key, source in groups.items()},
    }


def document_url(project, document):
    if not re.fullmatch(r"[a-z][a-z0-9-]{4,62}", project):
        raise ValueError("Invalid Firebase project ID")
    return f"https://firestore.googleapis.com/v1/projects/{project}/databases/(default)/documents/{document}"


def read_firestore(project, token, document):
    url = document_url(project, document)
    headers = {"Authorization": f"Bearer {token}"}
    try:
        with urlopen(Request(url, headers=headers), timeout=20) as response:
            return json.loads(json.load(response)["fields"]["payload"]["stringValue"])
    except HTTPError as error:
        if error.code == 404:
            return {}
        raise


def document_write(project, document, value):
    payload = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    if len(payload.encode("utf-8")) > 900_000:
        raise ValueError("Fixture snapshot exceeds the Firestore size guard")
    name = document_url(project, document).split("/v1/", 1)[1]
    return {"update": {"name": name, "fields": {"payload": {"stringValue": payload}}}}


def publish(project, token, groups, result):
    legacy_sources = {
        key: source for key, source in groups.items()
        if not key.startswith(("denizli_", "boxing_", "combat_", "national_"))
    }
    request = Request(
        document_url(project, "unused").removesuffix("/unused") + ":commit",
        data=json.dumps({"writes": [
            document_write(project, "sports_internal/sources", groups),
            document_write(project, "sports_feed/current", snapshot(legacy_sources)),
            document_write(project, "sports_feed/current_v2", result),
        ]}).encode("utf-8"),
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        method="POST",
    )
    with urlopen(request, timeout=20) as response:
        response.read()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--preview", type=Path)
    args = parser.parse_args()
    now = datetime.now(legacy.TURKEY_TZ)
    project = os.environ.get("FIREBASE_PROJECT_ID", "")
    token = os.environ.get("FIRESTORE_ACCESS_TOKEN", "")
    previous = {} if args.preview else read_firestore(project, token, "sports_internal/sources")
    groups = collect(now, previous, sources(now))
    result = snapshot(groups)
    if not any(source["checked"] == source["ok"] for source in groups.values()) or not result["events"]:
        raise RuntimeError("No usable fixture snapshot; previous publication retained")
    if args.preview:
        args.preview.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    else:
        publish(project, token, groups, result)
    print(f"Fixture snapshot: {len(result['events'])} events")


if __name__ == "__main__":
    main()
