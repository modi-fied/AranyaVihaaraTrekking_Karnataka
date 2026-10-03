// Talking to aranyavihaara.karnataka.gov.in from the Vercel functions in api/.
// Same logic as trek_map.py. Read-only: never books anything or sends personal details.
//
// The booking site only accepts connections from Indian IP addresses, so these
// functions are pinned to Vercel's Mumbai region in vercel.json.

const BASE = "https://aranyavihaara.karnataka.gov.in";
const UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 " +
           "(KHTML, like Gecko) Chrome/126.0 Safari/537.36";
const MAX_PARALLEL = 3;              // be gentle with the government server
const DISTRICT_ID_SCAN = Array.from({ length: 40 }, (_, i) => i + 1);
const DISTRICT_EN = { 4: "Kalaburagi", 17: "Chikkamagaluru", 21: "Bengaluru Rural", 24: "Dakshina Kannada" };
const TRAIL_LEVEL = { 1: "Easy", 2: "Moderate", 3: "Difficult" };

// Start points worked out by hand (same as KNOWN_COORDS in trek_map.py).
// Other treks use the start of their route (KML) file.
const KNOWN_COORDS = {
  143: [13.333996, 77.418609], 77: [13.425834, 77.505251],
  85: [13.149825, 75.417127], 84: [13.149825, 75.417127],
  98: [12.230029, 75.625144], 114: [13.250410, 75.166678],
  144: [13.286778, 75.359782], 112: [13.151909, 75.304612],
  110: [13.207147, 75.192358], 88: [13.290069, 75.366531],
  113: [13.155547, 75.334280], 126: [17.556916, 77.490127],
  127: [17.531606, 77.465422],
};

// Pages allowed to use these functions. Add your own domain here if you change it.
const ALLOWED_ORIGINS = ["https://modi-fied.github.io", "http://127.0.0.1", "http://localhost"];

let session = null;        // {token, cookie} - reused while this function instance lives
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
  return { token: m[1], cookie: cookieHeader(resp), page };
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

async function pool(items, n, fn) {
  const out = new Array(items.length);
  let i = 0;
  await Promise.all(Array.from({ length: n }, async () => {
    while (i < items.length) { const k = i++; out[k] = await fn(items[k]); }
  }));
  return out;
}

// --------------------------------------------------------------------------
// Trek list (same fields as load_treks() in trek_map.py)
// --------------------------------------------------------------------------
async function startCoords(t) {
  if (KNOWN_COORDS[t.id]) return KNOWN_COORDS[t.id];
  const m = (t.start_point || "").match(/N\s*([\d.]+)\s*,?\s*E\s*([\d.]+)/);
  if (m) return [+m[1], +m[2]];
  if (!t.kml) return null;
  try {
    const kml = await (await fetch(BASE + "/storage/images/" + t.kml, { headers: { "User-Agent": UA } })).text();
    const c = kml.match(/<coordinates>\s*([-\d.]+),([-\d.]+)/) || kml.match(/<gx:coord>\s*([-\d.]+)\s+([-\d.]+)/);
    return c ? [+c[2], +c[1]] : null;
  } catch { return null; }
}

export async function loadTreks() {
  const home = (await getSession(true)).page;
  const names = {};
  const sel = home.match(/<select[^>]*id="district"[\s\S]*?<\/select>/i);
  if (sel) {
    for (const m of sel[0].matchAll(/<option[^>]*value="(\d+)"[^>]*>([\s\S]*?)<\/option>/gi)) names[+m[1]] = cleanText(m[2]);
  }
  const ids = [...new Set([...Object.keys(names).map(Number), ...DISTRICT_ID_SCAN])].sort((a, b) => a - b);
  const results = await pool(ids, MAX_PARALLEL, async d => {
    try { return [d, JSON.parse((await post("/get-treks", { district_id: d })).trim())]; }
    catch { return [d, []]; }
  });
  const raw = [];
  for (const [d, arr] of results) {
    if (!Array.isArray(arr)) continue;
    for (const t of arr) if (t && typeof t === "object" && t.id) raw.push([d, t]);
  }
  const coords = await pool(raw, MAX_PARALLEL, ([, t]) => startCoords(t));
  const treks = raw.map(([d, t], i) => ({
    id: t.id,
    district_id: d,
    district: DISTRICT_EN[d] || names[d] || `District ${d}`,
    district_kn: names[d] || "",
    name: (t.name || "").trim(),
    name_kn: (t.name_kn || "").trim(),
    start_point: (t.start_point || "").replace(/\(N[^)]*\)/g, "").trim(),
    end_point: (t.end_point || "").trim(),
    distance_km: t.distance ?? null,
    duration: (t.duration || "").slice(0, 5),
    level: TRAIL_LEVEL[t.trail_level_id] || "",
    guide: (t.guide_name || "").trim(),
    guide_phone: (t.guide_mobile_no || "").trim(),
    lat: coords[i] ? coords[i][0] : null,
    lon: coords[i] ? coords[i][1] : null,
  }));
  treks.sort((a, b) => a.district.localeCompare(b.district) || a.name.localeCompare(b.name));
  return treks;
}

// --------------------------------------------------------------------------
// Availability
// --------------------------------------------------------------------------
export async function blockedDates(district, trek) {
  const data = JSON.parse((await post("/get-blocked-dates", { district_id: district, trek_id: trek })).trim());
  return [...new Set(data.blockedDates || [])].sort();
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

export async function availability(district, trek, date) {
  return parseAvailability(await post("/availability", { district, trek, check_in: date }));
}

// --------------------------------------------------------------------------
// HTTP helpers for the api/ functions
// --------------------------------------------------------------------------
function json(request, obj, status, maxAge) {
  const h = { "Content-Type": "application/json; charset=utf-8", "Vary": "Origin",
              // s-maxage: Vercel's CDN shares the answer between visitors for a while
              "Cache-Control": maxAge ? `public, max-age=60, s-maxage=${maxAge}` : "no-store" };
  const o = request.headers.get("origin") || "";
  if (ALLOWED_ORIGINS.some(a => o === a || o.startsWith(a + ":"))) h["Access-Control-Allow-Origin"] = o;
  return new Response(JSON.stringify(obj), { status, headers: h });
}

// Runs fn(params) for a GET request and turns the result (or error) into a JSON response.
export async function handle(request, maxAge, fn) {
  const q = new URL(request.url).searchParams;
  const p = { district: q.get("district") || "", trek: q.get("trek") || "", date: q.get("date") || "" };
  try {
    return json(request, await fn(p), 200, maxAge);
  } catch (e) {
    if (e.badRequest) return json(request, { error: e.message }, 400);
    return json(request, { error: "Could not reach the Aranya Vihaara site: " + e.message }, 502);
  }
}

export function check(ok, message) {
  if (!ok) throw Object.assign(new Error(message), { badRequest: true });
}

export const isId = s => /^\d{1,6}$/.test(s);
