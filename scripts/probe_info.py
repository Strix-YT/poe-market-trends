import urllib.request, urllib.parse, re
UA={"User-Agent":"Mozilla/5.0 (compatible; poe-market-trends/1.0; +https://github.com/Strix-YT/poe-market-trends)"}
def get(u):
    with urllib.request.urlopen(urllib.request.Request(u,headers=UA),timeout=60) as r: return r.read().decode('utf-8','replace')
for n in ["Golden Oil","House of Mirrors","Runegraft of Blasphemy","Cartographer's Delirium Orb","Allflame Ember of Flesh","Burial Medallion","Omen of Amelioration","Feral Astrolabe","Pristine Fossil","Chaos Orb"]:
    u="https://poedb.tw/us/"+urllib.parse.quote(n.replace(' ','_'),safe="_")
    try:
        t=get(u); i=t.find('class="explicitMod"'); j=t.find('class="property"'); k=min([x for x in (i,j) if x>0] or [0])
        print("PAGE",n,len(t))
        print("  CTX", re.sub(r'\s+',' ',t[max(0,k-1500):k+2500]))
    except Exception as e: print("PAGE fail",n,u,e)
