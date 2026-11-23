"""Hourly prices from past leagues at the same point of the league as today, from GGG's currency exchange.

For every past league (names and start dates from the daily curves index) this fetches the hourly Faustus
digests for league hours [now - BACK_H, now + AHEAD_H], where "now" is the current league's age in hours.
Run daily by .github/workflows/pastfx.yml; the output lives on the orphan `pastfx` branch and is served at
data/pastfx/ by the hourly deploy.

    python scripts/pastfx.py <out_dir> <curves_index.json>

Output (out_dir):
  index.json   {generatedAt, league, start, h0, n, shards, leagues:[{name,start}], div:{league:[chaos per Divine]}}
               hour k of a series is league hour h0+k (hours since that league's start)
  <i>.json     {item: {league: [chaos price per hour]}}   items sharded like the daily curves
  cache/<league>.json  {hour: {item: [price, chaos volume]}}  raw hours already fetched (not deployed)
"""
import datetime, json, os, sys, time, urllib.request

sys.path.insert(0, os.path.dirname(__file__))
import build  # noqa: E402  (reuses the Faustus parsing, name mapping and misprint filter)

OUT = sys.argv[1] if len(sys.argv) > 1 else "pastfx"
CURVES_INDEX = sys.argv[2] if len(sys.argv) > 2 else "curves/index.json"
BACK_H, AHEAD_H = int(os.environ.get("BACK_H", "72")), int(os.environ.get("AHEAD_H", "96"))
SHARDS = 64
MARKET_URL = os.environ.get("MARKET_URL", "https://strix-yt.github.io/poe-market-trends/data/market.json")
MAX_FETCH = int(os.environ.get("MAX_FETCH", "1500"))


def shard(name, n=SHARDS):   # same djb2 hash as the daily curves and the page
    h = 5381
    for ch in name:
        h = ((h * 33) ^ ord(ch)) & 0xFFFFFFFF
    return h % n


def main():
    now = datetime.datetime.now(datetime.timezone.utc)
    start = datetime.datetime.fromisoformat(build.LEAGUE_START).replace(tzinfo=datetime.timezone.utc)
    H = int((now - start).total_seconds() // 3600)
    h0, h1 = max(0, H - BACK_H), H + AHEAD_H
    idx = json.load(open(CURVES_INDEX, encoding="utf-8"))
    leagues = [(l["name"], l["start"], l.get("days") or 9999) for l in idx["leagues"]]
    market = build.get(MARKET_URL + f"?t={int(time.time())}")
    known = {i["n"] for i in market["items"] if i["g"] == "Exchange"} | {"Divine Orb"}
    build.load_meta_names()
    os.makedirs(f"{OUT}/cache", exist_ok=True)
    fetched = 0
    series, divs = {}, {}
    for name, lstart, days in leagues:
        cpath = f"{OUT}/cache/{name}.json"
        try:
            cache = {int(k): v for k, v in json.load(open(cpath, encoding="utf-8")).items()}
        except Exception:
            cache = {}
        cache = {k: v for k, v in cache.items() if h0 <= k <= h1}          # keep only the current window
        t0 = int(datetime.datetime.fromisoformat(lstart).replace(tzinfo=datetime.timezone.utc).timestamp()) // 3600 * 3600
        last = days * 24
        for h in range(h0, min(h1, last) + 1):
            if h in cache or fetched >= MAX_FETCH:
                continue
            try:
                j = build.fx_hour(t0 + h * 3600)
            except Exception as e:
                print(f"warn: {name} hour {h}: {e}", file=sys.stderr)
                continue
            fetched += 1
            rec = {}
            for mid, r in build.fx_prices(j, name).items() if j else []:
                n = build.fx_name(mid)
                if n in known:
                    rec[n] = [build.r4(r["p"]), round(r["v"])]
            cache[h] = rec
            time.sleep(0.15)
        with open(cpath, "w", encoding="utf-8") as fh:
            json.dump({str(k): v for k, v in sorted(cache.items())}, fh, ensure_ascii=False, separators=(",", ":"))
        hours = list(range(h0, h1 + 1))
        names = sorted({n for v in cache.values() for n in v})
        out = {"s": {n: [(cache.get(h) or {}).get(n, [None])[0] for h in hours] for n in names},
               "v": {n: [((cache.get(h) or {}).get(n) or [None, None])[1] for h in hours] for n in names}}
        dropped = build.clean_fx(out, names)
        series[name] = out["s"]
        divs[name] = out["s"].get("Divine Orb") or []
        print(f"{name}: {len(cache)} hours cached, {len(names)} items, {dropped} item-hours dropped")
    shards = [{} for _ in range(SHARDS)]
    for lg, s in series.items():
        for n, arr in s.items():
            if any(x is not None for x in arr):
                shards[shard(n)].setdefault(n, {})[lg] = arr
    for k, sh in enumerate(shards):
        with open(f"{OUT}/{k}.json", "w", encoding="utf-8") as fh:
            json.dump(sh, fh, ensure_ascii=False, separators=(",", ":"))
    index = {"generatedAt": now.isoformat(timespec="seconds"), "league": build.LEAGUE, "start": build.LEAGUE_START,
             "h0": h0, "n": h1 - h0 + 1, "shards": SHARDS, "leagues": [{"name": n, "start": s} for n, s, _ in leagues], "div": divs}
    with open(f"{OUT}/index.json", "w", encoding="utf-8") as fh:
        json.dump(index, fh, separators=(",", ":"))
    print(f"window: league hours {h0}-{h1} (now {H}); {fetched} hours fetched")


if __name__ == "__main__":
    main()
