#!/usr/bin/env python3
"""Collect actual UK parkrun event counts in the 4 years before parkwalker.
Frozen 118 venues only. Does NOT modify parkwalker.csv or audit scope.
Historical attendance is event-level, never inferred from volunteer presence.
"""
import csv, json, os, re, tempfile, time
from collections import Counter
from datetime import date, datetime
from pathlib import Path
from urllib.parse import urlparse
import requests
from bs4 import BeautifulSoup
from collect import sources, HEADERS

START=date(2018,10,1)
END=date(2022,9,30)
TARGET=Path('historical_venues_118.json')
OUTPUT=Path('pre_parkwalker_2018_2022.csv')
STATE=Path('pre_parkwalker_state.json')
FIELDS=['Локация','Номер протокола','Дата','Ссылка','Участники','Статус']
UNVERIFIED='НЕ ПРОВЕРЕНО'
VALID='Проверено'
def atomic_csv(rows):
    with tempfile.NamedTemporaryFile('w',encoding='utf-8-sig',newline='',delete=False,dir='.') as f:
        w=csv.DictWriter(f,fieldnames=FIELDS,delimiter=';',extrasaction='ignore')
        w.writeheader()
        w.writerows(sorted(rows.values(),key=lambda r:(r['Локация'],int(r['Номер протокола']))))
        tmp=f.name
    os.replace(tmp,OUTPUT)
def atomic_state(state):
    with tempfile.NamedTemporaryFile('w',encoding='utf-8',delete=False,dir='.') as f:
        json.dump(state,f,ensure_ascii=False,indent=2)
        tmp=f.name
    os.replace(tmp,STATE)
def parse_date(s):
    for fmt in ('%d/%m/%Y','%d/%m/%y','%Y-%m-%d','%d.%m.%Y'):
        try:return datetime.strptime(s.strip(),fmt).date()
        except ValueError:pass
    return None
def get(sess,url):
    errors=[]
    for alt in sources(url):
        try:
            r=sess.get(alt,timeout=25)
            r.raise_for_status()
            soup=BeautifulSoup(r.text,'html.parser')
            tx=soup.get_text(' ',strip=True)
            if len(tx)<300 or re.search('captcha|confirm you are human|complete the security check',tx,re.I):
                raise ValueError('CAPTCHA / incomplete')
            return soup
        except Exception as e:
            errors.append(str(e)[:65])
    raise RuntimeError('; '.join(errors))
def history(sess,slug):
    url=f'https://www.parkrun.org.uk/{slug}/results/eventhistory/'
    soup=get(sess,url)
    table=soup.select_one('table#results,table.Results-table,table')
    if table is None:raise ValueError('No history table')
    # A count is accepted only when tied to an explicit finisher/participant heading.
    headers=[x.get_text(' ',strip=True).lower() for x in table.select('thead th')]
    if not headers:
        first=table.find('tr')
        headers=[x.get_text(' ',strip=True).lower() for x in first.find_all(['th','td'],recursive=False)] if first else []
    count_col=next((i for i,h in enumerate(headers)
                    if any(t in h for t in ('finishers','participants','runners','walkers and runners','финишер'))),None)
    found={}
    parsed=0
    for tr in table.find_all('tr'):
        cells=tr.find_all('td',recursive=False)
        if not cells:continue
        num=tr.get('data-parkrun') or tr.get('data-event')
        if not num:
            m=re.search(r'^#?\s*(\d{1,5})\s*$',cells[0].get_text(' ',strip=True))
            if m:num=m.group(1)
        if not num:
            a=tr.find('a',href=re.compile(r'/results/\d+|runSeqNumber=\d+'))
            if a:
                m=re.search(r'/results/(\d+)|runSeqNumber=(\d+)',a.get('href',''))
                if m:num=next(z for z in m.groups() if z)
        if not num or not str(num).isdigit():continue
        dates=[tr.get('data-date','')]
        dates.extend(c.get_text(' ',strip=True) for c in cells[:2])
        dt=None
        for ds in dates:
            m=re.search(r'(20\d\d-\d\d-\d\d|\d{1,2}/\d{1,2}/\d{2,4}|\d{1,2}\.\d{1,2}\.20\d\d)',ds)
            if m:
                dt=parse_date(m.group(1))
                if dt:break
        if not dt:continue
        parsed+=1
        if not START<=dt<=END:continue
        count=''
        if count_col is not None and count_col<len(cells):
            value=cells[count_col].get_text(' ',strip=True).replace(',','')
            if re.fullmatch(r'\d{1,5}',value):count=value
        found[int(num)]=(dt.strftime('%d.%m.%Y'),count)
    if not parsed:raise ValueError('No valid dated events parsed; do not mark complete')
    return found
def event_count(sess,url,number):
    soup=get(sess,url)
    heads=' '.join(h.get_text(' ',strip=True) for h in soup.select('h1,h2,h3')[:30])
    if not re.search(r'(?:#|№)\s*'+re.escape(str(number))+r'(?!\d)',heads):
        raise ValueError('Event number mismatch')
    body=soup.get_text(' ',strip=True)
    m=re.search(r'\b(\d{1,5})\s*(?:finishers|finisher|финишер)',body,re.I)
    if m:return int(m.group(1))
    for table in soup.find_all('table'):
        hdr=' '.join(c.get_text(' ',strip=True).lower() for c in table.select('thead th'))
        if 'position' in hdr and ('time' in hdr or 'gender' in hdr):
            positions=[]
            for tr in table.select('tbody tr'):
                cs=tr.find_all('td',recursive=False)
                if cs:
                    t=cs[0].get_text(' ',strip=True)
                    if t.isdigit():positions.append(int(t))
            if positions and len(positions)>=max(1,max(positions)-2):
                return max(positions)
    raise ValueError('Unable to verify participant count')
def main():
    venues=json.loads(TARGET.read_text(encoding='utf-8'))['venues']
    if len(venues)!=118 or len(set(venues))!=118:
        raise RuntimeError('Frozen venue list is not exactly 118 entries')
    rows={}
    if OUTPUT.exists():
        with OUTPUT.open(encoding='utf-8-sig',newline='') as f:
            for r in csv.DictReader(f,delimiter=';'):
                if r['Локация'] in venues:
                    rows[(r['Локация'],r['Номер протокола'])]=r
    st=json.loads(STATE.read_text(encoding='utf-8')) if STATE.exists() else {'discovered':[],'history_attempts':{},'event_attempts':{}}
    sess=requests.Session()
    sess.headers.update(HEADERS)
    max_hist=int(os.getenv('HISTORY_VENUE_BATCH','12'))
    max_events=int(os.getenv('HISTORY_EVENT_BATCH','35'))
    done=set(st['discovered'])
    todo=[v for v in venues if v not in done]
    todo.sort(key=lambda v:(st['history_attempts'].get(v,0),v))
    for slug in todo[:max_hist]:
        try:
            events=history(sess,slug)
            for n,(dt,count) in events.items():
                key=(slug,str(n))
                if key not in rows:
                    rows[key]=dict(zip(FIELDS,[slug,str(n),dt,f'https://www.parkrun.org.uk/{slug}/results/{n}/',count,VALID if count else UNVERIFIED]))
                elif count and not rows[key]['Участники']:
                    rows[key]['Участники']=count
                    rows[key]['Статус']=VALID
            st['discovered'].append(slug)
            st['history_attempts'].pop(slug,None)
            print('HISTORY',slug,'events',len(events),flush=True)
        except Exception as e:
            st['history_attempts'][slug]=st['history_attempts'].get(slug,0)+1
            print('HISTORY RETRY',slug,str(e)[:220],flush=True)
        atomic_csv(rows);atomic_state(st)
        time.sleep(1)
    # Backfill empty counts from individual result pages, without fabricating counts.
    pending=[r for r in rows.values() if r['Статус']!=VALID]
    pending.sort(key=lambda r:(st['event_attempts'].get(r['Ссылка'],0),r['Локация'],int(r['Номер протокола'])))
    for r in pending[:max_events]:
        url=r['Ссылка']
        try:
            n=event_count(sess,url,r['Номер протокола'])
            r['Участники']=str(n)
            r['Статус']=VALID
            print('COUNT',r['Локация'],r['Номер протокола'],n,flush=True)
        except Exception as e:
            st['event_attempts'][url]=st['event_attempts'].get(url,0)+1
            print('COUNT RETRY',r['Локация'],r['Номер протокола'],str(e)[:150],flush=True)
        atomic_csv(rows);atomic_state(st)
        time.sleep(1)
    print('HISTORICAL SUMMARY',{'venues_discovered':len(st['discovered']),'venues_target':len(venues),
          'events':len(rows),'verified_counts':sum(r['Статус']==VALID for r in rows.values()),
          'pending_counts':sum(r['Статус']!=VALID for r in rows.values())},flush=True)
if __name__=='__main__':main()
