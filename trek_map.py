#!/usr/bin/env python3
"""
Aranya Vihaara trek availability map
====================================

Checks ticket availability for every open trek on
https://aranyavihaara.karnataka.gov.in for the dates you pick, and shows the
treks on a map (start point of each trek) coloured by availability.

Run it:
    python trek_map.py              -> opens the interactive map in your browser
    python trek_map.py --dates 02-10-2026,03-10-2026 --tickets 2
                                    -> prints a table in the terminal instead

Only the Python standard library is used - nothing to install.
The tool only READS availability. It never books or sends personal details.
"""

import argparse
import html as htmllib
import http.cookiejar
import json
import os
import re
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

BASE = "https://aranyavihaara.karnataka.gov.in"
HERE = os.path.dirname(os.path.abspath(__file__))
CACHE_FILE = os.path.join(HERE, "trek_cache.json")
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0 Safari/537.36")
MAX_PARALLEL = 3            # be gentle with the government server
TREK_LIST_MAX_AGE = 12 * 3600
DISTRICT_ID_SCAN = range(1, 41)   # scan all ids so seasonal treks appear when they reopen

# English names for districts seen on the site (the site itself returns Kannada)
DISTRICT_EN = {4: "Kalaburagi", 17: "Chikkamagaluru", 21: "Bengaluru Rural",
               24: "Dakshina Kannada"}
TRAIL_LEVEL = {1: "Easy", 2: "Moderate", 3: "Difficult"}

# Start-point coordinates already worked out from each trek's route (KML) file.
# New treks are resolved automatically and saved to trek_cache.json.
KNOWN_COORDS = {
    143: (13.333996, 77.418609), 77: (13.425834, 77.505251),
    85: (13.149825, 75.417127), 84: (13.149825, 75.417127),
    98: (12.230029, 75.625144), 114: (13.250410, 75.166678),
    144: (13.286778, 75.359782), 112: (13.151909, 75.304612),
    110: (13.207147, 75.192358), 88: (13.290069, 75.366531),
    113: (13.155547, 75.334280), 126: (17.556916, 77.490127),
    127: (17.531606, 77.465422),
}

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(errors="replace")


# --------------------------------------------------------------------------
# Talking to the website
# --------------------------------------------------------------------------
class Site:
    """Keeps one browser-like session (cookies + security token) with the site."""

    def __init__(self):
        self.cookies = http.cookiejar.CookieJar()
        self.opener = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(self.cookies))
        self.token = None
        self.lock = threading.Lock()
        self.gate = threading.Semaphore(MAX_PARALLEL)

    def _request(self, path, data=None, timeout=40):
        url = path if path.startswith("http") else BASE + path
        body = urllib.parse.urlencode(data).encode() if data is not None else None
        req = urllib.request.Request(url, data=body, headers={
            "User-Agent": UA, "Referer": BASE + "/",
            "Accept": "text/html,application/json,*/*",
            "Accept-Language": "en-IN,en;q=0.9"})
        if body is not None:
            req.add_header("Content-Type", "application/x-www-form-urlencoded")
        with self.gate:
            with self.opener.open(req, timeout=timeout) as resp:
                text = resp.read().decode("utf-8", "replace")
            time.sleep(0.15)
        return text

    def refresh_token(self):
        page = self._request("/")
        m = (re.search(r'name="_token"[^>]*value="([^"]+)"', page)
             or re.search(r'value="([^"]+)"[^>]*name="_token"', page)
             or re.search(r"_token['\"]?\s*:\s*['\"]([^'\"]+)['\"]", page))
        if not m:
            raise RuntimeError("Could not find the security token on the home page "
                               "(the website may have changed).")
        self.token = m.group(1)
        return page

    def post(self, path, data):
        with self.lock:
            if not self.token:
                self.refresh_token()
            token = self.token
        try:
            return self._request(path, {"_token": token, **data})
        except urllib.error.HTTPError as e:
            if e.code in (401, 403, 419):          # session expired -> new token, retry once
                with self.lock:
                    self.refresh_token()
                    token = self.token
                return self._request(path, {"_token": token, **data})
            raise


def clean_text(s):
    s = re.sub(r"(?is)<(script|style)[^>]*>.*?</\1>", " ", s or "")
    s = re.sub(r"<[^>]+>", " ", s)
    s = htmllib.unescape(s).replace("\xa0", " ")
    return re.sub(r"\s+", " ", s).strip()


def parse_json(text):
    return json.loads(text.strip())


# --------------------------------------------------------------------------
# Trek list (+ start coordinates), cached on disk
# --------------------------------------------------------------------------
def _load_cache():
    try:
        with open(CACHE_FILE, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _save_cache(cache):
    try:
        with open(CACHE_FILE, "w", encoding="utf-8") as f:
            json.dump(cache, f, ensure_ascii=False, indent=1)
    except Exception as e:
        print("  (could not save cache:", e, ")")


def _coords_for(site, trek, cache_coords):
    tid = trek["id"]
    if str(tid) in cache_coords:
        return cache_coords[str(tid)]
    if tid in KNOWN_COORDS:
        return list(KNOWN_COORDS[tid])
    sp = trek.get("start_point") or ""
    m = re.search(r"N\s*([\d.]+)\s*,?\s*E\s*([\d.]+)", sp)
    if m:
        return [float(m.group(1)), float(m.group(2))]
    if trek.get("kml"):
        try:
            kml = site._request("/storage/images/" + trek["kml"])
            m = (re.search(r"<coordinates>\s*([-\d.]+),([-\d.]+)", kml)
                 or re.search(r"<gx:coord>\s*([-\d.]+)\s+([-\d.]+)", kml))
            if m:
                return [float(m.group(2)), float(m.group(1))]
        except Exception:
            pass
    return None


def load_treks(site, force=False):
    cache = _load_cache()
    if (not force and cache.get("treks")
            and time.time() - cache.get("fetched_at", 0) < TREK_LIST_MAX_AGE):
        return cache["treks"]

    print("Fetching the list of open treks ...")
    home = site.refresh_token()
    names = {}
    sel = re.search(r'(?is)<select[^>]*id="district".*?</select>', home)
    if sel:
        for v, t in re.findall(r'(?is)<option[^>]*value="(\d+)"[^>]*>(.*?)</option>', sel.group(0)):
            names[int(v)] = clean_text(t)
    district_ids = sorted(set(names) | set(DISTRICT_ID_SCAN))

    def fetch_district(d):
        try:
            return d, parse_json(site.post("/get-treks", {"district_id": d}))
        except Exception:
            return d, []

    with ThreadPoolExecutor(MAX_PARALLEL) as ex:
        results = list(ex.map(fetch_district, district_ids))

    coords_cache = cache.get("coords", {})
    treks = []
    for d, arr in results:
        if not isinstance(arr, list):
            continue
        for t in arr:
            if not isinstance(t, dict) or not t.get("id"):
                continue
            ll = _coords_for(site, t, coords_cache)
            if ll:
                coords_cache[str(t["id"])] = ll
            dur = (t.get("duration") or "")[:5]
            treks.append({
                "id": t["id"],
                "district_id": d,
                "district": DISTRICT_EN.get(d) or names.get(d) or f"District {d}",
                "district_kn": names.get(d, ""),
                "name": (t.get("name") or "").strip(),
                "name_kn": (t.get("name_kn") or "").strip(),
                "start_point": re.sub(r"\(N[^)]*\)", "", t.get("start_point") or "").strip(),
                "end_point": (t.get("end_point") or "").strip(),
                "distance_km": t.get("distance"),
                "duration": dur,
                "level": TRAIL_LEVEL.get(t.get("trail_level_id"), ""),
                "guide": (t.get("guide_name") or "").strip(),
                "guide_phone": (t.get("guide_mobile_no") or "").strip(),
                "lat": ll[0] if ll else None,
                "lon": ll[1] if ll else None,
            })
    treks.sort(key=lambda x: (x["district"], x["name"]))
    cache.update({"fetched_at": time.time(), "treks": treks, "coords": coords_cache})
    _save_cache(cache)
    print(f"  {len(treks)} open treks found.")
    return treks


# --------------------------------------------------------------------------
# Availability
# --------------------------------------------------------------------------
def blocked_dates(site, district_id, trek_id):
    data = parse_json(site.post("/get-blocked-dates",
                                {"district_id": district_id, "trek_id": trek_id}))
    return sorted(set(data.get("blockedDates") or []))


def parse_availability(page):
    """Return (slots, max_per_booking) from the /availability result page."""
    slots = []
    chunks = re.split(r'class="[^"]*\bslot_card\b', page)
    for chunk in chunks[1:]:
        chunk = chunk[:5000]
        tm = re.search(r'(?is)class="[^"]*\bslot_text\b[^"]*"[^>]*>(.*?)</div>', chunk)
        text = clean_text(chunk)
        m = re.search(r"(\d+)\s*/\s*(\d+)", text)
        if m:
            slots.append({"time": clean_text(tm.group(1)) if tm else "",
                          "left": int(m.group(1)), "capacity": int(m.group(2))})
    if not slots:   # fallback: look for "<time> 123/300 ಲಭ್ಯವಿದೆ" in the plain text
        text = clean_text(page)
        for m in re.finditer(r"(\d{1,2}[:.]\d\d\s*[AaPp]\.?\s*[Mm]\.?.{0,30}?)\s+(\d+)\s*/\s*(\d+)\s*ಲಭ್ಯ", text):
            slots.append({"time": m.group(1).strip(), "left": int(m.group(2)),
                          "capacity": int(m.group(3))})
    mp = re.search(r"(\d+)\s*visitors?\s*per\s*booking", clean_text(page), re.I)
    return slots, (int(mp.group(1)) if mp else None)


def availability(site, district_id, trek_id, date):
    page = site.post("/availability", {"district": district_id, "trek": trek_id,
                                       "check_in": date})
    slots, max_pb = parse_availability(page)
    return {"slots": slots, "max_per_booking": max_pb}


# --------------------------------------------------------------------------
# Local web server for the map
# --------------------------------------------------------------------------
SITE = Site()


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def _send(self, code, body, ctype="application/json; charset=utf-8"):
        data = body.encode("utf-8") if isinstance(body, str) else body
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _json(self, obj, code=200):
        self._send(code, json.dumps(obj, ensure_ascii=False))

    def do_GET(self):
        u = urllib.parse.urlparse(self.path)
        q = {k: v[0] for k, v in urllib.parse.parse_qs(u.query).items()}
        try:
            if u.path in ("/", "/index.html"):
                return self._send(200, PAGE, "text/html; charset=utf-8")
            if u.path == "/api/treks":
                return self._json(load_treks(SITE, force=q.get("refresh") == "1"))
            if u.path == "/api/blocked":
                return self._json(blocked_dates(SITE, q["district"], q["trek"]))
            if u.path == "/api/availability":
                if not re.fullmatch(r"\d\d-\d\d-\d{4}", q.get("date", "")):
                    return self._json({"error": "bad date"}, 400)
                return self._json(availability(SITE, q["district"], q["trek"], q["date"]))
            self._send(404, "not found", "text/plain")
        except urllib.error.URLError as e:
            self._json({"error": f"Could not reach the Aranya Vihaara site: {e}"}, 502)
        except Exception as e:
            self._json({"error": str(e)}, 500)


def serve(open_browser=True):
    server = None
    for port in range(8765, 8785):
        try:
            server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
            break
        except OSError:
            continue
    if not server:
        sys.exit("No free local port found (8765-8784).")
    url = f"http://127.0.0.1:{server.server_address[1]}/"
    print("Trek availability map is running at", url)
    print("Keep this window open while you use the map. Press Ctrl+C to stop.")
    if open_browser:
        threading.Timer(0.8, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")


# --------------------------------------------------------------------------
# Terminal mode
# --------------------------------------------------------------------------
def cli(dates, tickets):
    treks = load_treks(SITE)
    print(f"\nChecking {len(treks)} treks x {len(dates)} date(s) for {tickets} ticket(s)...\n")

    def check(t):
        try:
            bl = set(blocked_dates(SITE, t["district_id"], t["id"]))
        except Exception:
            bl = set()
        row = {}
        for d in dates:
            if d in bl:
                row[d] = "closed"
                continue
            try:
                a = availability(SITE, t["district_id"], t["id"], d)
                best = max([s["left"] for s in a["slots"]], default=None)
                row[d] = "closed" if best is None else (
                    f"{best} left" + ("" if best >= tickets else " (too few)") if best else "sold out")
            except Exception as e:
                row[d] = "error"
        return t, row

    with ThreadPoolExecutor(MAX_PARALLEL) as ex:
        rows = list(ex.map(check, treks))
    w = max(len(t["name"]) for t in treks) + 2
    print("Trek".ljust(w) + "".join(d.ljust(20) for d in dates))
    print("-" * (w + 20 * len(dates)))
    for t, row in rows:
        print(t["name"].ljust(w) + "".join(row[d].ljust(20) for d in dates))


# --------------------------------------------------------------------------
# The map page
# --------------------------------------------------------------------------
PAGE = r"""<!doctype html>
<html lang="en"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Trek Availability Map</title>
<link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/leaflet/1.9.4/leaflet.min.css">
<script src="https://cdnjs.cloudflare.com/ajax/libs/leaflet/1.9.4/leaflet.min.js"></script>
<style>
:root{--bg:#f6f5f0;--panel:#fff;--ink:#1f2a24;--muted:#66706a;--line:#e3e1d8;--accent:#1c6b3c;
 --ok:#1e9e57;--part:#e0a100;--none:#d6453d;--idle:#9aa39e}
*{box-sizing:border-box}html,body{margin:0;height:100%;font:14px/1.45 system-ui,-apple-system,Segoe UI,Roboto,sans-serif;color:var(--ink);background:var(--bg)}
#app{display:grid;grid-template-columns:380px 1fr;height:100vh}
aside{background:var(--panel);border-right:1px solid var(--line);display:flex;flex-direction:column;min-height:0}
.head{padding:16px 18px 10px;border-bottom:1px solid var(--line)}
h1{font-size:18px;margin:0}.sub{color:var(--muted);font-size:12px}
.controls{padding:12px 18px;border-bottom:1px solid var(--line)}
.lbl{font-size:12px;font-weight:600;color:var(--muted);text-transform:uppercase;letter-spacing:.04em;margin:0 0 6px;display:flex;justify-content:space-between}
.lbl a{text-transform:none;font-weight:500;color:var(--accent);cursor:pointer}
.calnav{display:flex;align-items:center;gap:6px;margin-bottom:6px}
.calnav button{border:1px solid var(--line);background:#fff;border-radius:8px;width:30px;height:30px;cursor:pointer;font-size:16px;color:var(--ink)}
.calnav button:disabled{opacity:.35;cursor:default}
.calnav select{flex:1;padding:5px 6px}
.chips{display:grid;grid-template-columns:repeat(7,1fr);gap:4px}
.wd{font-size:10px;color:var(--muted);text-align:center;font-weight:600}
.chip{border:1px solid var(--line);background:#fff;border-radius:8px;padding:6px 0;cursor:pointer;font:600 13px system-ui;color:var(--ink)}
.chip.wk{background:#f3f7f1}.chip.on{background:var(--accent);border-color:var(--accent);color:#fff}
.chip.off{background:transparent;border-color:transparent;color:#b5bab6;cursor:not-allowed;font-weight:400}
.chip.today{outline:2px solid #c9d8cd;outline-offset:-2px}
.calhint{font-size:11px;color:var(--muted);margin-top:6px}
.row{display:flex;gap:10px;margin-top:12px;align-items:end}
.row label{flex:1;font-size:12px;color:var(--muted);display:flex;flex-direction:column;gap:4px}
input,select{font:inherit;padding:6px 8px;border:1px solid var(--line);border-radius:8px;background:#fff;color:var(--ink)}
button.go{margin-top:12px;width:100%;padding:10px;border:0;border-radius:10px;background:var(--accent);color:#fff;font:600 14px system-ui;cursor:pointer}
button.go:disabled{opacity:.6;cursor:wait}
#status{font-size:12px;color:var(--muted);margin-top:8px;min-height:18px}
.bar{height:4px;background:var(--line);border-radius:4px;overflow:hidden;margin-top:4px;display:none}.bar i{display:block;height:100%;width:0;background:var(--accent);transition:width .2s}
#list{overflow:auto;flex:1;padding:6px 10px 14px}
.item{display:flex;gap:10px;padding:9px 8px;border-radius:10px;cursor:pointer;align-items:flex-start}
.item:hover,.item.sel{background:#f1f4ef}
.dot{width:12px;height:12px;border-radius:50%;margin-top:4px;flex:none;border:2px solid #fff;box-shadow:0 0 0 1px rgba(0,0,0,.15)}
.item .n{font-weight:600}.item .m{font-size:12px;color:var(--muted)}
.days{display:flex;gap:3px;margin-top:4px;flex-wrap:wrap}
.days em{font-style:normal;font-size:11px;padding:1px 6px;border-radius:6px;background:#eee;color:#333}
.days .ok{background:#dff3e6;color:#11643a}.days .few{background:#fdf0cc;color:#7a5a00}.days .full,.days .closed{background:#fbe1df;color:#8f2621}.days .closed{background:#eceeed;color:#666}
.group{font-size:11px;font-weight:700;color:var(--muted);text-transform:uppercase;letter-spacing:.05em;padding:12px 8px 4px}
#mapwrap{position:relative;min-height:0}
#map{position:absolute;inset:0}
.legend{position:absolute;right:12px;bottom:22px;z-index:500;background:#fff;border:1px solid var(--line);border-radius:10px;padding:8px 10px;font-size:12px;box-shadow:0 2px 8px rgba(0,0,0,.08)}
.legend div{display:flex;align-items:center;gap:6px}.legend .dot{margin:0;width:10px;height:10px}
#detail{position:absolute;top:12px;right:12px;width:380px;max-height:calc(100% - 24px);overflow:auto;z-index:600;background:#fff;border:1px solid var(--line);border-radius:14px;box-shadow:0 6px 24px rgba(0,0,0,.14);display:none}
#detail .top{padding:14px 16px 8px;position:relative}
#detail h2{font-size:17px;margin:0 28px 2px 0}#detail .kn{color:var(--muted);font-size:13px}
#detail .x{position:absolute;right:10px;top:8px;border:0;background:none;font-size:22px;cursor:pointer;color:var(--muted)}
#detail .facts{display:grid;grid-template-columns:1fr 1fr;gap:6px 12px;padding:4px 16px 10px;font-size:13px}
#detail .facts b{display:block;font-size:11px;color:var(--muted);font-weight:600;text-transform:uppercase}
#detail iframe{width:100%;height:210px;border:0;display:block;border-top:1px solid var(--line);border-bottom:1px solid var(--line)}
.btns{display:flex;gap:8px;padding:10px 16px}
.btns a{flex:1;text-align:center;padding:8px;border-radius:9px;text-decoration:none;font-weight:600;font-size:13px;border:1px solid var(--line);color:var(--ink)}
.btns a.pri{background:#1a73e8;border-color:#1a73e8;color:#fff}
table{width:calc(100% - 32px);margin:4px 16px 14px;border-collapse:collapse;font-size:13px}
td,th{padding:5px 4px;border-bottom:1px solid var(--line);text-align:left;vertical-align:top}th{font-size:11px;color:var(--muted);text-transform:uppercase}
.note{font-size:12px;color:var(--muted);padding:0 16px 14px}
@media (max-width:800px){#app{grid-template-columns:1fr;grid-template-rows:auto 55vh}aside{max-height:none}#list{max-height:40vh}#detail{left:8px;right:8px;width:auto}}
</style></head>
<body><div id="app">
<aside>
 <div class="head"><h1>Trek availability</h1><div class="sub">Aranya Vihaara · Karnataka Forest Department · live from the booking site</div></div>
 <div class="controls">
  <div class="lbl">Dates <a id="clear">clear</a></div>
  <div class="calnav"><button id="prev" title="Previous month">&#8249;</button><select id="month"></select><select id="year"></select><button id="next" title="Next month">&#8250;</button></div>
  <div id="dates" class="chips"></div>
  <div class="calhint" id="calhint"></div>
  <div class="row">
   <label>Tickets needed<input id="tickets" type="number" min="1" max="50" value="2"></label>
   <label>Show treks open on<select id="mode"><option value="all">all selected dates</option><option value="any">any selected date</option></select></label>
  </div>
  <button class="go" id="go">Check availability</button>
  <div id="status">Pick one or more dates, then check.</div><div class="bar" id="bar"><i></i></div>
 </div>
 <div id="list"></div>
</aside>
<div id="mapwrap"><div id="map"></div>
 <div class="legend">
  <div><span class="dot" style="background:var(--ok)"></span>Enough tickets</div>
  <div><span class="dot" style="background:var(--part)"></span>Some dates / fewer seats</div>
  <div><span class="dot" style="background:var(--none)"></span>Sold out or closed</div>
  <div><span class="dot" style="background:var(--idle)"></span>Not checked yet</div>
 </div>
 <div id="detail"></div>
</div></div>
<script>
const $=s=>document.querySelector(s);
const esc=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const pad=n=>String(n).padStart(2,'0');
const key=d=>pad(d.getDate())+'-'+pad(d.getMonth()+1)+'-'+d.getFullYear();
const sortKey=k=>k.split('-').reverse().join('');
const pretty=k=>{const [d,m,y]=k.split('-');return new Date(+y,m-1,+d).toLocaleDateString('en-IN',{weekday:'short',day:'numeric',month:'short'})};
const COLORS={ok:'#1e9e57',part:'#e0a100',none:'#d6453d',idle:'#9aa39e'};
const FRESH_MS=5*60*1000;
let TREKS=[],markers={},blocked={},avail={},selected=new Set(),openId=null,busy=false;

const map=L.map('map',{zoomControl:true}).setView([14.6,76.3],7);
L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png',{maxZoom:18,attribution:'&copy; OpenStreetMap contributors'}).addTo(map);

const MONTHS=['January','February','March','April','May','June','July','August','September','October','November','December'];
let view=null;
function windowDates(){const now=new Date(),lead=now.getHours()<12?1:2;
  return{now,min:new Date(now.getFullYear(),now.getMonth(),now.getDate()+lead),max:new Date(now.getFullYear(),now.getMonth(),now.getDate()+30)};}
function buildDates(){
  const w=windowDates();
  if(!view)view={y:w.min.getFullYear(),m:w.min.getMonth()};
  const ms=$('#month'),ys=$('#year');
  if(!ms.options.length){MONTHS.forEach((n,i)=>ms.add(new Option(n,i)));
    for(let y=w.now.getFullYear()-1;y<=w.now.getFullYear()+2;y++)ys.add(new Option(y,y));
    ms.onchange=()=>{view.m=+ms.value;buildDates()};ys.onchange=()=>{view.y=+ys.value;buildDates()};
    $('#prev').onclick=()=>{view.m--;if(view.m<0){view.m=11;view.y--}buildDates()};
    $('#next').onclick=()=>{view.m++;if(view.m>11){view.m=0;view.y++}buildDates()};}
  ms.value=view.m;ys.value=view.y;
  $('#prev').disabled=view.y==w.now.getFullYear()-1&&view.m==0;
  $('#next').disabled=view.y==w.now.getFullYear()+2&&view.m==11;
  const box=$('#dates');box.innerHTML='';
  'Sun Mon Tue Wed Thu Fri Sat'.split(' ').forEach(d=>{const e=document.createElement('div');e.className='wd';e.textContent=d;box.appendChild(e)});
  const first=new Date(view.y,view.m,1),days=new Date(view.y,view.m+1,0).getDate();
  for(let i=0;i<first.getDay();i++)box.appendChild(document.createElement('div'));
  const todayK=key(w.now);
  for(let dn=1;dn<=days;dn++){
    const d=new Date(view.y,view.m,dn),k=key(d),b=document.createElement('button');
    const ok=d>=w.min&&d<=w.max;
    b.className='chip'+(d.getDay()===0||d.getDay()===6?' wk':'')+(ok?'':' off')+(selected.has(k)?' on':'')+(k===todayK?' today':'');
    b.textContent=dn;b.dataset.k=k;
    if(ok){b.title=pretty(k);b.onclick=()=>{selected.has(k)?selected.delete(k):selected.add(k);b.classList.toggle('on');buildDates();render();};}
    else{b.disabled=true;
      if(d<w.min)b.title=k===todayK?'Today - booking must be made at least 1 day ahead':'Past date';
      else{const opens=new Date(d.getFullYear(),d.getMonth(),d.getDate()-30);b.title='Opens for booking on '+opens.toLocaleDateString('en-IN',{day:'numeric',month:'short',year:'numeric'});}}
    box.appendChild(b);
  }
  const f=o=>o.toLocaleDateString('en-IN',{day:'numeric',month:'short'});
  const sel=[...selected].sort((a,b)=>sortKey(a)<sortKey(b)?-1:1);
  $('#calhint').innerHTML=`Bookable now: ${f(w.min)} – ${f(w.max)} (the site opens dates 30 days ahead; hover a grey date to see when it opens).`+
    (sel.length?`<br>Selected: ${sel.map(k=>esc(pretty(k))).join(', ')}`:'');
}
$('#clear').onclick=()=>{selected.clear();buildDates();render();};

function dayStatus(t,k,n){
  if((blocked[t.id]||[]).includes(k))return{s:'closed'};
  const a=avail[t.id+'|'+k];
  if(!a)return{s:'pending'};
  if(a.error)return{s:'error',msg:a.error};
  if(!a.slots.length)return{s:'closed'};
  const best=Math.max(...a.slots.map(x=>x.left));
  return best>=n?{s:'ok',best}:best>0?{s:'few',best}:{s:'full',best:0};
}
function trekStatus(t){
  const n=Math.max(1,+$('#tickets').value||1),mode=$('#mode').value;
  const days=[...selected].sort((a,b)=>sortKey(a)<sortKey(b)?-1:1).map(k=>({k,...dayStatus(t,k,n)}));
  if(!days.length||days.every(d=>d.s==='pending'))return{c:'idle',days};
  const ok=days.filter(d=>d.s==='ok').length,few=days.some(d=>d.s==='few'),pend=days.some(d=>d.s==='pending');
  let c;
  if(mode==='all')c=ok===days.length?'ok':(ok>0||few)?'part':'none';
  else c=ok>0?'ok':few?'part':'none';
  if(pend&&c!=='ok')c='idle';
  return{c,days,ok};
}
const dayLabel=d=>({ok:`${d.best} left`,few:`only ${d.best}`,full:'sold out',closed:'closed',pending:'…',error:'error'})[d.s];

function render(){
  const groups={ok:[],part:[],none:[],idle:[]};
  for(const t of TREKS){
    const st=trekStatus(t);groups[st.c].push([t,st]);
    const m=markers[t.id];if(m){m.setStyle({fillColor:COLORS[st.c],radius:st.c==='ok'?11:8,fillOpacity:st.c==='idle'?.6:.95});if(st.c==='ok')m.bringToFront();}
  }
  const titles={ok:'Available',part:'Partly available',none:'Not available',idle:selected.size?'Not checked yet':'Open treks'};
  let h='';
  for(const g of ['ok','part','none','idle']){
    if(!groups[g].length)continue;
    h+=`<div class="group">${titles[g]} · ${groups[g].length}</div>`;
    for(const [t,st] of groups[g].sort((a,b)=>a[0].name.localeCompare(b[0].name))){
      const days=st.days.filter(d=>d.s!=='pending').map(d=>`<em class="${d.s}" title="${esc(pretty(d.k))}">${esc(pretty(d.k).split(',')[0]+' '+d.k.slice(0,2))}: ${esc(dayLabel(d))}</em>`).join('');
      h+=`<div class="item${openId===t.id?' sel':''}" data-id="${t.id}"><span class="dot" style="background:${COLORS[st.c]}"></span><div><div class="n">${esc(t.name)}</div><div class="m">${esc(t.district)}${t.level?' · '+esc(t.level):''}${t.distance_km?' · '+esc(t.distance_km)+' km':''}</div>${days?`<div class="days">${days}</div>`:''}</div></div>`;
    }
  }
  $('#list').innerHTML=h||'<div class="group">No treks loaded</div>';
  document.querySelectorAll('.item').forEach(el=>el.onclick=()=>showDetail(+el.dataset.id,true));
  if(openId)showDetail(openId,false);
}

function showDetail(id,fly){
  const t=TREKS.find(x=>x.id===id);if(!t)return;openId=id;
  document.querySelectorAll('.item').forEach(el=>el.classList.toggle('sel',+el.dataset.id===id));
  if(fly&&t.lat)map.flyTo([t.lat,t.lon],Math.max(map.getZoom(),11),{duration:.6});
  const ll=t.lat?`${t.lat},${t.lon}`:'';
  const st=trekStatus(t),n=Math.max(1,+$('#tickets').value||1);
  let rows='';
  for(const d of st.days){
    const a=avail[t.id+'|'+d.k];
    let cell;
    if(d.s==='closed')cell='Closed / blocked';
    else if(d.s==='pending')cell='Not checked yet';
    else if(d.s==='error')cell='Could not check: '+esc(d.msg);
    else cell=a.slots.map(s=>`${esc(s.time||'Slot')}: <b>${s.left}</b> / ${s.capacity} left`).join('<br>');
    rows+=`<tr><td>${esc(pretty(d.k))}</td><td>${cell}</td></tr>`;
  }
  const mpb=Object.keys(avail).filter(k=>k.startsWith(t.id+'|')).map(k=>avail[k].max_per_booking).find(x=>x);
  $('#detail').innerHTML=`<div class="top"><button class="x" title="Close">×</button><h2>${esc(t.name)}</h2><div class="kn">${esc(t.name_kn)} · ${esc(t.district)}</div></div>
   <div class="facts"><div><b>Start point</b>${esc(t.start_point||'—')}</div><div><b>End point</b>${esc(t.end_point||'—')}</div>
   <div><b>Distance</b>${t.distance_km?esc(t.distance_km)+' km':'—'}</div><div><b>Duration</b>${t.duration?esc(t.duration)+' h':'—'}</div>
   ${t.level?`<div><b>Difficulty</b>${esc(t.level)}</div>`:''}${mpb?`<div><b>Max per booking</b>${mpb} visitors</div>`:''}
   ${t.guide?`<div><b>Guide</b>${esc(t.guide)} ${esc(t.guide_phone)}</div>`:''}</div>
   ${ll?`<iframe loading="lazy" referrerpolicy="no-referrer-when-downgrade" src="https://maps.google.com/maps?q=${ll}&z=13&output=embed"></iframe>
   <div class="btns"><a class="pri" target="_blank" href="https://www.google.com/maps/dir/?api=1&destination=${ll}">Directions in Google Maps</a><a target="_blank" href="https://www.google.com/maps/search/?api=1&query=${ll}">Open in Google Maps</a></div>`:''}
   ${rows?`<table><tr><th>Date</th><th>Slots (seats left)</th></tr>${rows}</table>`:'<div class="note">Select dates and press Check to see seats.</div>'}
   ${mpb&&n>mpb?`<div class="note">You need ${n} tickets but this trek allows ${mpb} per booking, so you'd need more than one booking.</div>`:''}
   <div class="note">To book, go to <a target="_blank" href="https://aranyavihaara.karnataka.gov.in/">aranyavihaara.karnataka.gov.in</a> and pick ${esc(t.district_kn||t.district)} → ${esc(t.name_kn||t.name)}.</div>`;
  $('#detail').style.display='block';
  $('#detail .x').onclick=()=>{$('#detail').style.display='none';openId=null;render();};
}

async function api(path){const r=await fetch(path);const j=await r.json();if(!r.ok)throw new Error(j.error||r.status);return j;}

async function pool(tasks,n,tick){let i=0;const w=async()=>{while(i<tasks.length){const t=tasks[i++];try{await t()}catch(e){console.warn(e)}tick()}};await Promise.all(Array.from({length:n},w));}

function setProgress(done,total,msg){
  $('#bar').style.display=total?'block':'none';$('#bar i').style.width=total?(100*done/total)+'%':'0';
  $('#status').textContent=msg;
}

async function check(){
  if(busy)return;
  if(!selected.size){$('#status').textContent='Pick at least one date first.';return;}
  busy=true;$('#go').disabled=true;
  const now=Date.now(),dates=[...selected];
  const t1=TREKS.filter(t=>!blocked[t.id]).map(t=>async()=>{blocked[t.id]=await api(`/api/blocked?district=${t.district_id}&trek=${t.id}`)});
  let done=0;
  await pool(t1,3,()=>setProgress(++done,t1.length,`Reading closed dates… ${done}/${t1.length}`));
  const t2=[];
  for(const t of TREKS)for(const k of dates){
    if((blocked[t.id]||[]).includes(k))continue;
    const a=avail[t.id+'|'+k];if(a&&!a.error&&now-a.t<FRESH_MS)continue;
    t2.push(async()=>{try{avail[t.id+'|'+k]={...await api(`/api/availability?district=${t.district_id}&trek=${t.id}&date=${k}`),t:Date.now()}}catch(e){avail[t.id+'|'+k]={error:e.message,t:Date.now()}}render();});
  }
  done=0;
  await pool(t2,3,()=>setProgress(++done,t2.length,`Checking seats… ${done}/${t2.length}`));
  render();
  const n=TREKS.filter(t=>trekStatus(t).c==='ok').length,errs=Object.values(avail).filter(a=>a.error).length;
  setProgress(0,0,`${n} trek${n===1?'':'s'} available · checked ${new Date().toLocaleTimeString('en-IN',{hour:'2-digit',minute:'2-digit'})}${errs?` · ${errs} checks failed`:''}`);
  busy=false;$('#go').disabled=false;
}
$('#go').onclick=check;
$('#tickets').oninput=render;$('#mode').onchange=render;

async function init(){
  buildDates();
  $('#status').textContent='Loading trek list…';
  try{TREKS=await api('/api/treks');}catch(e){$('#status').textContent='Could not load treks: '+e.message;return;}
  const seen={},pts=[];
  for(const t of TREKS){
    if(t.lat==null)continue;
    const s=t.lat.toFixed(3)+','+t.lon.toFixed(3);const off=(seen[s]=(seen[s]||0)+1)-1;
    const m=L.circleMarker([t.lat,t.lon+off*0.006],{radius:8,color:'#fff',weight:2,fillColor:COLORS.idle,fillOpacity:.6}).addTo(map);
    m.bindTooltip(t.name,{direction:'top',offset:[0,-8]});m.on('click',()=>showDetail(t.id,false));
    markers[t.id]=m;pts.push([t.lat,t.lon]);
  }
  if(pts.length)map.fitBounds(pts,{padding:[40,40]});
  $('#status').textContent=`${TREKS.length} open treks. Pick dates, then check.`;
  render();
}
init();
</script></body></html>
"""


def main():
    ap = argparse.ArgumentParser(description="Aranya Vihaara trek availability map")
    ap.add_argument("--dates", help="comma separated dd-mm-yyyy dates (terminal mode)")
    ap.add_argument("--tickets", type=int, default=1)
    ap.add_argument("--no-browser", action="store_true")
    ap.add_argument("--refresh", action="store_true", help="re-download the trek list")
    a = ap.parse_args()
    if a.refresh:
        load_treks(SITE, force=True)
    if a.dates:
        cli([d.strip() for d in a.dates.split(",") if d.strip()], a.tickets)
    else:
        serve(open_browser=not a.no_browser)


if __name__ == "__main__":
    main()
