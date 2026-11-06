#!/usr/bin/env python3
"""One-time discovery of UK 5 km parkrun event histories through 2026-11-04.
Keeps a frozen venue inventory and checkpointed per-venue progress.
No future/new-event monitoring.
"""
import csv
import json
import os
import re
import tempfile
import time
from datetime import datetime, date
from pathlib import Path
from urllib.parse import urlparse
import requests
from bs4 import BeautifulSoup
from collect import FIELDS, HEADERS, UNK, sources

START=date(2022,10,1)
END=date(2026,11,4)
BASE=Path('parkwalker.csv')
VENUES=Path('venues.json')
STATE=Path('discovery_state.json')
DONE=Path('audit_complete.json')
FETCH_LIMIT=int(os.getenv('DISCOVERY_LIMIT','3'))

def atomic_json(path, obj):
    with tempfile.NamedTemporaryFile('w',encoding='utf-8',delete=False,dir='.') as out:
        json.dump(obj,out,ensure_ascii=False,indent=2)
        tmp=out.name
    os.replace(tmp,path)

def dateparse(text):
    text=text.strip()
    for pattern,fmt in ((r'\b(20\d\d-\d\d-\d\d)\b','%Y-%m-%d'),
                        (r'\b(\d{1,2}/\d{1,2}/\d{2,4})\b','%d/%m/%Y'),
                        (r'\b(\d{1,2}/\d{1,2}/\d{2})\b','%d/%m/%y'),
                        (r'\b(\d{1,2}\.\d{1,2}\.20\d\d)\b','%d.%m.%Y')):
        for m in re.finditer(pattern,text):
            try: return datetime.strptime(m.group(1),fmt).date()
            except ValueError: pass
    return None

def get_venue_list(sess,original_rows):
    # Official parkrun GeoJSON; UK country code 97, standard 5k series ID 1.
    resp=sess.get('https://images.parkrun.com/events.json',timeout=40)
    resp.raise_for_status()
    data=resp.json()
    events=[]
    for feature in data['events']['features']:
        p=feature.get('properties') or {}
        if str(p.get('countrycode'))!='97' or str(p.get('seriesid'))!='1': continue
        slug=str(p.get('eventname') or '').strip().lower()
        if not re.fullmatch(r'[a-z0-9-]+',slug): continue
        events.append({'slug':slug,'name':str(p.get('EventShortName') or slug)})
    # Keep all 10 original venues even if the current GeoJSON omits a
    # temporarily inactive or now-closed venue.
    by_slug={e['slug']:e for e in events}
    for row in original_rows[:1909]:
        slug=urlparse(row['Ссылка']).path.strip('/').split('/')[0].lower()
        if re.fullmatch(r'[a-z0-9-]+',slug) and slug not in by_slug:
            by_slug[slug]={'slug':slug,'name':row['Локация']}
    events=sorted(by_slug.values(),key=lambda e:e['slug'])
    if len(events)<500:
        raise ValueError('Too few UK 5k venues returned: '+str(len(events)))
    return events

def eventhistory(html,slug):
    soup=BeautifulSoup(html,'html.parser')
    title=soup.get_text(' ',strip=True)
    if re.search(r'confirm you are human|complete the security check|captcha',title,re.I):
        raise ValueError('CAPTCHA')
    # Current parkrun event history uses tr.Results-table-row with data-parkrun
    # and data-date rather than requiring a link in each table row.
    table=soup.select_one('table#results, table.Results-table, table')
    if not table:
        raise ValueError('Missing event history table')
    events={}
    parsed=0
    for tr in soup.find_all('tr'):
        numtxt=tr.get('data-parkrun') or tr.get('data-event') or ''
        datetxt=tr.get('data-date') or ''
        a=tr.find('a',href=re.compile(r'/results/\d+/?(?:[?#]|$)|runSeqNumber=\d+'))
        if not numtxt and a:
            m=re.search(r'/results/(\d+)|runSeqNumber=(\d+)',a.get('href',''))
            if m:numtxt=next(x for x in m.groups() if x)
        cells=tr.find_all('td',recursive=False)
        if not numtxt and cells:
            m=re.search(r'^#?\s*(\d{1,5})\s*$',cells[0].get_text(' ',strip=True))
            if m: numtxt=m.group(1)
        if not str(numtxt).isdigit():continue
        if not datetxt:
            if len(cells)>1: datetxt=cells[1].get_text(' ',strip=True)
            elif a: datetxt=a.get_text(' ',strip=True)
        dt=dateparse(datetxt)
        if not dt:
            # Date is often the anchor in the first table cell.
            if cells:dt=dateparse(cells[0].get_text(' ',strip=True))
        if not dt: continue
        parsed+=1
        if START<=dt<=END:
            events[int(numtxt)]=dt.strftime('%d.%m.%Y')
    if parsed==0:
        raise ValueError('No dated event rows parsed: do not mark venue complete')
    return events

def pull_history(sess,slug):
    url=f'https://www.parkrun.org.uk/{slug}/results/eventhistory/'
    errors=[]
    for candidate in sources(url):
        try:
            r=sess.get(candidate,timeout=30)
            r.raise_for_status()
            events=eventhistory(r.text,slug)
            return events
        except (requests.RequestException,ValueError) as e:
            errors.append(urlparse(candidate).netloc+':'+str(e)[:70])
    raise ValueError('; '.join(errors))

def load_csv():
    with BASE.open(encoding='utf-8-sig',newline='') as f:
        reader=csv.DictReader(f,delimiter=';')
        fields=reader.fieldnames
        rows=list(reader)
    if len(rows)<1909 or not all(k in fields for k in FIELDS):
        raise RuntimeError('Missing original 1909 rows or incorrect headers.')
    return fields, rows

def write_csv(fields,rows):
    with tempfile.NamedTemporaryFile('w',encoding='utf-8-sig',newline='',delete=False,dir='.') as f:
        writer=csv.DictWriter(f,delimiter=';',fieldnames=fields,extrasaction='ignore')
        writer.writeheader()
        writer.writerows(rows)
        tmp=f.name
    os.replace(tmp,BASE)

def main():
    fields,rows=load_csv()
    # Prefer finishing all 1909 records first. A permanently blocked page must
    # not prevent the broader UK history audit: permit discovery after each
    # remaining original URL has already had three separate attempts.
    pending_original=[r for r in rows[:1909] if r['Статус']==UNK]
    if pending_original:
        attempts_path=Path('attempts.json')
        attempts=json.loads(attempts_path.read_text(encoding='utf-8')) if attempts_path.exists() else {}
        untouched=sum(attempts.get(r['Ссылка'],0)<3 for r in pending_original)
        if untouched:
            print('DISCOVERY PAUSED: original records remaining',len(pending_original),
                  'with',untouched,'requiring initial retries.',flush=True)
            return
        print('DISCOVERY STARTING with',len(pending_original),
              'blocked original records preserved as NOT CHECKED.',flush=True)
    if DONE.exists():
        print('DISCOVERY ALREADY COMPLETE. No new/current events will be checked.',flush=True)
        return

    # Venue-first mode: do not keep growing the nationwide backlog while
    # partially verified venues still have reasonable untried/retryable rows.
    attempts_path=Path('attempts.json')
    attempts=json.loads(attempts_path.read_text(encoding='utf-8')) if attempts_path.exists() else {}
    focused={}
    for row in rows:
        slug=urlparse(row['Ссылка']).path.strip('/').split('/')[0].lower()
        g=focused.setdefault(slug,{'checked':0,'pending':[]})
        if row['Статус']==UNK:
            g['pending'].append(row)
        else:
            g['checked']+=1
    pending_partial=[
        slug for slug,g in focused.items()
        if g['checked']>0 and g['pending']
        and any(attempts.get(row['Ссылка'],0)<5 for row in g['pending'])
    ]
    if pending_partial:
        print('DISCOVERY PAUSED: finishing',len(pending_partial),
              'partially checked venues first.',flush=True)
        return
    sess=requests.Session()
    sess.headers.update(HEADERS)
    if not VENUES.exists():
        venues=get_venue_list(sess,rows)
        atomic_json(VENUES,venues)
        print('FROZEN UK 5K VENUE LIST:',len(venues),flush=True)
    else:
        venues=json.loads(VENUES.read_text(encoding='utf-8'))
    state=json.loads(STATE.read_text(encoding='utf-8')) if STATE.exists() else {}
    complete=state.setdefault('complete',[])
    retries=state.setdefault('retries',{})
    seen={(r['Ссылка'].rstrip('/')).lower() for r in rows}
    # Only venues from frozen inventory: no recurring/new event lookup.
    undone=[v for v in venues if v['slug'] not in complete]
    original_slugs={urlparse(r['Ссылка']).path.strip('/').split('/')[0] for r in rows[:1909]}
    undone.sort(key=lambda v:(v['slug'] not in original_slugs,retries.get(v['slug'],0),v['slug']))
    print('VENUES awaiting discovery:',len(undone),flush=True)
    for v in undone[:FETCH_LIMIT]:
        slug=v['slug']
        try:
            history=pull_history(sess,slug)
            added=0
            for num,dt in sorted(history.items()):
                url=f'https://www.parkrun.org.uk/{slug}/results/{num}/'
                if url.rstrip('/').lower() in seen:continue
                rows.append(dict(zip(FIELDS,[v['name'],str(num),dt,url,UNK,'','',''])))
                seen.add(url.rstrip('/').lower())
                added+=1
            write_csv(fields,rows)
            complete.append(slug)
            retries.pop(slug,None)
            print('DISCOVERED',slug,'events_in_range',len(history),'new_rows',added,flush=True)
        except Exception as exc:
            retries[slug]=retries.get(slug,0)+1
            print('DISCOVERY RETRY',slug,str(exc)[:220],flush=True)
        atomic_json(STATE,state)
        time.sleep(2)
    remaining=len(venues)-len(complete)
    print('DISCOVERY SUMMARY: venues_remaining=',remaining,
          'all_results_rows=',len(rows),flush=True)
    if remaining==0 and all(r['Статус']!=UNK for r in rows):
        atomic_json(DONE,{'date_limit':str(END),'venues':len(venues),
                          'results':len(rows),'status':'complete'})
        print('HISTORICAL AUDIT COMPLETE; no future monitoring.',flush=True)

if __name__=='__main__':
    main()
