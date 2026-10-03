// Cloudflare Worker: live availability proxy for the Aranya Vihaara trek map.
//
// The map page (on GitHub Pages) cannot call aranyavihaara.karnataka.gov.in
// directly - the site needs a session cookie + security token and does not
// allow requests from other websites. This Worker keeps that session and
// answers two read-only questions:
//
//   GET /api/blocked?district=17&trek=85              -> ["02-08-2026", ...]
//   GET /api/availability?district=17&trek=85&date=dd-mm-yyyy
//                                                     -> {slots:[...], max_per_booking}
//
// It never books anything and never sends personal details.

const BASE = "https://aranyavihaara.karnataka.gov.in";
const UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 " +
           "(KHTML, like Gecko) Chrome/126.0 Safari/537.36";

// Pages allowed to use this Worker. Add your own domain here if you change it.
const ALLOWED_ORIGINS = [
  "https://modi-fied.github.io",
  "http://127.0.0.1", "http://localhost",
];

// How long answers are shared between visitors (seconds), to go easy on the site.
const CACHE_BLOCKED = 3600;
const CACHE_AVAIL = 120;

let session = null;        // {token, cookie} - reused while this Worker instance lives
let sessionPromise = null;

function cookieHeader(resp) {
  const list = resp.headers.getSetCookie ? resp.headers.getSetCookie()
                                         : [resp.headers.get("set-cookie") || ""];
  return list.map(c => c.split(";")[0]).filter(Boolean).join("; ");
}

async function newSession() {
  const resp = await fetch(BASE + "/", { headers: { "User-Agent": UA, "Accept-Language": "en-IN,en;q=0.9" } });
  const page = await resp.text();
  const m = page.match(/name="_token"[^>]*value="([^"]+)"/) ||
            page.match(/value="([^"]+)"[^>]*name="_token"/) ||
            page.match(/_token['"]?\s*:\s*['"]([^'"]+)['"]/);
  if (!m) {
    const title = (page.match(/<title[^>]*>([\s\S]*?)<\/title>/i) || [])[1] || page.slice(0, 120);
    throw new Error(`Could not find the security token on the home page (site replied ${resp.status}: ` +
                    `${cleanText(title).slice(0, 120)}).`);
  }
  return { token: m[1], cookie: cookieHeader(resp) };
}

async function getSession(force) {
  if (session && !force) return session;
  if (!sessionPromise) {
    sessionPromise = newSession().then(s => (session = s)).finally(() => (sessionPromise = null));
  }
  return sessionPromise;
}

async function post(path, data) {
  for (let attempt = 0; attempt < 2; attempt++) {
    const s = await getSession(attempt > 0);
    const resp = await fetch(BASE + path, {
      method: "POST",
      headers: {
        "User-Agent": UA, "Referer": BASE + "/", "Cookie": s.cookie,
        "Accept": "text/html,application/json,*/*", "Accept-Language": "en-IN,en;q=0.9",
        "Content-Type": "application/x-www-form-urlencoded",
      },
      body: new URLSearchParams({ _token: s.token, ...data }).toString(),
    });
    if ([401, 403, 419].includes(resp.status) && attempt === 0) continue;   // session expired
    if (!resp.ok) throw new Error(`Aranya Vihaara site replied ${resp.status}`);
    return resp.text();
  }
}

function cleanText(s) {
  return (s || "")
    .replace(/<(script|style)[^>]*>[\s\S]*?<\/\1>/gi, " ")
    .replace(/<[^>]+>/g, " ")
    .replace(/&nbsp;| /g, " ")
    .replace(/&amp;/g, "&").replace(/&lt;/g, "<").replace(/&gt;/g, ">")
    .replace(/&quot;/g, '"').replace(/&#0?39;/g, "'")
    .replace(/\s+/g, " ").trim();
}

// Same logic as parse_availability() in trek_map.py
function parseAvailability(page) {
  const slots = [];
  const chunks = page.split(/class="[^"]*\bslot_card\b/);
  for (let chunk of chunks.slice(1)) {
    chunk = chunk.slice(0, 5000);
    const tm = chunk.match(/class="[^"]*\bslot_text\b[^"]*"[^>]*>([\s\S]*?)<\/div>/i);
    const m = cleanText(chunk).match(/(\d+)\s*\/\s*(\d+)/);
    if (m) slots.push({ time: tm ? cleanText(tm[1]) : "", left: +m[1], capacity: +m[2] });
  }
  if (!slots.length) {
    const text = cleanText(page);
    const re = /(\d{1,2}[:.]\d\d\s*[AaPp]\.?\s*[Mm]\.?.{0,30}?)\s+(\d+)\s*\/\s*(\d+)\s*ಲಭ್ಯ/g;
    for (const m of text.matchAll(re)) slots.push({ time: m[1].trim(), left: +m[2], capacity: +m[3] });
  }
  const mp = cleanText(page).match(/(\d+)\s*visitors?\s*per\s*booking/i);
  return { slots, max_per_booking: mp ? +mp[1] : null };
}

function json(obj, status, origin, maxAge) {
  const h = { "Content-Type": "application/json; charset=utf-8",
              "Cache-Control": maxAge ? `public, max-age=${maxAge}` : "no-store" };
  if (origin) { h["Access-Control-Allow-Origin"] = origin; h["Vary"] = "Origin"; }
  return new Response(JSON.stringify(obj), { status, headers: h });
}

export default {
  async fetch(request, env, ctx) {
    const url = new URL(request.url);
    const reqOrigin = request.headers.get("Origin") || "";
    const origin = ALLOWED_ORIGINS.find(o => reqOrigin === o || reqOrigin.startsWith(o + ":")) ? reqOrigin : "";
    if (reqOrigin && !origin) return json({ error: "origin not allowed" }, 403, "");
    if (request.method === "OPTIONS") return new Response(null, { status: 204, headers: {
      "Access-Control-Allow-Origin": origin || "*", "Access-Control-Allow-Methods": "GET" } });
    if (request.method !== "GET") return json({ error: "GET only" }, 405, origin);

    const q = url.searchParams;
    const district = q.get("district") || "", trek = q.get("trek") || "", date = q.get("date") || "";
    if (!/^\d{1,4}$/.test(district) || !/^\d{1,6}$/.test(trek)) return json({ error: "bad district/trek" }, 400, origin);

    let maxAge, work;
    if (url.pathname === "/api/blocked") {
      maxAge = CACHE_BLOCKED;
      work = async () => {
        const data = JSON.parse((await post("/get-blocked-dates", { district_id: district, trek_id: trek })).trim());
        return [...new Set(data.blockedDates || [])].sort();
      };
    } else if (url.pathname === "/api/availability") {
      if (!/^\d\d-\d\d-\d{4}$/.test(date)) return json({ error: "bad date" }, 400, origin);
      maxAge = CACHE_AVAIL;
      work = async () => parseAvailability(await post("/availability", { district, trek, check_in: date }));
    } else {
      return json({ error: "not found" }, 404, origin);
    }

    // Share answers between visitors for a short while
    const cache = caches.default;
    const cacheKey = new Request(url.origin + url.pathname + "?" +
      new URLSearchParams({ district, trek, date }).toString());
    const hit = await cache.match(cacheKey);
    if (hit) return json(await hit.json(), 200, origin, maxAge);

    try {
      const result = await work();
      ctx.waitUntil(cache.put(cacheKey, json(result, 200, "", maxAge)));
      return json(result, 200, origin, maxAge);
    } catch (e) {
      return json({ error: "Could not reach the Aranya Vihaara site: " + e.message }, 502, origin);
    }
  },
};
