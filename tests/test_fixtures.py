import unittest
import json
from datetime import datetime, timedelta
from unittest.mock import patch

from scripts.publish_fixtures import calendar_events, calendar_start, collect, publish, snapshot, ufc_main_event
from scripts.combat_fixtures import parse_turkish_mma, parse_wba
from scripts.denizli_fixtures import parse_denizlispor
from scripts.national_fixtures import parse_fiba, parse_volleyball_draw
from scripts.turkish_basketball import parse_bsl
from scripts.veri_topla import TURKEY_TZ


class FixtureTests(unittest.TestCase):
    now = datetime(2026, 11, 9, tzinfo=TURKEY_TZ)

    def test_midnight_utc_is_a_confirmed_start_and_belongs_to_turkish_day(self):
        start, confirmed = calendar_start("DTSTART:20261110T000000Z")
        self.assertEqual((start.day, start.hour, confirmed), (10, 3, True))
        start, confirmed = calendar_start("DTSTART;VALUE=DATE:20261110")
        self.assertEqual((start.day, start.hour, confirmed), (10, 0, False))

    def test_same_teams_on_two_dates_keep_separate_ids(self):
        payload = """BEGIN:VCALENDAR
BEGIN:VEVENT
UID:match-1
DTSTART:20261110T000000Z
SUMMARY:Beşiktaş - Tofaş
END:VEVENT
BEGIN:VEVENT
UID:match-2
DTSTART:20261120T000000Z
SUMMARY:Beşiktaş - Tofaş
END:VEVENT
END:VCALENDAR"""
        with patch("scripts.publish_fixtures.legacy.http_get", return_value=payload):
            records = calendar_events("https://example.org", self.now, "Basketbol", "BSL")
        self.assertEqual(len({event.event_id for event in records}), 2)

    def test_tentative_denizli_home_game_updates_in_place(self):
        html = """<div data-week-name="13.Hafta"><div class="citem">
        <span class="citem01A">26.12.2026-00:00</span>
        <h3 class="teamName">Yukatel Denizli Basket</h3>
        <span class="score">-</span><h3 class="teamName">Fenerbahçe Tarfin</h3>
        </div></div>"""
        provisional = parse_bsl(html, 2026, "https://example.org", self.now)[0]
        confirmed = parse_bsl(html.replace("26.12.2026-00:00", "27.12.2026-18:30"),
                              2026, "https://example.org", self.now)[0]
        self.assertEqual(provisional.event_id, confirmed.event_id)
        self.assertEqual(provisional.status, "DATE_TENTATIVE")
        self.assertFalse(provisional.has_time)
        self.assertTrue(confirmed.has_time)

    def test_denizlispor_only_includes_dated_senior_home_fixtures(self):
        start = int((self.now + timedelta(days=3, hours=14)).timestamp())
        row = """<article class="club-match-row" data-state="upcoming" data-venue="home" data-time="{stamp}">
        <div class="club-match-context"><span>4. hafta</span><span>Süper Amatör Lig</span></div>
        <div class="club-match-team club-team-1"><span>DENİZLİSPOR</span></div>
        <a href="/mac-detay.php?id={id}">Program</a>
        <div class="club-match-team club-team-2"><span>KIRALAN DEMİR SK</span></div>
        </article>"""
        page = '<div id="clubMatchList">' + row.format(stamp=0, id=1) + row.format(stamp=start, id=2)
        page += row.format(stamp=start, id=3).replace('data-venue="home"', 'data-venue="away"')
        fixtures = parse_denizlispor(page, self.now)
        self.assertEqual([fixture.event_id for fixture in fixtures], ["DENIZLISPOR_ASKF_2"])
        self.assertEqual(fixtures[0].title, "Denizlispor - KIRALAN DEMİR SK")
        self.assertEqual(fixtures[0].time_str, "14:00")

    def test_failed_source_keeps_previous_data_and_marks_it_stale(self):
        previous = {"nba": {"ok": 1, "events": [
            {"id": "a", "start": int((self.now + timedelta(days=1)).timestamp() * 1000)}
        ]}}

        def fail():
            raise OSError("source unavailable")

        result = snapshot(collect(self.now, previous, {"nba": fail}))
        self.assertEqual([row["id"] for row in result["events"]], ["a"])
        self.assertLess(result["sources"]["nba"]["ok"], result["sources"]["nba"]["checked"])

    def test_imported_future_records_survive_first_firebase_only_refresh(self):
        future = int((self.now + timedelta(days=3)).timestamp() * 1000)
        expired = int((self.now - timedelta(days=5)).timestamp() * 1000)
        previous = {"github_migration": {"ok": 1, "checked": 1, "events": [
            {"id": "future", "start": future, "sport": "Futbol", "title": "A - B"},
            {"id": "expired", "start": expired, "sport": "Futbol", "title": "C - D"},
        ]}}
        result = snapshot(collect(self.now, previous, {}))
        self.assertEqual([row["id"] for row in result["events"]], ["future"])

    def test_firestore_publishes_archive_and_app_snapshot_atomically(self):
        with patch("scripts.publish_fixtures.urlopen") as request:
            groups = {
                "nba": {"events": [{"id": "nba", "start": 1}], "ok": 1, "checked": 1},
                "denizli_tff": {"events": [{"id": "tff", "start": 2}], "ok": 1, "checked": 1},
            }
            publish("test-project", "test-token", groups, snapshot(groups))
        http_request = request.call_args.args[0]
        self.assertEqual(http_request.get_method(), "POST")
        self.assertTrue(http_request.full_url.endswith("/documents:commit"))
        writes = json.loads(http_request.data)["writes"]
        self.assertEqual(
            [item["update"]["name"].split("/documents/")[1] for item in writes],
            ["sports_internal/sources", "sports_feed/current", "sports_feed/current_v2"],
        )

        def event_ids(write):
            payload = json.loads(write["update"]["fields"]["payload"]["stringValue"])
            return [row["id"] for row in payload["events"]]
        self.assertEqual(event_ids(writes[1]), ["nba"])
        self.assertEqual(event_ids(writes[2]), ["nba", "tff"])

    def test_snapshot_filters_unreviewed_boxing_even_from_cache_but_keeps_ufc(self):
        rows = [
            {"id": "BOXING_JASSIEL_AMADOR_VS_KENSHIRO_TERAJI", "start": 1, "sport": "Boks"},
            {"id": "BOXING_GABRIELA_FUNDORA_VS_JASMINA_ZAPOTOCZNA", "start": 2, "sport": "Boks"},
            {"id": "UFC_WOMENS_BOUT", "start": 3, "sport": "UFC"},
        ]
        result = snapshot({"cached": {"checked": 2, "ok": 1, "events": rows}})
        self.assertEqual([row["id"] for row in result["events"]], [rows[0]["id"], rows[2]["id"]])

    def test_wba_retains_world_titles_and_deduplicates_price_with_the_wbc_entry(self):
        def card(label):
            row = {"@type": "Event", "startDate": "2026-11-20", "name": "LAUREN PRICE vs MICHAELA KOTASKOVA - " + label}
            return '<script type="application/ld+json">' + json.dumps(row) + '</script>'

        result = parse_wba(card("WBA Continental") + card("UNIFICATION WBA World / IBF / WBC"), self.now)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].event_id, "BOXING_lauren-price-vs-michaela-kotaskova")
        self.assertEqual(result[0].session_or_stage, "Ana maç")
        self.assertFalse(result[0].has_time)

    def test_turkish_registry_only_accepts_future_dated_local_fight_nights(self):
        def card(name, identifier, date):
            return (
                f'<a href="/Mma/Organizasyon-Detay/fight/{identifier}">'
                f'<h6 class="posts__title">{name}</h6><div data-date="{date}"></div></a>'
            )

        page = '<div class="mmaorganizasyon">' + card("VENDETTA FIGHT NIGHT ANKARA", 1, "11 30, 2026 19:00:00")
        page += card("VENDETTA FIGHT NIGHT WIEN", 2, "11 30, 2026 19:00:00")
        page += card("CORE CFC ISTANBUL", 3, "10 30, 2026 19:00:00")
        self.assertEqual([item.event_id for item in parse_turkish_mma(page, self.now)], ["TURKEY_MMA_1"])

    def test_ufc_official_event_link_from_ics_description(self):
        payload = (
            "BEGIN:VCALENDAR\nBEGIN:VEVENT\nUID:ufc-test\nDTSTART:20261112T000000Z\n"
            "SUMMARY:UFC 334: Gane vs Hokit\nDESCRIPTION:https://www.ufc.com/event/ufc-334\n"
            "END:VEVENT\nEND:VCALENDAR"
        )
        with patch("scripts.publish_fixtures.legacy.http_get", return_value=payload):
            result = calendar_events("https://example.org/ufc.ics", self.now, "UFC", "UFC")
        self.assertEqual(result[0].source_url, "https://www.ufc.com/event/ufc-334")

    def test_fiba_mens_qualifier_uses_turkish_time_and_excludes_other_teams(self):
        def game(match, home, away):
            return {
                "gameId":match, "gameName":"J-7", "statusCode":"INIT",
                "teamA":{"code":home,"officialName":home},
                "teamB":{"code":away,"officialName":away},
                "gameDateTime":"2026-11-26T20:00:00", "gameDateTimeUTC":"2026-11-26T19:00:00",
                "hasTimeGameDateTime":True, "competition":{"competitionId":208943},
            }

        payload=json.dumps([game(1,"ITA","TUR"), game(2,"ITA","LTU")], separators=(",",":"))
        page="<script>self.__next_f.push([1," + json.dumps(payload) + "])</script>"
        result=parse_fiba(page,self.now)
        self.assertEqual([(e.event_id,e.title,e.time_str) for e in result],
                         [("MILLI_BASKET_1", "İtalya - Türkiye", "22:00")])

    def test_volleyball_draw_is_a_date_only_pool_event(self):
        page="<p>Pool Play: August 20–25, 2027</p><p>Pool C: Türkiye, Dominican Republic, France, Egypt</p>"
        result=parse_volleyball_draw(page,self.now)[0]
        self.assertEqual(result.date_str, "2027-08-20")
        self.assertFalse(result.has_time)
        self.assertIn("20–25 Ağustos",result.session_or_stage)

    def test_ufc_only_uses_the_first_bout_when_it_matches_the_announced_headliner(self):
        first = "• Jiří Procházka (#2) vs. Navajo Stirling (#5) @205"
        second = "• Aljamain Sterling vs. Kevin Vallejos @145"
        item = {"DESCRIPTION": "Main Card\n--------------------\n" + first + "\n" + second}
        self.assertEqual(
            ufc_main_event(item, "UFC Fight Night: Prochazka vs Stirling"),
            ("Jiří Procházka", "Navajo Stirling"),
        )
        self.assertIsNone(ufc_main_event(item, "UFC Fight Night: Sterling vs Vallejos"))
        self.assertIsNone(ufc_main_event(item, "UFC Fight Night: TBD vs TBD"))


if __name__ == "__main__":
    unittest.main()
