// Character import for Exile Market (https://strix-yt.github.io/poe-market-trends/).
// Reads PUBLIC Path of Exile profiles the same way Path of Building does, one request per click from the site.
// No cookies or session ids are ever accepted or sent. Responses are cached briefly to keep load on GGG low.
//
//   GET /characters?account=Name%231234            -> [{name, class, league, level, ...}]
//   GET /character?account=Name%231234&name=Char   -> {character, items, passives: {hashes, items, ...}}

const GGG = 'https://www.pathofexile.com/character-window';
const UA = 'poe-market-trends/1.0 (+https://github.com/Strix-YT/poe-market-trends; contact: Strix-YT@users.noreply.github.com)';
const ALLOWED_ORIGINS = ['https://strix-yt.github.io'];
const TTL = { characters: 300, character: 120 };   // fresh for this long
const KEEP = 86400;                                 // kept a day, served when GGG says "slow down"

const ACCOUNT_RE = /^[^\s#/?&]{2,40}#\d{4}$/;
const NAME_RE = /^[^\s/?&#\\<>"]{1,40}$/;

function cors(req) {
  const o = req.headers.get('Origin') || '';
  return {
    'Access-Control-Allow-Origin': ALLOWED_ORIGINS.includes(o) || o.startsWith('http://127.0.0.1') || o.startsWith('http://localhost') ? o : ALLOWED_ORIGINS[0],
    'Access-Control-Allow-Methods': 'GET, OPTIONS',
    'Access-Control-Expose-Headers': 'X-Cache, X-Fetched-At, Retry-After',
    'Vary': 'Origin',
  };
}
const json = (req, body, status = 200, extra = {}) =>
  new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json; charset=utf-8', ...cors(req), ...extra } });

async function ggg1(path, params) {
  const r = await fetch(`${GGG}/${path}?${new URLSearchParams({ ...params, realm: 'pc' })}`, {
    headers: { 'User-Agent': UA, 'Accept': 'application/json' },
  });
  const text = await r.text();
  let body = null; try { body = JSON.parse(text); } catch (e) {}
  return { status: r.status, body, retry: r.headers.get('Retry-After'), state: r.headers.get('X-Rate-Limit-Ip-State'), rules: r.headers.get('X-Rate-Limit-Ip') };
}
// one quiet retry: the limit is per server address, which Cloudflare shares with other apps
async function ggg(path, params) {
  let r = await ggg1(path, params);
  if (r.status === 429 && (+r.retry || 99) <= 3) { await new Promise((ok) => setTimeout(ok, (+r.retry || 2) * 1000 + 200)); r = await ggg1(path, params); }
  return r;
}

function upstreamError(req, res) {
  if (res.status === 429) return json(req, { error: 'busy', message: 'Path of Exile is limiting requests right now. Try again in a minute.', retryAfter: +res.retry || 60, limit: { state: res.state, rules: res.rules } }, 429, { 'Retry-After': res.retry || '60' });
  if (res.status === 403 || res.status === 404 || (res.body && res.body.error))
    return json(req, { error: 'private', message: 'That profile or character is private or does not exist. Check the name (it is case-sensitive, with the #1234) and that the profile is public.' }, 404);
  return json(req, { error: 'upstream', message: `Path of Exile returned ${res.status}.` }, 502);
}

export default {
  async fetch(req, env, ctx) {
    if (req.method === 'OPTIONS') return new Response(null, { status: 204, headers: cors(req) });
    if (req.method !== 'GET') return json(req, { error: 'method' }, 405);
    const url = new URL(req.url);
    const route = url.pathname.replace(/\/+$/, '').split('/').pop();
    if (route !== 'characters' && route !== 'character') return json(req, { ok: true, routes: ['/characters?account=Name%231234', '/character?account=Name%231234&name=Char'] });

    const account = (url.searchParams.get('account') || '').trim();
    const name = (url.searchParams.get('name') || '').trim();
    if (!ACCOUNT_RE.test(account)) return json(req, { error: 'input', message: 'Account names look like Name#1234.' }, 400);
    if (route === 'character' && !NAME_RE.test(name)) return json(req, { error: 'input', message: 'That character name is not valid.' }, 400);

    // shared cache, keyed on the normalised request only
    const key = new Request(`https://cache.local/${route}?${new URLSearchParams(route === 'character' ? { account, name } : { account })}`);
    const cache = caches.default;
    const hit = await cache.match(key);
    let stale = null;
    if (hit) {
      const at = +hit.headers.get('X-Fetched-At') || 0, b = await hit.text();
      if (Date.now() - at < TTL[route] * 1000) return new Response(b, { headers: { 'Content-Type': 'application/json; charset=utf-8', 'X-Cache': 'hit', 'X-Fetched-At': String(at), ...cors(req) } });
      stale = { b, at };
    }
    const serveStale = (res) => stale && (res.status === 429 || res.status >= 500)
      ? new Response(stale.b, { headers: { 'Content-Type': 'application/json; charset=utf-8', 'X-Cache': 'stale', 'X-Fetched-At': String(stale.at), ...cors(req) } }) : null;

    let out;
    if (route === 'characters') {
      const r = await ggg('get-characters', { accountName: account });
      if (r.status !== 200 || !Array.isArray(r.body)) return serveStale(r) || upstreamError(req, r);
      out = r.body.map((c) => ({ name: c.name, class: c.class, league: c.league, level: c.level, lastLoginTime: c.lastLoginTime }));
    } else {
      const items = await ggg('get-items', { accountName: account, character: name });
      if (items.status !== 200 || !items.body || !items.body.character) return serveStale(items) || upstreamError(req, items);
      const pas = await ggg('get-passive-skills', { accountName: account, character: name });
      const p = pas.status === 200 && pas.body ? pas.body : null;
      out = { character: items.body.character, items: items.body.items || [],
        passives: p ? { hashes: p.hashes || [], hashes_ex: p.hashes_ex || [], items: p.items || [], character: p.character, ascendancy: p.ascendancy, alternate_ascendancy: p.alternate_ascendancy, mastery_effects: p.mastery_effects || {} } : null };
    }
    const body = JSON.stringify(out), now = Date.now();
    ctx.waitUntil(cache.put(key, new Response(body, { headers: { 'Content-Type': 'application/json', 'Cache-Control': `public, max-age=${KEEP}`, 'X-Fetched-At': String(now) } })));
    return new Response(body, { headers: { 'Content-Type': 'application/json; charset=utf-8', 'X-Cache': 'miss', 'X-Fetched-At': String(now), ...cors(req) } });
  },
};
