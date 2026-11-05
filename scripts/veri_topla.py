#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
scripts/veri_topla.py
Aşama 7.4 – Spor Etkinlikleri Yönetim Sistemi

Bu betik spor karşılaşmalarını ve seanslarını yönetir:
- Genel fikstürler başlangıç verisi olarak alınır.
- Kendi etkinlik kayıtlarımız (data/etkinlikler.json) ana kayıttır.
- İnternet kontrolleri (günde 5 kez) yeni etkinlikleri ve değişiklikleri (tarih, saat, durum, mekan) tespit eder.
- Değişiklikler aynı eventId üzerinden güncellenir ve tarihçeye kaydedilir.
- Çıktı olarak Android uygulaması için proje kökündeki maclar.txt dosyası atomik olarak üretilir.

Format (maclar.txt): GG.AA.YYYY SS:DD | Branş | Karşılaşma/Seans | eventId | Kaynak URL
"""

import os
import sys
import re
import json
import urllib.request
import urllib.error
from datetime import datetime, timezone, timedelta

# Türkiye Standart Saati (UTC+3, kalıcı yaz saati)
TURKEY_TZ = timezone(timedelta(hours=3))

USER_AGENT = "AsistanApp/1.0 (Open-Source Personal Assistant; github.com/asistan)"
HTTP_TIMEOUT = 15


# ==============================================================================
# YARDIMCI FONKSİYONLAR (METİN VE ZAMAN DÖNÜŞÜMLERİ)
# ==============================================================================

def fix_mojibake(text: str) -> str:
    """Olası çift UTF-8 kodlama bozulmalarını düzeltir."""
    if not text:
        return ""
    if any(c in text for c in ("Ã", "Å", "Ä")):
        try:
            return text.encode("latin-1").decode("utf-8")
        except (UnicodeEncodeError, UnicodeDecodeError):
            pass
    return text


def slugify(text: str) -> str:
    """Türkçe karakterleri normalize eder ve alfanümerik ASCII slug üretir."""
    if not text:
        return "UNKNOWN"
    text = fix_mojibake(text)
    tr_map = {
        'ı': 'I', 'I': 'I', 'İ': 'I', 'i': 'I',
        'ğ': 'G', 'Ğ': 'G',
        'ü': 'U', 'Ü': 'U',
        'ş': 'S', 'Ş': 'S',
        'ö': 'O', 'Ö': 'O',
        'ç': 'C', 'Ç': 'C'
    }
    for k, v in tr_map.items():
        text = text.replace(k, v)
    text = re.sub(r'[^A-Za-z0-9]+', '_', text)
    return text.strip('_').upper()


def http_get(url: str, timeout: int = HTTP_TIMEOUT) -> str:
    """Belirtilen URL'den HTTP GET ile metin içeriği çeker."""
    req = urllib.request.Request(
        url,
        headers={"User-Agent": USER_AGENT, "Accept": "*/*"}
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        raw_bytes = resp.read()
        try:
            text = raw_bytes.decode("utf-8")
        except UnicodeDecodeError:
            charset = resp.headers.get_content_charset() or "iso-8859-1"
            text = raw_bytes.decode(charset, errors="replace")
        return fix_mojibake(text)


def parse_ics_datetime(dt_str: str) -> tuple[datetime | None, bool]:
    """
    ICS DTSTART/DTEND dizgisini Türkiye saatine (UTC+3) çevirir.
    Döndürür: (datetime_objesi, has_time_bool)
    has_time: Saat bilgisi kaynakta açıkça mevcutsa True, sadece tarih veya 00:00 yer tutucusu varsa False.
    """
    if not dt_str:
        return None, False
    dt_str = dt_str.strip()
    if ":" in dt_str:
        parts = dt_str.split(":")
        dt_str = parts[-1].strip()

    # Sadece gün formatı: YYYYMMDD (Saat henüz açıklanmamış)
    if len(dt_str) == 8 and dt_str.isdigit():
        try:
            dt_day = datetime.strptime(dt_str, "%Y%m%d")
            return dt_day.replace(hour=0, minute=0, second=0, tzinfo=TURKEY_TZ), False
        except ValueError:
            return None, False

    # UTC formatı: YYYYMMDDTHHMMSSZ
    if dt_str.endswith("Z"):
        clean_str = dt_str[:-1]
        try:
            dt_utc = datetime.strptime(clean_str, "%Y%m%dT%H%M%S").replace(tzinfo=timezone.utc)
            # Eğer saat 00:00:00 ise bu bir tarih yer tutucusudur (TBD)
            if clean_str.endswith("T000000"):
                return dt_utc.astimezone(TURKEY_TZ).replace(hour=0, minute=0, second=0), False
            return dt_utc.astimezone(TURKEY_TZ), True
        except ValueError:
            return None, False

    # Yerel saat formatı: YYYYMMDDTHHMMSS
    if "T" in dt_str:
        try:
            dt_naive = datetime.strptime(dt_str, "%Y%m%dT%H%M%S")
            if dt_str.endswith("T000000") or (dt_naive.hour == 0 and dt_naive.minute == 0 and dt_naive.second == 0):
                return dt_naive.replace(tzinfo=TURKEY_TZ), False
            return dt_naive.replace(tzinfo=TURKEY_TZ), True
        except ValueError:
            return None, False

    return None, False


def parse_ics_events(ics_text: str) -> list[dict]:
    """iCalendar metnindeki VEVENT bloklarını ayrıştırır."""
    events = []
    unfolded = re.sub(r'\r?\n[ \t]', '', ics_text)
    lines = unfolded.splitlines()

    current = None
    for line in lines:
        line = line.strip()
        if not line:
            continue
        if line == "BEGIN:VEVENT":
            current = {}
            continue
        if line == "END:VEVENT":
            if current:
                events.append(current)
            current = None
            continue
        if current is not None:
            if ":" in line:
                idx = line.find(":")
                key = line[:idx].strip().upper()
                val = line[idx + 1:].strip()
                val_clean = val.replace(r'\,', ',').replace(r'\;', ';').replace(r'\n', '\n').strip()
                key_clean = key.split(";")[0]
                current[key_clean] = val_clean
                if key.startswith("DTSTART"):
                    current["_DTSTART_RAW"] = line
    return events


# ==============================================================================
# ETKİNLİK VERİ MODELİ
# ==============================================================================

class MasterEvent:
    """Kendi etkinlik kayıtlarımızı temsil eden temel veri sınıfı."""

    def __init__(
        self,
        event_id: str,
        sport: str,
        title: str,
        date_str: str,
        time_str: str | None,
        has_time: bool,
        tournament: str = "",
        session_or_stage: str = "",
        venue: str = "",
        status: str = "SCHEDULED",
        source_url: str = "",
        first_seen: str = "",
        last_updated: str = "",
        change_log: list[str] | None = None
    ):
        self.event_id = event_id
        self.sport = sport
        self.title = fix_mojibake(title)
        self.date_str = date_str          # "YYYY-MM-DD"
        self.time_str = time_str          # "HH:MM" veya None
        self.has_time = has_time          # True/False
        self.time_str = time_str if has_time else None  # Saat belirsizse kesinlikle None
        self.tournament = tournament
        self.session_or_stage = session_or_stage
        self.venue = fix_mojibake(venue)
        self.status = status              # "SCHEDULED", "POSTPONED", "CANCELLED", "COMPLETED"
        self.source_url = source_url
        self.first_seen = first_seen
        self.last_updated = last_updated
        self.change_log = change_log if change_log is not None else []

    def to_dict(self) -> dict:
        return {
            "event_id": self.event_id,
            "sport": self.sport,
            "title": self.title,
            "date_str": self.date_str,
            "time_str": self.time_str,
            "has_time": self.has_time,
            "tournament": self.tournament,
            "session_or_stage": self.session_or_stage,
            "venue": self.venue,
            "status": self.status,
            "source_url": self.source_url,
            "first_seen": self.first_seen,
            "last_updated": self.last_updated,
            "change_log": self.change_log,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "MasterEvent":
        return cls(
            event_id=d.get("event_id", ""),
            sport=d.get("sport", ""),
            title=d.get("title", ""),
            date_str=d.get("date_str", ""),
            time_str=d.get("time_str"),
            has_time=d.get("has_time", True),
            tournament=d.get("tournament", ""),
            session_or_stage=d.get("session_or_stage", ""),
            venue=d.get("venue", ""),
            status=d.get("status", "SCHEDULED"),
            source_url=d.get("source_url", ""),
            first_seen=d.get("first_seen", ""),
            last_updated=d.get("last_updated", ""),
            change_log=d.get("change_log", []),
        )

    def to_maclar_line(self) -> str:
        """maclar.txt için kesin 5 alanlı satır üretir."""
        try:
            parts = self.date_str.split("-")
            formatted_date = f"{parts[2]}.{parts[1]}.{parts[0]}"
        except Exception:
            formatted_date = "01.01.2026"

        clean_title = re.sub(r'\s*\(Saat Belirsiz\)', '', self.title, flags=re.IGNORECASE).strip()

        if self.has_time and self.time_str:
            formatted_time = self.time_str
        else:
            formatted_time = "00:00"
            clean_title = f"{clean_title} (Saat Belirsiz)"

        if self.status == "POSTPONED" and "ertelendi" not in clean_title.lower():
            clean_title = f"{clean_title} [ERTELENDİ]"
        elif self.status == "CANCELLED" and "iptal" not in clean_title.lower():
            clean_title = f"{clean_title} [İPTAL]"

        return f"{formatted_date} {formatted_time} | {self.sport} | {clean_title} | {self.event_id} | {self.source_url}"


# ==============================================================================
# SPOR ETKİNLİKLERİ TOPLAYICILARI (DIŞ KAYNAK OKUYUCULARI)
# ==============================================================================

def fetch_f1_events(now_tr: datetime) -> list[MasterEvent]:
    """Formula 1 seanslarını Jolpica Ergast API'sinden (yedek: Sportstimes ICS) çeker."""
    events = []
    current_year = now_tr.year
    jolpica_url = f"https://api.jolpi.ca/ergast/f1/{current_year}.json"

    session_names = {
        "FirstPractice": ("1. Antrenman", "FP1"),
        "SecondPractice": ("2. Antrenman", "FP2"),
        "ThirdPractice": ("3. Antrenman", "FP3"),
        "Qualifying": ("Sıralama Turları", "QUALIFYING"),
        "SprintQualifying": ("Sprint Sıralama", "SPRINT_QUALIFYING"),
        "Sprint": ("Sprint Yarışı", "SPRINT"),
    }

    try:
        data_text = http_get(jolpica_url)
        data = json.loads(data_text)
        races = data.get("MRData", {}).get("RaceTable", {}).get("Races", [])

        for race in races:
            round_num = int(race.get("round", "0"))
            race_name = race.get("raceName", "Grand Prix")
            gp_short = race_name.replace(" Grand Prix", " GP").replace("Grand Prix", "GP")
            circuit_name = race.get("Circuit", {}).get("circuitName", "")
            locality = race.get("Circuit", {}).get("Location", {}).get("locality", "")
            venue = f"{circuit_name}, {locality}".strip(", ")

            # Ana yarış
            race_date = race.get("date")
            race_time = race.get("time")
            if race_date:
                has_time = False
                time_str = None
                if race_time:
                    dt_raw = f"{race_date}T{race_time}"
                    dt, has_t = parse_ics_datetime(dt_raw.replace(":", "").replace("-", ""))
                    if dt and dt >= now_tr - timedelta(days=1):
                        has_time = has_t
                        time_str = dt.strftime("%H:%M")
                        event_id = f"F1_{current_year}_R{round_num:02d}_RACE"
                        title = f"Formula 1 - {gp_short} - Yarış"
                        events.append(MasterEvent(
                            event_id=event_id,
                            sport="Formula 1",
                            title=title,
                            date_str=race_date,
                            time_str=time_str,
                            has_time=has_time,
                            tournament="Formula 1",
                            session_or_stage="Yarış",
                            venue=venue,
                            source_url=jolpica_url
                        ))

            # Alt seanslar
            for sess_key, (sess_title, sess_code) in session_names.items():
                sess_obj = race.get(sess_key)
                if isinstance(sess_obj, dict):
                    s_date = sess_obj.get("date")
                    s_time = sess_obj.get("time")
                    if s_date:
                        has_time = False
                        time_str = None
                        if s_time:
                            dt_raw = f"{s_date}T{s_time}"
                            dt, has_t = parse_ics_datetime(dt_raw.replace(":", "").replace("-", ""))
                            if dt and dt >= now_tr - timedelta(days=1):
                                has_time = has_t
                                time_str = dt.strftime("%H:%M")
                                event_id = f"F1_{current_year}_R{round_num:02d}_{sess_code}"
                                title = f"Formula 1 - {gp_short} - {sess_title}"
                                events.append(MasterEvent(
                                    event_id=event_id,
                                    sport="Formula 1",
                                    title=title,
                                    date_str=s_date,
                                    time_str=time_str,
                                    has_time=has_time,
                                    tournament="Formula 1",
                                    session_or_stage=sess_title,
                                    venue=venue,
                                    source_url=jolpica_url
                                ))

        if events:
            return events

    except Exception as e:
        print(f"[UYARI] Jolpica F1 API erişilemedi: {e}. Sportstimes ICS deneniyor...")

    # Yedek: Sportstimes F1 ICS
    ics_url = "https://files-f1.sportstimes.com/f1-calendar_all.ics"
    try:
        ics_text = http_get(ics_url)
        vevents = parse_ics_events(ics_text)
        for v in vevents:
            summary = v.get("SUMMARY", "")
            raw_dt = v.get("_DTSTART_RAW", v.get("DTSTART", ""))
            venue = v.get("LOCATION", "")
            dt, has_time = parse_ics_datetime(raw_dt)
            if not dt or dt < now_tr - timedelta(days=1):
                continue
            slug_name = slugify(summary)
            event_id = f"F1_{current_year}_{slug_name}"
            events.append(MasterEvent(
                event_id=event_id,
                sport="Formula 1",
                title=f"Formula 1 - {summary}",
                date_str=dt.strftime("%Y-%m-%d"),
                time_str=dt.strftime("%H:%M") if has_time else None,
                has_time=has_time,
                tournament="Formula 1",
                session_or_stage=summary,
                venue=venue,
                source_url=ics_url
            ))
        return events
    except Exception as e2:
        raise RuntimeError(f"F1 verisi toplanamadı (Jolpica: {e}, ICS: {e2})")


def fetch_motogp_events(now_tr: datetime) -> list[MasterEvent]:
    """MotoGP seanslarını Nixxo iCal takviminden çeker."""
    events = []
    current_year = now_tr.year
    urls = [
        f"https://nixxo.github.io/calendars/motogp/{current_year}/MotoGP_{current_year}_calendar.ics",
        f"https://nixxo.github.io/calendars/motogp/{current_year - 1}/MotoGP_{current_year - 1}_calendar.ics"
    ]

    last_error = None
    for ics_url in urls:
        try:
            ics_text = http_get(ics_url)
            vevents = parse_ics_events(ics_text)
            for v in vevents:
                summary = v.get("SUMMARY", "")
                venue = v.get("LOCATION", "")
                raw_dt = v.get("_DTSTART_RAW", v.get("DTSTART", ""))
                dt, has_time = parse_ics_datetime(raw_dt)
                if not dt or dt < now_tr - timedelta(days=1):
                    continue

                title = f"MotoGP - {summary}" if not summary.startswith("MotoGP") else summary
                event_id = f"MGP_{current_year}_{slugify(summary)}"
                events.append(MasterEvent(
                    event_id=event_id,
                    sport="MotoGP",
                    title=title,
                    date_str=dt.strftime("%Y-%m-%d"),
                    time_str=dt.strftime("%H:%M") if has_time else None,
                    has_time=has_time,
                    tournament="MotoGP",
                    session_or_stage=summary,
                    venue=venue,
                    source_url=ics_url
                ))

            if events:
                return events
        except Exception as e:
            last_error = e
            continue

    raise RuntimeError(f"MotoGP takvimi alınamadı: {last_error}")


def fetch_football_events(now_tr: datetime) -> list[MasterEvent]:
    """Futbol karşılaşmalarını Fixtur.es üzerinden çeker."""
    events = []
    current_year = now_tr.year

    turkish_teams = [
        "besiktas", "beşiktaş", "galatasaray", "fenerbahce", "fenerbahçe",
        "trabzonspor", "basaksehir", "başakşehir", "samsunspor", "turkey", "türkiye",
        "adana demirspor", "eyupspor", "eyüpspor", "antalyaspor", "kayserispor",
        "sivasspor", "rizespor", "kasimpasa", "kasımpaşa", "alanyaspor", "konyaspor"
    ]

    marquee_teams = [
        "real madrid", "manchester city", "bayern", "barcelona", "paris saint-germain",
        "psg", "arsenal", "liverpool", "inter", "milan", "juventus", "atletico madrid",
        "borussia dortmund", "chelsea", "bayer leverkusen", "aston villa", "newcastle",
        "tottenham", "roma", "benfica", "sporting", "porto"
    ]

    sources = [
        ("https://ics.fixtur.es/v2/besiktas.ics", "BJK", "Trendyol Süper Lig / Avrupa"),
        ("https://ics.fixtur.es/v2/galatasaray.ics", "GS", "Trendyol Süper Lig / Avrupa"),
        ("https://ics.fixtur.es/v2/fenerbahce.ics", "FB_TEAM", "Trendyol Süper Lig / Avrupa"),
        ("https://ics.fixtur.es/v2/trabzonspor.ics", "TS_TEAM", "Trendyol Süper Lig / Avrupa"),
        ("https://ics.fixtur.es/v2/tr.ics", "MILLI", "UEFA / Millî Maçlar"),
        ("https://ics.fixtur.es/v2/league/champions-league.ics", "UCL", "UEFA Şampiyonlar Ligi"),
        ("https://ics.fixtur.es/v2/league/europa-league.ics", "UEL", "UEFA Avrupa Ligi"),
        ("https://ics.fixtur.es/v2/league/uefa-conference-league.ics", "UECL", "UEFA Konferans Ligi"),
    ]

    collected_ids = set()

    for url, tag, tournament_name in sources:
        try:
            ics_text = http_get(url)
            vevents = parse_ics_events(ics_text)
            for v in vevents:
                summary = v.get("SUMMARY", "").strip()
                if not summary:
                    continue
                venue = v.get("LOCATION", "")
                raw_dt = v.get("_DTSTART_RAW", v.get("DTSTART", ""))
                dt, has_time = parse_ics_datetime(raw_dt)
                if not dt or dt < now_tr - timedelta(days=1):
                    continue

                sum_lower = summary.lower()
                is_selected = False
                if tag in ("BJK", "GS", "FB_TEAM", "TS_TEAM", "MILLI"):
                    is_selected = True
                else:
                    has_turkish = any(t in sum_lower for t in turkish_teams)
                    has_marquee = sum(1 for m in marquee_teams if m in sum_lower) >= 1
                    if has_turkish or has_marquee:
                        is_selected = True

                if not is_selected:
                    continue

                event_id = f"FB_{current_year}_{slugify(summary)}"
                if event_id in collected_ids:
                    continue
                collected_ids.add(event_id)

                events.append(MasterEvent(
                    event_id=event_id,
                    sport="Futbol",
                    title=summary,
                    date_str=dt.strftime("%Y-%m-%d"),
                    time_str=dt.strftime("%H:%M") if has_time else None,
                    has_time=has_time,
                    tournament=tournament_name,
                    venue=venue,
                    source_url=url
                ))

        except Exception as e:
            print(f"[UYARI] Futbol beslemesi ({tag} - {url}) alınamadı: {e}")

    if not events:
        raise RuntimeError("Hiçbir futbol kaynağından veri alınamadı.")
    return events


def fetch_nba_events(now_tr: datetime) -> list[MasterEvent]:
    """NBA önemli karşılaşmalarını Fixtur.es takviminden çeker."""
    events = []
    current_year = now_tr.year
    nba_url = "https://ics.fixtur.es/v2/league/nba.ics"

    focus_teams = [
        "lakers", "celtics", "warriors", "nuggets", "bucks",
        "mavericks", "knicks", "heat", "76ers", "suns",
        "timberwolves", "thunder", "clippers", "cavaliers"
    ]

    seen_ids = set()

    try:
        ics_text = http_get(nba_url)
        vevents = parse_ics_events(ics_text)
        for v in vevents:
            summary = v.get("SUMMARY", "").strip()
            venue = v.get("LOCATION", "")
            raw_dt = v.get("_DTSTART_RAW", v.get("DTSTART", ""))
            dt, has_time = parse_ics_datetime(raw_dt)
            if not dt or dt < now_tr - timedelta(days=1):
                continue

            sum_lower = summary.lower()
            is_important = (
                "playoff" in sum_lower or
                "final" in sum_lower or
                sum(1 for t in focus_teams if t in sum_lower) >= 1
            )

            if not is_important:
                continue

            event_id = f"NBA_{current_year}_{slugify(summary)}"
            if event_id in seen_ids:
                continue
            seen_ids.add(event_id)

            events.append(MasterEvent(
                event_id=event_id,
                sport="NBA",
                title=summary,
                date_str=dt.strftime("%Y-%m-%d"),
                time_str=dt.strftime("%H:%M") if has_time else None,
                has_time=has_time,
                tournament="NBA",
                venue=venue,
                source_url=nba_url
            ))

        return events

    except Exception as e:
        raise RuntimeError(f"NBA takvimi alınamadı ({nba_url}): {e}")


def parse_ufc_main_card_bouts(description: str, event_title: str) -> list[str]:
    """UFC VEVENT açıklamasındaki 'Main Card' eşleşmelerini ayrıştırır."""
    bouts = []
    if not description:
        return bouts

    desc_clean = description.replace(r'\n', '\n')
    main_section = desc_clean
    if "Main Card" in desc_clean:
        main_section = desc_clean.split("Main Card", 1)[1]
    if "Prelims" in main_section:
        main_section = main_section.split("Prelims", 1)[0]

    lines = main_section.split("\n")
    for line in lines:
        line = fix_mojibake(line)
        line = re.sub(r'^[•\*\-\s\u2022\xa0]+', '', line).strip()
        if not line or "vs" not in line.lower():
            continue

        m = re.search(r'^(.*?)\s+vs\.?\s+(.*?)(?:@\d+|\s*$)', line, re.IGNORECASE)
        if m:
            f1_raw, f2_raw = m.group(1).strip(), m.group(2).strip()
            f1 = re.sub(r'\s*\([#\dC\s]+\)', '', f1_raw).strip()
            f2 = re.sub(r'\s*\([#\dC\s]+\)', '', f2_raw).strip()
            if f1 and f2:
                bouts.append(f"{f1} vs {f2}")

    return bouts


def fetch_ufc_events(now_tr: datetime) -> list[MasterEvent]:
    """UFC etkinliklerini açık takvimden çeker, ana kart eşleşmelerini listeler."""
    events = []
    ufc_url = "https://raw.githubusercontent.com/clarencechaan/ufc-cal/ics/UFC.ics"

    try:
        ics_text = http_get(ufc_url)
        vevents = parse_ics_events(ics_text)
        for v in vevents:
            summary = v.get("SUMMARY", "").strip()
            venue = v.get("LOCATION", "")
            raw_dt = v.get("_DTSTART_RAW", v.get("DTSTART", ""))
            dt, has_time = parse_ics_datetime(raw_dt)
            if not dt or dt < now_tr - timedelta(days=1):
                continue

            card_prefix = summary if summary.startswith("UFC") else f"UFC - {summary}"
            card_slug = slugify(summary)
            desc = v.get("DESCRIPTION", "")
            bouts = parse_ufc_main_card_bouts(desc, summary)

            date_str = dt.strftime("%Y-%m-%d")
            time_str = dt.strftime("%H:%M") if has_time else None

            if bouts:
                for bout in bouts:
                    bout_title = f"{card_prefix} - {bout}"
                    bout_id = f"UFC_{card_slug}_{slugify(bout)}"
                    events.append(MasterEvent(
                        event_id=bout_id,
                        sport="UFC",
                        title=bout_title,
                        date_str=date_str,
                        time_str=time_str,
                        has_time=has_time,
                        tournament="UFC",
                        session_or_stage="Main Card",
                        venue=venue,
                        source_url=ufc_url
                    ))
            else:
                event_id = f"UFC_{card_slug}"
                events.append(MasterEvent(
                    event_id=event_id,
                    sport="UFC",
                    title=card_prefix,
                    date_str=date_str,
                    time_str=time_str,
                    has_time=has_time,
                    tournament="UFC",
                    venue=venue,
                    source_url=ufc_url
                ))

        return events

    except Exception as e:
        raise RuntimeError(f"UFC takvimi alınamadı ({ufc_url}): {e}")


# ==============================================================================
# KENDİ ETKİNLİK SİSTEMİMİZ: DEPOLAMA VE DEĞİŞİKLİK MUTABAKATI
# ==============================================================================

def load_master_store(json_path: str, maclar_path: str, now_tr: datetime) -> dict[str, MasterEvent]:
    """
    Kendi ana etkinlik kayıtlarımızı (etkinlikler.json) yükler.
    Dosya henüz yoksa mevcut maclar.txt dosyasından başlangıç verisi (bootstrap) oluşturur.
    """
    store = {}
    if os.path.exists(json_path):
        try:
            with open(json_path, "r", encoding="utf-8") as f:
                data = json.load(f)
                for ev_id, ev_dict in data.items():
                    store[ev_id] = MasterEvent.from_dict(ev_dict)
            print(f"[ANA KAYIT] {json_path} yüklendi: {len(store)} kayıt mevcut.")
            return store
        except Exception as e:
            print(f"[UYARI] {json_path} okunamadı: {e}. maclar.txt'den kurtarma deneniyor.")

    # JSON henüz yoksa maclar.txt'den bootstrap yap
    if os.path.exists(maclar_path):
        try:
            now_iso = now_tr.isoformat()
            with open(maclar_path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line or line.startswith("#"):
                        continue
                    parts = [p.strip() for p in line.split("|")]
                    if len(parts) == 5:
                        date_time_str, sport, title, event_id, source_url = parts
                        # Parse GG.AA.YYYY SS:DD
                        dt_parts = date_time_str.split(" ")
                        if len(dt_parts) == 2:
                            d, m, y = dt_parts[0].split(".")
                            t = dt_parts[1]
                            date_str = f"{y}-{m}-{d}"
                            has_time = True
                            time_str = t
                            if t == "00:00" and ("belirsiz" in title.lower() or "açıklanmadı" in title.lower()):
                                has_time = False
                                time_str = None

                            store[event_id] = MasterEvent(
                                event_id=event_id,
                                sport=sport,
                                title=title,
                                date_str=date_str,
                                time_str=time_str,
                                has_time=has_time,
                                source_url=source_url,
                                first_seen=now_iso,
                                last_updated=now_iso,
                                change_log=["Başlangıç aktarımı: Mevcut fikstürden ana kayıtlara alındı."]
                            )
            print(f"[BOOTSTRAP] maclar.txt üzerinden {len(store)} ana kayıt oluşturuldu.")
        except Exception as e:
            print(f"[UYARI] maclar.txt bootstrap hatası: {e}")

    return store


def reconcile_events(
    master_store: dict[str, MasterEvent],
    incoming_events: list[MasterEvent],
    now_tr: datetime
) -> tuple[int, int, int]:
    """
    Gelen dış verileri kendi ana kayıtlarımızla karşılaştırır:
    - Yeni karşılaşmaları tespit edip ekler.
    - Tarih, saat, durum, mekan veya başlık değişikliklerini aynı eventId üzerinde günceller.
    - Kaynakta bulunmayan mevcut kayıtları ASLA silmez, korur.
    Döndürür: (yeni_kayit_sayisi, guncellenen_kayit_sayisi, korunan_kayit_sayisi)
    """
    now_iso = now_tr.isoformat()
    new_count = 0
    updated_count = 0

    for inc in incoming_events:
        ev_id = inc.event_id
        if ev_id not in master_store:
            # 1. YENİ ETKİNLİK
            inc.first_seen = now_iso
            inc.last_updated = now_iso
            inc.change_log = [f"{now_iso}: Etkinlik ilk kez kaydedildi ({inc.source_url})."]
            master_store[ev_id] = inc
            new_count += 1
        else:
            # 2. MEVCUT ETKİNLİK - DEĞİŞİKLİK TAKİBİ
            existing = master_store[ev_id]
            changes = []

            # Tarih değişikliği
            if inc.date_str and inc.date_str != existing.date_str:
                changes.append(f"Tarih güncellendi: {existing.date_str} -> {inc.date_str}")
                existing.date_str = inc.date_str

            # Saat açıklanması veya değişikliği
            if not existing.has_time and inc.has_time and inc.time_str:
                changes.append(f"Başlama saati açıklandı: {inc.time_str}")
                existing.has_time = True
                existing.time_str = inc.time_str
                existing.title = re.sub(r'\s*\(Saat Belirsiz\)', '', existing.title, flags=re.IGNORECASE).strip()
            elif existing.has_time and inc.has_time and inc.time_str and inc.time_str != existing.time_str:
                changes.append(f"Başlama saati değişti: {existing.time_str} -> {inc.time_str}")
                existing.time_str = inc.time_str

            # Durum (Status) kontrolü
            if inc.status != existing.status and inc.status in ("POSTPONED", "CANCELLED", "COMPLETED"):
                changes.append(f"Durum değişti: {existing.status} -> {inc.status}")
                existing.status = inc.status

            # Mekân bilgisi zenginleştirmesi
            if inc.venue and inc.venue != existing.venue:
                if not existing.venue:
                    changes.append(f"Mekân eklendi: {inc.venue}")
                else:
                    changes.append(f"Mekân güncellendi: {existing.venue} -> {inc.venue}")
                existing.venue = inc.venue

            # Başlık / Seans zenginleştirmesi
            if inc.title and inc.title != existing.title:
                if len(inc.title) > len(existing.title) or " vs " in inc.title:
                    changes.append(f"Başlık zenginleştirildi: {existing.title} -> {inc.title}")
                    existing.title = inc.title

            if changes:
                existing.last_updated = now_iso
                existing.change_log.extend([f"{now_iso}: {c}" for c in changes])
                updated_count += 1

    retained_count = len(master_store) - new_count
    return new_count, updated_count, retained_count


def save_master_store(json_path: str, master_store: dict[str, MasterEvent]):
    """Ana kayıtlarımızı (etkinlikler.json) atomik olarak güvenle kaydeder."""
    os.makedirs(os.path.dirname(json_path), exist_ok=True)
    temp_path = f"{json_path}.tmp"
    raw_dict = {k: v.to_dict() for k, v in master_store.items()}

    with open(temp_path, "w", encoding="utf-8", newline="\n") as f:
        json.dump(raw_dict, f, ensure_ascii=False, indent=2)
        f.flush()
        os.fsync(f.fileno())

    os.replace(temp_path, json_path)
    print(f"[BAŞARILI] Ana kayıt deposu güncellendi: {json_path} ({len(master_store)} kayıt)")


def export_maclar_txt(maclar_path: str, master_store: dict[str, MasterEvent], now_tr: datetime):
    """
    Android uygulaması için maclar.txt dosyasını atomik olarak üretir.
    Format kesin olarak: GG.AA.YYYY SS:DD | Branş | Karşılaşma/Seans | eventId | Kaynak URL
    """
    temp_path = f"{maclar_path}.tmp"
    cutoff = (now_tr - timedelta(days=1)).strftime("%Y-%m-%d")

    # Gelecek veya dünkü aktif karşılaşmalar (İptal edilenler hariç)
    active_events = [
        ev for ev in master_store.values()
        if ev.status != "CANCELLED" and ev.date_str >= cutoff
    ]

    # Kronolojik sıralama: tarih, saat (belirsiz olanlar gün sonuna), event_id
    active_events.sort(key=lambda ev: (ev.date_str, ev.time_str or "99:99", ev.event_id))

    header_lines = [
        f"# ASISTAN SPOR ETKINLIKLERI (Guncelleme: {now_tr.strftime('%d.%m.%Y %H:%M')} TR)",
        "# Format: GG.AA.YYYY SS:DD | Branş | Karşılaşma/Seans | eventId | Kaynak URL",
        f"# Toplam Etkinlik Sayisi: {len(active_events)}"
    ]

    body_lines = [ev.to_maclar_line() for ev in active_events]
    full_content = "\n".join(header_lines + body_lines) + "\n"

    with open(temp_path, "w", encoding="utf-8", newline="\n") as f:
        f.write(full_content)
        f.flush()
        os.fsync(f.fileno())

    os.replace(temp_path, maclar_path)
    print(f"[BAŞARILI] maclar.txt dosyası güncellendi ({len(active_events)} aktif etkinlik).")


# ==============================================================================
# ANA ÇALIŞMA FONKSİYONU
# ==============================================================================

def main():
    now_tr = datetime.now(TURKEY_TZ)
    print("=" * 65)
    print(f"SPOR ETKİNLİKLERİ YÖNETİM SİSTEMİ ÇALIŞTI ({now_tr.strftime('%d.%m.%Y %H:%M')} TSİ)")
    print("=" * 65)

    base_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
    data_dir = os.path.join(base_dir, "data")
    master_json_path = os.path.join(data_dir, "etkinlikler.json")
    maclar_txt_path = os.path.join(base_dir, "maclar.txt")

    # 1. Kendi ana etkinlik kayıtlarımızı yükle
    master_store = load_master_store(master_json_path, maclar_txt_path, now_tr)
    initial_count = len(master_store)

    # 2. İnternet kontrolü: Yeni fikstürler ve değişiklikler
    collectors = [
        ("Formula 1", fetch_f1_events),
        ("MotoGP", fetch_motogp_events),
        ("Futbol", fetch_football_events),
        ("NBA", fetch_nba_events),
        ("UFC", fetch_ufc_events),
    ]

    all_incoming: list[MasterEvent] = []
    collector_status = {}

    for sport_name, collector_fn in collectors:
        try:
            print(f"-> {sport_name} verileri toplanıyor...")
            sport_events = collector_fn(now_tr)
            print(f"   [OK] {sport_name}: {len(sport_events)} etkinlik çekildi.")
            all_incoming.extend(sport_events)
            collector_status[sport_name] = f"Aktif ({len(sport_events)} kayıt)"
        except Exception as e:
            print(f"   [UYARI] {sport_name} kaynağına ulaşılamadı: {e}")
            existing_count = sum(1 for ev in master_store.values() if ev.sport == sport_name)
            print(f"   [KORUMA] {sport_name} için ana kayıttaki {existing_count} etkinlik aynen korundu.")
            collector_status[sport_name] = f"Hata ({e}) - {existing_count} mevcut kayıt korundu"

    # 3. Değişiklik ve Tekillik Mutabakatı (Reconciliation)
    new_count, updated_count, retained_count = reconcile_events(master_store, all_incoming, now_tr)

    print("\n" + "-" * 65)
    print("MUTABAKAT ÖZETİ:")
    print(f" - Başlangıçtaki Ana Kayıt Sayısı : {initial_count}")
    print(f" - Yeni Eklenen Karşılaşmalar    : {new_count}")
    print(f" - Güncellenen Karşılaşmalar      : {updated_count}")
    print(f" - Değişmeyen / Korunan Kayıtlar  : {retained_count}")
    print(f" - Son Toplam Ana Kayıt Sayısı   : {len(master_store)}")
    print("-" * 65)

    # 4. Kendi ana depomuzu ve maclar.txt dosyasını atomik olarak yaz
    save_master_store(master_json_path, master_store)
    export_maclar_txt(maclar_txt_path, master_store, now_tr)

    print("\n" + "=" * 65)
    print("KAYNAK ÇALIŞMA DURUMLARI:")
    for sp, st in collector_status.items():
        print(f" - {sp:12}: {st}")
    print("=" * 65)


if __name__ == "__main__":
    main()
