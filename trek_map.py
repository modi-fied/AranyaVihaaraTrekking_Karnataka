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
PAGE_FILE = os.path.join(HERE, "docs", "index.html")   # same page that GitHub Pages serves
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
                with open(PAGE_FILE, "rb") as f:
                    return self._send(200, f.read(), "text/html; charset=utf-8")
            if u.path == "/treks.json":
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


def main():
    ap = argparse.ArgumentParser(description="Aranya Vihaara trek availability map")
    ap.add_argument("--dates", help="comma separated dd-mm-yyyy dates (terminal mode)")
    ap.add_argument("--tickets", type=int, default=1)
    ap.add_argument("--no-browser", action="store_true")
    ap.add_argument("--refresh", action="store_true", help="re-download the trek list")
    ap.add_argument("--export", metavar="FILE",
                    help="re-download the trek list, save it to FILE (e.g. docs/treks.json) and exit")
    a = ap.parse_args()
    if a.export:
        treks = load_treks(SITE, force=True)
        if not treks:
            sys.exit("No treks found - not overwriting " + a.export)
        with open(a.export, "w", encoding="utf-8", newline="\n") as f:
            json.dump(treks, f, ensure_ascii=False, indent=1)
            f.write("\n")
        return
    if a.refresh:
        load_treks(SITE, force=True)
    if a.dates:
        cli([d.strip() for d in a.dates.split(",") if d.strip()], a.tickets)
    else:
        serve(open_browser=not a.no_browser)


if __name__ == "__main__":
    main()
