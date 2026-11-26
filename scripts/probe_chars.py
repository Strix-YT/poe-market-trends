import json, urllib.request, urllib.parse
UA="poe-market-trends/1.0 (+https://github.com/Strix-YT/poe-market-trends; contact: Strix-YT@users.noreply.github.com)"
def call(url, data=None):
    req=urllib.request.Request(url, data=urllib.parse.urlencode(data).encode() if data else None, headers={"User-Agent":UA,"Accept":"application/json"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r: b=r.read(); return r.status, dict(r.headers), b
    except urllib.error.HTTPError as e: return e.code, dict(e.headers), e.read()
acct="heygyus#0416"
st,h,b=call("https://www.pathofexile.com/character-window/get-characters", {"accountName":acct,"realm":"pc"})
print("CHARS",st,{k:v for k,v in h.items() if k.lower() in ('content-type','server','cf-mitigated','x-rate-limit-ip','x-rate-limit-ip-state','x-rate-limit-policy','x-rate-limit-rules')}, b[:300])
try:
    chars=json.loads(b); c=[x for x in chars if x.get("league")=="Covens"] or chars
    name="RESURRECTTTTT"; print("first char", json.dumps(c[0])[:300])
    st,h,b=call("https://www.pathofexile.com/character-window/get-items", {"accountName":acct,"character":name,"realm":"pc"})
    print("ITEMS-RAW",st,len(b),b[:400])
    st,h,b=call("https://www.pathofexile.com/character-window/get-items?"+urllib.parse.urlencode({"accountName":acct,"character":name,"realm":"pc"}))
    print("ITEMS-GET",st,len(b),b[:400])
    try:
        j=json.loads(b); it=j.get("items") or []; print("ITEMS",len(it), json.dumps(it[0])[:700] if it else '', "char", json.dumps(j.get("character"))[:300])
    except Exception as e: print("items parse",e)
    st,h,b=call("https://www.pathofexile.com/character-window/get-passive-skills?"+urllib.parse.urlencode({"accountName":acct,"character":name,"realm":"pc"}))
    print("PASSIVES",st,len(b),b[:200])
except Exception as e: print("parse fail",e)
