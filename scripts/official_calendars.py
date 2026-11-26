"""Public official UFC and MotoGP adapters; no repository-hosted feeds."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime,timedelta
from html import unescape
import re
from urllib.parse import urljoin
from scripts import veri_topla as legacy
from scripts.denizli_fixtures import name_key,text

UFC='https://www.ufc.com/events'
MOTOGP='https://www.motogp.com/en/calendar'


def parse_ufc(page,now):
    blocks=re.split(r'<(?:article|div)\b[^>]*class="c-card-event--result(?:\s[^"]*)?"[^>]*>',page)
    if len(blocks)<2:raise ValueError('Official UFC event structure missing')
    events={}
    for block in blocks[1:]:
        heading=re.search(r'<h3[^>]*class="c-card-event--result__headline"[^>]*>\s*<a[^>]*href="(/event/[^"?#]+)"[^>]*>(.*?)</a>',block,re.S)
        stamp=re.search(r'data-main-card-timestamp="(\d+)"',block)
        if not heading or not stamp:continue
        start=datetime.fromtimestamp(int(stamp[1]),legacy.TURKEY_TZ)
        if not now.date()-timedelta(days=1)<=start.date()<=now.date()+timedelta(days=120):continue
        url=urljoin(UFC,heading[1]); slug=heading[1].split('/')[-1]
        label='UFC '+slug.removeprefix('ufc-') if re.fullmatch(r'ufc-\d+',slug) else 'UFC Fight Night'
        matchup=text(heading[2])
        item=legacy.MasterEvent('UFC_OFFICIAL_'+slug,'UFC',label+': '+matchup,start.date().isoformat(),start.strftime('%H:%M'),True,'UFC','Ana kart','', 'SCHEDULED',url)
        events[url]=item
    return list(events.values())


def official_headliner(page,title):
    names=[]
    for side in ['red','blue']:
        corner=re.search(r'class="c-listing-fight__corner-name c-listing-fight__corner-name--'+side+r'"[^>]*>(.*?)</div>',page,re.S)
        if not corner:return None
        pieces=re.findall(r'class="c-listing-fight__corner-(?:given|family)-name"[^>]*>(.*?)</span>',corner[1],re.S)
        if not pieces:return None
        names.append(' '.join(text(p) for p in pieces))
    announced=re.split(r'\s+vs\.?\s+',title.split(':',1)[-1].strip(),maxsplit=1,flags=re.I)
    if len(announced)!=2 or not all(name_key(full).endswith(name_key(short)) for full,short in zip(names,announced)):return None
    return tuple(names)


def fetch_ufc(now):
    events=parse_ufc(legacy.http_get(UFC),now)
    def enrich(item):
        try:
            names=official_headliner(legacy.http_get(item.source_url),item.title)
            if names:item.main_event=names;item.session_or_stage='Ana maç'
        except Exception:pass  # Official listing remains valid without an optional portrait match.
        return item
    with ThreadPoolExecutor(max_workers=3) as pool:return list(pool.map(enrich,events[:12]))


def parse_motogp(page,now,url):
    blocks=re.findall(r'<div\b[^>]*data-widget="structured-data-event/structured-data-event"[^>]*>',page,re.S)
    if not blocks:raise ValueError('Official MotoGP calendar structure missing')
    events=[]
    for block in blocks:
        fields=dict((key,unescape(value)) for key,value in re.findall(r'data-structured-([a-z-]+)="([^"]*)"',block))
        if fields.get('kind')!='GP':continue
        start=datetime.fromisoformat(fields['start-date']);end=datetime.fromisoformat(fields['end-date'])
        # Calendar window opening/closing hours are NOT session times. Publish date-only.
        day=end.date()
        if not now.date()-timedelta(days=1)<=day<=now.date()+timedelta(days=400):continue
        title='MotoGP - '+fields['title'].strip()
        item=legacy.MasterEvent(f"MGP_OFFICIAL_{fields['year']}_{legacy.slugify(fields['title'])}",'MotoGP',title,day.isoformat(),None,False,'MotoGP',f"Yarış hafta sonu {start:%d.%m}–{end:%d.%m.%Y}; seans saati duyurusu bekleniyor",fields.get('location',''),'CANCELLED' if fields.get('status')=='CANCELLED' else 'SCHEDULED',url)
        events.append(item)
    return events


def fetch_motogp(now):
    result=[];success=False;last=None
    for year in [now.year,now.year+1]:
        url=f'{MOTOGP}/{year}'
        try:result.extend(parse_motogp(legacy.http_get(url),now,url));success=True
        except Exception as error:last=error
    if not success:raise ValueError('Official MotoGP calendars unavailable') from last
    return result
