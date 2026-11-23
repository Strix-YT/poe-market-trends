import json, urllib.request, datetime, collections, time
UA={"User-Agent":"poe-market-trends probe (github.com/Strix-YT/poe-market-trends)"}
def get(u):
    t=time.time()
    with urllib.request.urlopen(urllib.request.Request(u,headers=UA),timeout=60) as r: b=r.read()
    return json.loads(b), len(b), time.time()-t
for lg,start in [("Allflame","2026-07-24"),("Mirage","2026-03-06"),("Keepers","2025-10-31"),("Mercenaries","2025-06-13"),("Settlers","2024-07-26")]:
    d=datetime.datetime.fromisoformat(start).replace(tzinfo=datetime.timezone.utc)+datetime.timedelta(days=9,hours=12)
    h=int(d.timestamp())//3600*3600
    try:
        j,n,dt=get(f"https://web.poecdn.com/api/currency-exchange/{h}")
        ms=j.get("markets") or []
        c=collections.Counter(m.get("league") for m in ms)
        print("FX",lg,d.isoformat(),"bytes",n,"secs",round(dt,1),"next",j.get("next_change_id"),"markets",len(ms),"leagues",dict(c.most_common(6)))
        sm=[m for m in ms if m.get("league")==lg][:1]
        if sm: print("   sample",json.dumps(sm[0])[:400])
    except Exception as e: print("FX fail",lg,h,e)
