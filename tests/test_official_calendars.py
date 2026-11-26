import unittest
from datetime import datetime
from scripts.official_calendars import parse_ufc,parse_motogp,official_headliner
from scripts.veri_topla import TURKEY_TZ


class OfficialCalendarTests(unittest.TestCase):
    now=datetime(2026,11,25,tzinfo=TURKEY_TZ)

    def test_ufc_uses_main_card_timestamp_not_ticket_or_prelim_time(self):
        page='<article class="c-card-event--result"><h3 class="c-card-event--result__headline"><a href="/event/ufc-999">Alpha vs Beta</a></h3><div data-main-card-timestamp="1796493600" data-prelims-card-timestamp="1796482800"></div></article>'
        item=parse_ufc(page,self.now)[0]
        self.assertEqual(item.time_str,'21:00')
        self.assertEqual(item.source_url,'https://www.ufc.com/event/ufc-999')

    def test_motogp_calendar_window_is_never_published_as_session_time(self):
        page='<div data-widget="structured-data-event/structured-data-event" data-structured-kind="GP" data-structured-title="TEST GP" data-structured-year="2026" data-structured-start-date="2026-11-27T08:00:00+01:00" data-structured-end-date="2026-11-29T18:00:00+01:00" data-structured-status="NOT-STARTED"></div>'
        item=parse_motogp(page,self.now,'https://www.motogp.com/en/calendar/2026')[0]
        self.assertEqual(item.date_str,'2026-11-29')
        self.assertFalse(item.has_time)
        self.assertIsNone(item.time_str)

    def test_headliner_must_match_official_event_title(self):
        page=''.join(f'<div class="c-listing-fight__corner-name c-listing-fight__corner-name--{side}"><span class="c-listing-fight__corner-given-name">{first}</span><span class="c-listing-fight__corner-family-name">{last}</span></div>' for side,first,last in [('red','Test','Alpha'),('blue','Other','Beta')])
        self.assertEqual(official_headliner(page,'UFC: Alpha vs Beta'),('Test Alpha','Other Beta'))
        self.assertIsNone(official_headliner(page,'UFC: Gamma vs Delta'))
