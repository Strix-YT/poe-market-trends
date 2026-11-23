import json, urllib.request, urllib.parse
UA={"User-Agent":"poe-market-trends probe (github.com/Strix-YT/poe-market-trends)"}
def get(u):
    with urllib.request.urlopen(urllib.request.Request(u,headers=UA),timeout=60) as r: return json.load(r)
for u in ["https://repoe-fork.github.io/base_items.min.json"]:
    try:
        d=get(u); print("REPOE", u, len(d))
        want={'Chaos Orb','Divine Orb','Mirror Shard','Deafening Essence of Greed','Pristine Fossil','Golden Oil','Omen of Fortune',"Hinekora's Lock",'House of Mirrors','Fracturing Orb','Gilded Divination Scarab','Fragment of the Phoenix','Simulacrum','Tattoo of the Tasalio Scout','Delirium Orb of Diviners' ,'Kalguuran Runegraft of Bellows','Exceptional Eldritch Ember','Chronicle of Atzoatl','Exalted Orb','Orb of Annulment'}
        for k,v in d.items():
            if v.get('name') in want: print("  ",v['name'],'|',v.get('item_class'),'|',json.dumps(v.get('properties'))[:400], '| keys', list(v.keys()))
    except Exception as e: print("REPOE fail",u,e)
B="https://poe.ninja/poe1/api/economy/stash/current/item/overview"
for t in ["DivinationCard","Essence","Fossil","Oil","Scarab","Omen","Tattoo","Runegraft","DeliriumOrb","Artifact","Resonator","UniqueWeapon","AllflameEmber","Astrolabe"]:
    try:
        j=get(B+"?"+urllib.parse.urlencode({"league":"Covens","type":t})); L=j.get("lines",[])
        print("NINJA",t,len(L)); 
        if L: x=L[0]; print("   keys",list(x.keys())); print("   sample",json.dumps({k:x.get(k) for k in ['name','baseType','implicitModifiers','explicitModifiers','flavourText','stackSize','mutatedModifiers','itemClass','levelRequired']})[:700])
    except Exception as e: print("NINJA fail",t,e)
for t in ["Currency","Fragment"]:
    try:
        j=get("https://poe.ninja/poe1/api/economy/stash/current/currency/overview?"+urllib.parse.urlencode({"league":"Covens","type":t}))
        print("NINJA-CUR",t,list(j.keys()), json.dumps((j.get("currencyDetails") or [{}])[0])[:300])
    except Exception as e: print("NINJA-CUR fail",t,e)
    try:
        j=get("https://poe.ninja/poe1/api/economy/exchange/current/overview?"+urllib.parse.urlencode({"league":"Covens","type":t}))
        print("EXCH",t,list(j.keys()), json.dumps((j.get("items") or [{}])[0])[:300])
    except Exception as e: print("EXCH fail",t,e)
