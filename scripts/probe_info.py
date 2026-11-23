import urllib.request, re, html
UA={"User-Agent":"Mozilla/5.0 (compatible; poe-market-trends/1.0; +https://github.com/Strix-YT/poe-market-trends)"}
def get(u):
    with urllib.request.urlopen(urllib.request.Request(u,headers=UA),timeout=60) as r: return r.status, r.read().decode('utf-8','replace')
try:
    st,t=get("https://poedb.tw/robots.txt"); print("ROBOTS",st); print(t[:1500])
except Exception as e: print("ROBOTS fail",e)
for n in ["Golden_Oil","Tattoo_of_the_Tasalio_Scout","House_of_Mirrors","Mirror_Shard","Kalguuran_Runegraft_of_Bellows","Mageblood","Delirium_Orb_of_Diviners"]:
    try:
        st,t=get("https://poedb.tw/us/"+n)
        print("PAGE",n,st,len(t))
        for cls in ['explicitMod','implicitMod','enchantMod','flavourText','divinationCard','itemboxstats','secDescrText','descrText','property']:
            m=[html.unescape(re.sub('<[^>]+>',' ',x)).strip() for x in re.findall(r'<div class="'+cls+r'"[^>]*>(.*?)</div>',t,re.S)][:4]
            if m: print("   ",cls,m)
        i=t.find('itemboxstats'); print("   RAW", re.sub(r'\s+',' ',t[i-200:i+1200]) if i>0 else t[:600].replace('\n',' '))
    except Exception as e: print("PAGE fail",n,e)
