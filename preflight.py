import requests
from collect import parse, sources, HEADERS

url='https://www.parkrun.org.uk/plean/results/234/'
session=requests.Session()
session.headers.update(HEADERS)
ok=False
for candidate in sources(url):
    try:
        response=session.get(candidate,timeout=15)
        response.raise_for_status()
        record=parse(response.text,url)
        print('LIVE ACCESS OK:',candidate,record,flush=True)
        ok=True
        break
    except Exception as exc:
        print('ACCESS RETRY:',candidate,repr(exc)[:180],flush=True)
print('LIVE ACCESS', 'CONFIRMED' if ok else 'NOT CONFIRMED',flush=True)
