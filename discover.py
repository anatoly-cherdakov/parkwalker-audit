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
                        (r'\b(\d{1,2}/\d{1,2}/\d{2,4})\b','%m/%d/%Y'),
                        (r'\b(\d{1,2}/\d{1,2}/\d{2})\b','%m/%d/%y'),
                        (r'\b(\d{1,2}\.\d{1,2}\.20\d\d)\b','%d.%m.%Y')):
        for m in re.finditer(pattern,text):
            try: return datetime.strptime(m.group(1),fmt).date()
            except ValueError: pass
    return None

def get_venue_list(sess):
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
    events=sorted({e['slug']:e for e in events}.values(),key=lambda e:e['slug'])
    if len(events)<500:
        raise ValueError('Too few UK 5k venues returned: '+str(len(events)))
    return events

def eventhistory(html,slug):
    soup=BeautifulSoup(html,'html.parser')
    title=soup.get_text(' ',strip=True)
    if re.search(r'confirm you are human|complete the security check|captcha',title,re.I):
        raise ValueError('CAPTCHA')
    if not re.search('event history',title,re.I) and not re.search('veranstaltungshistorie|история',title,re.I):
        raise ValueError('Missing event history heading')
    events={}
    for table in soup.find_all('table'):
        found=0
        for tr in table.find_all('tr'):
            a=tr.find('a',href=re.compile(r'/results/\d+/?(?:[?#]|$)'))
            if not a:continue
            href=a.get('href','')
            m=re.search(r'/results/(\d+)',href)
            if not m: continue
            n=int(m.group(1))
            cells=tr.find_all(['td'],recursive=False)
            if len(cells)<2: continue
            # Date is in the second column, before first-finisher details.
            dt=dateparse(cells[1].get_text(' ',strip=True))
            if not dt: continue
            found+=1
            if START<=dt<=END:
                events[n]=dt.strftime('%d.%m.%Y')
        if found>=5: break
    if not events and not re.search('no events|no results',title,re.I):
        # A newer venue may have no eligible runs; this is valid only if
        # a real dated history table was parsed.
        if not any(re.search(r'/results/\d+',str(t)) for t in soup.find_all('table')):
            raise ValueError('No parseable dated event history')
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
    # Critical: finish the original 1909 audit before discovering additional venues.
    pending_original=sum(r['Статус']==UNK for r in rows[:1909])
    if pending_original:
        print('DISCOVERY PAUSED: original 1909 still have',pending_original,'unchecked; no new venues added.',flush=True)
        return
    if DONE.exists():
        print('DISCOVERY ALREADY COMPLETE. No new/current events will be checked.',flush=True)
        return
    sess=requests.Session()
    sess.headers.update(HEADERS)
    if not VENUES.exists():
        venues=get_venue_list(sess)
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
    undone.sort(key=lambda v:(retries.get(v['slug'],0),v['slug']))
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
