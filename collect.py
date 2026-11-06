#!/usr/bin/env python3
"""Verify UK parkrun volunteer rosters, preserving prior audit results."""
import csv, json, os, random, re, sys, tempfile, time
from collections import Counter
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse
import requests
from bs4 import BeautifulSoup

POS = 'Подтверждён parkwalker'
NEG = 'Проверено: parkwalker отсутствует'
UNK = 'НЕ ПРОВЕРЕНО'
FIELDS = ['Локация','Номер протокола','Дата','Ссылка','Статус','Участники','Среднее время','Последнее время']
HEADERS = {'User-Agent': 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/131.0 Safari/537.36',
           'Accept-Language': 'en-GB,en;q=0.9'}
DATE_RE = re.compile(r'(20\d{2}-\d\d-\d\d|\d{1,2}/\d{1,2}/\d{2,4}|\d{1,2}\.\d{1,2}\.20\d{2})')
TIME_RE = re.compile(r'(?<!\d)(?:(\d{1,2}):)?(\d{1,2}):(\d{2})(?!\d)')

def clean(node):
    return node.get_text(' ',strip=True) if node else ''

def event_date(value):
    if not value: return ''
    for fmt in ('%Y-%m-%d','%m/%d/%Y','%m/%d/%y'):
        try: return datetime.strptime(value,fmt).strftime('%d.%m.%Y')
        except ValueError: pass
    if '.' in value:
        p=value.split('.')
        if len(p)==3 and int(p[0])>12:
            try: return datetime.strptime(value,'%d.%m.%Y').strftime('%d.%m.%Y')
            except ValueError: pass
    return ''

def parse(html,url):
    num = re.search(r'/results/(\d+)/?',url)
    if not num: raise ValueError('Invalid event URL')
    num=num.group(1)
    soup=BeautifulSoup(html,'html.parser')
    body=clean(soup)
    if len(body)<300 or re.search('confirm you are human|captcha|complete the security check',body,re.I):
        raise ValueError('CAPTCHA or incomplete page')
    heads=' '.join(clean(h) for h in soup.find_all(['h1','h2','h3'],limit=30))
    if not re.search(r'(?:#|№)\s*'+num+r'(?!\d)',heads):
        raise ValueError('Wrong event or missing event heading')
    volunteers=soup.select_one('#volunteers-table')
    table=volunteers if volunteers and volunteers.name=='table' else volunteers.find('table') if volunteers else None
    if table is None:
        for candidate in soup.find_all('table'):
            hdr=clean(candidate.find('thead')).lower()
            if ('role' in hdr or 'rolle' in hdr or 'роль' in hdr) and ('volunteer' in hdr or 'freiwill' in hdr or 'волонт' in hdr):
                table=candidate;break
    if table is None:
        for heading in soup.find_all(['h2','h3','h4']):
            if re.search('thanks to the volunteers|dank an die freiwilligen|спасибо волонт',clean(heading),re.I):
                table=heading.find_next('table');break
    if table is None: raise ValueError('Missing volunteer roster')
    tr=table.find_all('tr')
    if len(tr)<2: raise ValueError('Incomplete volunteer roster')
    hdr=[clean(c).lower() for c in tr[0].find_all(['th','td'],recursive=False)]
    ri=next((i for i,h in enumerate(hdr) if any(s in h for s in ('role','rolle','роль','aufgabe'))),None)
    if ri is None and not any(any(s in h for s in ('volunteer','freiwill','волонт')) for h in hdr):
        raise ValueError('Unidentified roster columns')
    roles=[]
    for row in tr[1:]:
        cells=row.find_all('td',recursive=False)
        if cells:
            roles.append(clean(cells[ri]) if ri is not None and ri<len(cells) else ' '.join(clean(c) for c in cells))
    if not roles: raise ValueError('No volunteer entries')
    status=POS if any(re.search(r'\bparkwalker\b',r,re.I) for r in roles) else NEG
    date=''
    for h in soup.find_all(['h2','h3']):
        val=clean(h)
        if re.search(r'(?:#|№)\s*'+num+r'(?!\d)',val):
            m=DATE_RE.search(val)
            if m: date=event_date(m.group(1))
            break
    m=re.search(r'\b(\d{1,5})\s*(?:finishers|finisher|финишер)',body,re.I)
    count=int(m.group(1)) if m else None
    times=[]
    for tb in soup.find_all('table'):
        if tb==table: continue
        hdr=[clean(x).lower() for x in (tb.find('thead') or tb.find('tr')).find_all(['th','td'])]
        if not any('position' in h or 'позици' in h or 'platz' in h for h in hdr):continue
        ix=next((i for i,h in enumerate(hdr) if any(v in h for v in ('time','время','zeit'))),None)
        if ix is None:continue
        for rr in tb.find_all('tr'):
            cs=rr.find_all('td',recursive=False)
            if len(cs)<=ix:continue
            match=TIME_RE.search(clean(cs[ix]))
            if match:
                sec=int(match.group(3))+60*int(match.group(2))+3600*int(match.group(1) or 0)
                if 500<=sec<=18000: times.append(sec)
        break
    def fmt(s):
        if s is None:return ''
        return (str(s//3600)+':' if s>=3600 else '')+f'{s//60%60:02}:{s%60:02}'
    return {'status':status,'date':date,'finishers':count,
            'average':fmt(round(sum(times)/len(times))) if times else '',
            'last':fmt(times[-1]) if times else ''}

def sources(url):
    yield url
    path=urlparse(url).path
    for lang in ('de','ru','fr'):
        yield f'https://www-parkrun-org-uk.translate.goog{path}?_x_tr_sl=en&_x_tr_tl={lang}&_x_tr_hl={lang}'

def write_csv(path,rows,fields):
    with tempfile.NamedTemporaryFile('w',encoding='utf-8-sig',newline='',delete=False,dir='.') as t:
        w=csv.DictWriter(t,fieldnames=fields,delimiter=';',extrasaction='ignore')
        w.writeheader();w.writerows(rows)
        name=t.name
    os.replace(name,path)

def main():
    path=Path('parkwalker.csv')
    if not path.exists():
        print('ERROR: upload the original 1909-row parkwalker.csv to the repository root before running.',file=sys.stderr)
        sys.exit(2)
    with path.open(encoding='utf-8-sig',newline='') as f:
        r=csv.DictReader(f,delimiter=';')
        fields=r.fieldnames
        rows=list(r)
    if len(rows)<1909 or not all(k in fields for k in FIELDS):
        print('ERROR: missing original 1909 records or standard CSV columns; refusing to overwrite.',file=sys.stderr)
        sys.exit(2)
    attempts=Path('attempts.json')
    tries=json.loads(attempts.read_text()) if attempts.exists() else {}
    # Finish a partially verified venue before starting another one.
    # A venue's identity comes from its results URL, not its display name.
    def venue(row):
        return urlparse(row['Ссылка']).path.strip('/').split('/')[0].lower()

    grouped={}
    for index,row in enumerate(rows):
        key=venue(row)
        g=grouped.setdefault(key,{'checked':0,'pending':[]})
        if row['Статус']==UNK:
            g['pending'].append((index,row))
        else:
            g['checked']+=1

    limit=int(os.getenv('AUDIT_LIMIT','60'))
    max_attempts=int(os.getenv('VENUE_FOCUS_ATTEMPTS','5'))
    # Focus on whichever existing partially-checked venue can be finished
    # soonest. Do not let permanently blocked pages starve other venues.
    partial=[]
    for slug,g in grouped.items():
        if g['checked']==0 or not g['pending']:
            continue
        eligible=[p for p in g['pending']
                  if tries.get(p[1]['Ссылка'],0)<max_attempts]
        if eligible:
            partial.append((len(g['pending']),-g['checked'],slug,eligible))
    if partial:
        partial.sort(key=lambda item:item[:3])
        _,_,focus,eligible=partial[0]
        eligible.sort(key=lambda item:(tries.get(item[1]['Ссылка'],0),item[0]))
        queue=[item[1] for item in eligible[:limit]]
        print('VENUE FOCUS',focus,'checked',grouped[focus]['checked'],
              'remaining',len(grouped[focus]['pending']),
              'attempting',len(queue),flush=True)
    else:
        # Unrecoverable pages stay NOT CHECKED, rather than being declared
        # verified. Start another venue only after trying unfinished ones.
        # Prefer never-before-verified venues with fewest remaining results.
        new_venues=[]
        for slug,g in grouped.items():
            if g['checked']==0 and g['pending']:
                elig=[p for p in g['pending']
                      if tries.get(p[1]['Ссылка'],0)<max_attempts]
                if elig: new_venues.append((len(g['pending']),slug,elig))
        if new_venues:
            new_venues.sort(key=lambda item:(item[0],item[1]))
            _,focus,eligible=new_venues[0]
        else:
            # All venues have exhausted normal retries: recheck the least
            # attempted pages, without silently treating them as complete.
            focus='retry backlog'
            eligible=[(i,r) for i,r in enumerate(rows) if r['Статус']==UNK]
        eligible.sort(key=lambda item:(tries.get(item[1]['Ссылка'],0),item[0]))
        queue=[item[1] for item in eligible[:limit]]
        print('VENUE FOCUS',focus,'attempting',len(queue),flush=True)
    sess=requests.Session();sess.headers.update(HEADERS)
    for i,row in enumerate(queue,1):
        url=row['Ссылка']; found=None;errors=[]
        for candidate in sources(url):
            try:
                resp=sess.get(candidate,timeout=16)
                resp.raise_for_status()
                found=parse(resp.text,url)
                break
            except (requests.RequestException,ValueError) as exc:
                errors.append(f'{urlparse(candidate).netloc}:{str(exc)[:70]}')
        tries[url]=tries.get(url,0)+1
        if found is not None:
            row['Статус']=found['status']
            if not row['Дата']:row['Дата']=found['date']
            if not row['Участники'] and found['finishers'] is not None:row['Участники']=str(found['finishers'])
            if not row['Среднее время']:row['Среднее время']=found['average']
            if not row['Последнее время']:row['Последнее время']=found['last']
            print(f'{i}/{len(queue)} VERIFIED {row["Локация"]} #{row["Номер протокола"]} {row["Статус"]}',flush=True)
        else:
            print(f'{i}/{len(queue)} RETRY {url} {"; ".join(errors)}',flush=True)
        if i%5==0:
            write_csv(path,rows,fields)
            attempts.write_text(json.dumps(tries,ensure_ascii=False,indent=2),encoding='utf-8')
        time.sleep(1+random.random())
    write_csv(path,rows,fields)
    attempts.write_text(json.dumps(tries,ensure_ascii=False,indent=2),encoding='utf-8')
    print('SUMMARY',dict(Counter(r['Статус'] for r in rows)),flush=True)

if __name__=='__main__':main()
