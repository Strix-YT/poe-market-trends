"""Cache item text from poedb.tw (https://poedb.tw/us/) for every currency-exchange item on the site.

Run weekly by .github/workflows/poedb.yml. Reads the live market.json for item names, fetches each item's
poedb page politely (one request every ~1.5 s, refreshing entries older than MAX_AGE_DAYS), and writes
data/poedb.json:  {name: {"ok": bool, "t": "YYYY-MM-DD", "p": [properties], "m": [mod/effect lines], "u": [usage notes], "f": flavour}}

    python scripts/poedb.py [data/poedb.json]
"""
import datetime, html, json, os, re, sys, time, urllib.parse, urllib.request

OUT = sys.argv[1] if len(sys.argv) > 1 else "data/poedb.json"
MARKET_URL = os.environ.get("MARKET_URL", "https://strix-yt.github.io/poe-market-trends/data/market.json")
MAX_AGE_DAYS = int(os.environ.get("MAX_AGE_DAYS", "30"))
MAX_FETCH = int(os.environ.get("MAX_FETCH", "900"))
DELAY = float(os.environ.get("DELAY", "1.5"))
UA = "Mozilla/5.0 (compatible; poe-market-trends/1.0; +https://github.com/Strix-YT/poe-market-trends)"


def slug(name):
    return urllib.parse.quote(name.replace(" ", "_"), safe="_")


def clean(s):
    s = re.sub(r"<br\s*/?>", "\n", s)
    s = re.sub(r"<[^>]+>", "", s)
    s = html.unescape(s).replace("\xa0", " ")
    return re.sub(r"[ \t]+", " ", s).strip()


def parse(page):
    """The first item box on the page that isn't a buff/skill box."""
    for m in re.finditer(r'<div class="newItemPopup (\w*)Popup', page):
        if m.group(1) in ("Gem",):
            continue
        end = page.find('<div class="itemboximage">', m.start())
        seg = page[m.start(): end if end > 0 else m.start() + 8000]
        props = [clean(x) for x in re.findall(r'<div class="property">(.*?)</div>', seg, re.S)]
        mods = []
        for cls in ("enchantMod", "implicitMod", "explicitMod", "craftedMod", "secDescrText"):
            for x in re.findall(rf'<div class="{cls}">(.*?)</div>', seg, re.S):
                mods += [y.strip() for y in clean(x).split("\n")]
        notes = [clean(x) for x in re.findall(r'<div class="default fst-italic">(.*?)</div>', seg, re.S)]
        flav = [clean(x) for x in re.findall(r'<div class="flavourText[^"]*">(.*?)</div>', seg, re.S)]
        mods = [x for x in mods if x and not re.fullmatch(r"[a-z0-9_%+\-]+", x)]   # drop raw stat ids
        return {"p": [p for p in props if p], "m": mods, "u": [n for n in notes if n], "f": " ".join(flav)}
    return None


def main():
    try:
        cache = json.load(open(OUT, encoding="utf-8"))
    except Exception:
        cache = {}
    req = urllib.request.Request(MARKET_URL + f"?t={int(time.time())}", headers={"User-Agent": UA})
    market = json.load(urllib.request.urlopen(req, timeout=60))
    names = ["Divine Orb", "Chaos Orb"] + [i["n"] for i in market["items"] if i["g"] == "Exchange"]
    today = datetime.date.today()
    stale = lambda e: (today - datetime.date.fromisoformat(e.get("t", "2000-01-01"))).days > MAX_AGE_DAYS
    todo = [n for n in dict.fromkeys(names) if n not in cache or stale(cache[n])][:MAX_FETCH]
    print(f"{len(names)} items, {len(todo)} to fetch")
    ok = miss = 0
    for k, n in enumerate(todo):
        url = "https://poedb.tw/us/" + slug(n)
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": UA}), timeout=40) as r:
                info = parse(r.read().decode("utf-8", "replace"))
            cache[n] = {"ok": info is not None, "t": today.isoformat(), **(info or {})}
            ok += info is not None
        except urllib.error.HTTPError as e:
            cache[n] = {"ok": False, "t": today.isoformat()}
            miss += 1
            if e.code == 429:
                print("rate limited; stopping early")
                break
        except Exception as e:
            print(f"warn: {n}: {e}", file=sys.stderr)
        if k % 50 == 0:
            print(f"  {k}/{len(todo)}")
        time.sleep(DELAY)
    os.makedirs(os.path.dirname(OUT) or ".", exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as fh:
        json.dump(dict(sorted(cache.items())), fh, ensure_ascii=False, separators=(",", ":"))
    print(f"done: {ok} parsed, {miss} not found, cache has {len(cache)} items")


if __name__ == "__main__":
    main()
