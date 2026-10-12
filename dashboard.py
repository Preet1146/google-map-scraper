"""Local web dashboard for the Google Maps scraper.

    python dashboard.py            # opens http://127.0.0.1:8000

Pick a region (North/South/East/West/Central/North East), then a state: every selected district is searched with every keyword
(IELTS, English, visa, immigration, study abroad), rows appear live, and the
combined result is saved to output/<state>_centres.csv.
"""
import csv
import hashlib
import io
import json
import os
import re
import threading
import time
import urllib.request
import webbrowser
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from playwright.sync_api import sync_playwright

import scraper

CLOUD = "PORT" in os.environ                     # set by Render/Railway/Fly
PORT = int(os.environ.get("PORT", 8000))
TOKEN = os.environ.get("DASH_TOKEN", "")         # if set, every API call must carry it
ALLOWED_ORIGIN = os.environ.get("ALLOWED_ORIGIN", "*")  # your Vercel URL in production
WEB = scraper.ROOT / "web"
ROOT = scraper.ROOT
COLUMNS = ["name", "phone", "website", "email", "address", "city", "district", "state",
           "search_keyword", "google_category", "rating", "reviews", "maps_url"]
DISTRICTS = json.loads((ROOT / "data/districts.json").read_text("utf-8"))
KEYWORDS = json.loads((ROOT / "data/categories.json").read_text("utf-8"))
# region -> its states (data/regions.json); the dashboard goes region -> state -> districts
REGIONS = json.loads((ROOT / "data/regions.json").read_text("utf-8"))
ENABLED_STATES = list(DISTRICTS)  # every state in data/districts.json is offered
# Districts ticked by default (the first batch to run); the rest are one click away.
DEFAULT_DISTRICTS = {
    "Punjab": ["Ludhiana", "Jalandhar", "Amritsar", "Patiala", "Mohali"],
    "Haryana": ["Gurugram", "Faridabad", "Panipat", "Ambala", "Karnal"],
    "Himachal Pradesh": ["Shimla", "Kangra", "Solan", "Mandi", "Una"],
    "Uttarakhand": ["Dehradun", "Haridwar", "Nainital", "Udham Singh Nagar", "Almora"],
    "Delhi": ["New Delhi", "South Delhi", "West Delhi", "North Delhi", "East Delhi"],
    "Chandigarh": ["Chandigarh"],
    "Jammu and Kashmir": ["Jammu", "Srinagar", "Kathua", "Udhampur", "Baramulla"],
    "Ladakh": ["Leh", "Kargil"],
    "Rajasthan": ["Jaipur", "Jodhpur", "Kota", "Udaipur", "Ajmer"],
    "Uttar Pradesh": ["Lucknow", "Kanpur Nagar", "Varanasi", "Agra", "Gautam Buddha Nagar"],
    # South
    "Andhra Pradesh": ["Visakhapatnam", "NTR", "Guntur", "Tirupati", "East Godavari"],
    "Karnataka": ["Bengaluru Urban", "Mysuru", "Dakshina Kannada", "Belagavi", "Dharwad"],
    "Kerala": ["Thiruvananthapuram", "Ernakulam", "Kozhikode", "Thrissur", "Kollam"],
    "Tamil Nadu": ["Chennai", "Coimbatore", "Madurai", "Tiruchirappalli", "Salem"],
    "Telangana": ["Hyderabad", "Rangareddy", "Medchal-Malkajgiri", "Warangal", "Karimnagar"],
    "Puducherry": ["Puducherry"],
    "Lakshadweep": ["Lakshadweep"],
    "Andaman and Nicobar Islands": ["South Andaman"],
    # East
    "Bihar": ["Patna", "Gaya", "Muzaffarpur", "Bhagalpur", "Darbhanga"],
    "Jharkhand": ["Ranchi", "East Singhbhum", "Dhanbad", "Bokaro", "Hazaribagh"],
    "Odisha": ["Khordha", "Cuttack", "Ganjam", "Sambalpur", "Puri"],
    "West Bengal": ["Kolkata", "Howrah", "North 24 Parganas", "South 24 Parganas", "Darjeeling"],
    # West
    "Gujarat": ["Ahmedabad", "Surat", "Vadodara", "Rajkot", "Gandhinagar"],
    "Maharashtra": ["Mumbai City", "Mumbai Suburban", "Pune", "Thane", "Nagpur"],
    "Goa": ["North Goa", "South Goa"],
    "Dadra and Nagar Haveli and Daman and Diu": ["Dadra and Nagar Haveli", "Daman", "Diu"],
    # Central
    "Madhya Pradesh": ["Indore", "Bhopal", "Gwalior", "Jabalpur", "Ujjain"],
    "Chhattisgarh": ["Raipur", "Durg", "Bilaspur", "Raigarh", "Korba"],
    # North East
    "Arunachal Pradesh": ["Itanagar Capital Complex", "Lohit", "Changlang"],
    "Assam": ["Kamrup Metropolitan", "Dibrugarh", "Cachar", "Nagaon", "Jorhat"],
    "Manipur": ["Imphal West", "Imphal East", "Thoubal"],
    "Meghalaya": ["East Khasi Hills", "West Garo Hills"],
    "Mizoram": ["Aizawl", "Lunglei"],
    "Nagaland": ["Kohima", "Dimapur"],
    "Tripura": ["West Tripura", "North Tripura"],
    "Sikkim": ["Gangtok"],
}

job = {"running": False, "logs": [], "rows": [], "updates": [], "dupes": 0,
       "file": "", "progress": [0, 0], "complete": False, "cached": False}
stop_flag = threading.Event()
lock = threading.Lock()

# Finished scrapes are kept in output/<state>/*.csv and listed in this index, so asking for
# the same state + districts + keywords + max again serves the saved file instead of scraping.
CACHE_INDEX = scraper.OUT / "saved_index.json"


def slug(text):
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


def cache_key(state, districts, keywords, max_results):
    raw = json.dumps([state, sorted(districts), sorted(keywords), max_results])
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:10]


def saved_path(state, districts, key):
    if len(districts) == len(DISTRICTS[state]):
        label = "all-districts"
    elif len(districts) <= 3:
        label = "-".join(slug(d) for d in sorted(districts))
    else:
        label = f"{len(districts)}-districts"
    return scraper.OUT / slug(state) / f"{slug(state)}_{label}_{key}.csv"


def load_index():
    try:
        return json.loads(CACHE_INDEX.read_text("utf-8"))
    except (OSError, ValueError):
        return {}


def register_saved(key, path, state, districts, keywords, max_results, emails, n_rows):
    scraper.OUT.mkdir(exist_ok=True)
    index = load_index()
    index[key] = {"file": str(path.relative_to(ROOT)), "state": state, "districts": districts,
                  "keywords": keywords, "max": max_results, "emails": emails, "rows": n_rows,
                  "saved": time.strftime("%Y-%m-%d %H:%M")}
    CACHE_INDEX.write_text(json.dumps(index, indent=1, ensure_ascii=False), "utf-8")


def find_saved(key, want_email):
    """Return (index entry, rows) for a finished run of this configuration, else None."""
    entry = load_index().get(key)
    if not entry or (want_email and not entry.get("emails")):
        return None
    path = ROOT / entry["file"]
    try:
        with open(path, newline="", encoding="utf-8-sig") as f:
            rows = list(csv.DictReader(f))
    except OSError:
        return None
    return (entry, rows) if rows else None


def drop_saved(key):
    """Forget a saved run before its file gets rewritten, so a half-done rewrite is never served."""
    index = load_index()
    if index.pop(key, None) is not None:
        CACHE_INDEX.write_text(json.dumps(index, indent=1, ensure_ascii=False), "utf-8")


def log(msg):
    with lock:
        job["logs"].append(f"[{time.strftime('%H:%M:%S')}] {msg}")


def fetch_emails(site):
    """Homepage + contact pages over plain HTTP (no browser, safe to run in threads)."""
    if not site:
        return ""
    base = "{0.scheme}://{0.netloc}".format(urlparse(site))
    found = []
    for url in (site, base + "/contact", base + "/contact-us"):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            body = urllib.request.urlopen(req, timeout=8).read(600_000).decode("utf-8", "ignore")
        except Exception:
            continue
        for e in scraper.EMAIL_RE.findall(body):
            e = e.lower().strip(".")
            if not scraper.JUNK_EMAIL.search(e) and e not in found:
                found.append(e)
        if found:
            break
    return "; ".join(found[:3])


def scrape_search(page, query, district, state, keyword, max_results, want_email, seen, pool):
    """One Maps search: scroll the feed and publish new cards as rows while scrolling."""
    page.goto(f"https://www.google.com/maps/search/{scraper.quote(query)}?hl=en&gl=in",
              wait_until="domcontentloaded", timeout=45000)
    scraper.dismiss_consent(page)
    try:
        page.wait_for_selector('div[role="feed"]', timeout=15000)
    except Exception:
        log("   no result list (no results for this search)")
        return
    feed = page.query_selector('div[role="feed"]')
    processed = [0]
    got = [0]

    def email_task(row, idx):
        if stop_flag.is_set():
            return
        email = fetch_emails(row["website"])
        if email:
            with lock:
                row["email"] = email
                job["updates"].append([idx, email])

    def add_new_cards():
        cards = page.evaluate(scraper.CARDS_JS)
        new_cards, processed[0] = cards[processed[0]:], len(cards)  # feed is append-only
        for c in new_cards:
            if got[0] >= max_results:
                return
            rec = scraper.parse_card(c)
            if not rec["name"]:
                continue
            got[0] += 1
            key = scraper.place_key(c["href"], rec)
            if key in seen:
                with lock:
                    job["dupes"] += 1
                continue
            seen.add(key)
            row = {"name": rec["name"], "phone": rec["phone"], "website": rec["website"],
                   "email": "", "address": rec["address"], "city": district,
                   "district": district, "state": state, "search_keyword": keyword,
                   "google_category": rec["category"], "rating": rec["rating"],
                   "reviews": rec["reviews"], "maps_url": c["href"]}
            with lock:
                job["rows"].append(row)
                idx = len(job["rows"]) - 1
            if want_email and row["website"]:
                pool.submit(email_task, row, idx)

    stale = last = 0
    while not stop_flag.is_set():
        n = page.locator("div[role='feed'] div.Nv2PK").count()
        add_new_cards()
        if got[0] >= max_results or page.get_by_text("You've reached the end of the list").count():
            break
        stale = stale + 1 if n == last else 0
        if stale >= 5:
            break
        last = n
        feed.evaluate("e => e.scrollBy(0, e.scrollHeight)")
        page.wait_for_timeout(1100)
    add_new_cards()
    log(f"   {got[0]} cards read, {len(job['rows'])} unique so far")


def run_job(state, districts, keywords, max_results, want_email, headless, key):
    """Scrape every (district, keyword) search of a state in one browser session."""
    searches = [(d, k) for d in districts for k in keywords]
    job["progress"] = [0, len(searches)]
    seen, pool = set(), ThreadPoolExecutor(8)
    path, failed, finished = saved_path(state, districts, key), 0, False
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=headless or CLOUD,
                                         args=["--no-sandbox"] if CLOUD else [])
            ctx = browser.new_context(locale="en-IN", viewport={"width": 1366, "height": 900},
                                      timezone_id="Asia/Kolkata")
            page = ctx.new_page()
            log(f"{state}: {len(districts)} districts x {len(keywords)} keywords "
                f"= {len(searches)} searches")
            for i, (district, keyword) in enumerate(searches, 1):
                if stop_flag.is_set():
                    break
                job["progress"] = [i - 1, len(searches)]
                query = f"{keyword} in {district}, {state}"
                log(f"[{i}/{len(searches)}] {query}")
                try:
                    scrape_search(page, query, district, state, keyword, max_results,
                                  want_email, seen, pool)
                except Exception as e:  # one bad search must not kill the whole run
                    failed += 1
                    log(f"   search failed ({e.__class__.__name__}), continuing")
                save_csv(path)
            browser.close()
        job["progress"] = [len(searches), len(searches)]
        if want_email:
            log("Finishing email lookups...")
        pool.shutdown(wait=True)
        with lock:
            rows = list(job["rows"])
        log(f"Done: {len(rows)} records, {sum(1 for r in rows if r['email'])} with email.")
        finished = not stop_flag.is_set()
    except Exception as e:  # keep the dashboard alive whatever Google does
        log(f"ERROR: {e.__class__.__name__}: {e}")
    finally:
        pool.shutdown(wait=True)
        try:
            saved = save_csv(path)
            if saved:
                log(f"Saved {saved}")
                if finished and not failed:  # only a full, clean run may be served from disk later
                    register_saved(key, saved, state, districts, keywords, max_results,
                                   want_email, len(job["rows"]))
                    job["complete"] = True
                else:
                    log("Partial result (stopped or some searches failed) - it will not be "
                        "reused; running this selection again scrapes it fresh.")
            elif job["rows"]:
                job["complete"] = finished and not failed  # still downloadable from the page
        except Exception as e:  # whatever happens, the dashboard must not stay stuck on "running"
            log(f"ERROR while saving: {e.__class__.__name__}: {e}")
        job["running"] = False


def save_csv(path):
    with lock:
        rows = list(job["rows"])
    if not rows:
        return None
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        write_csv(job.get("redirect") or path, rows)
        path = job.get("redirect") or path
    except OSError as e:  # typically the CSV is open in Excel; never let that kill the scrape
        alt = job.get("redirect") or path.with_name(f"{path.stem}_{time.strftime('%H%M%S')}.csv")
        try:
            write_csv(alt, rows)
        except OSError:
            if not job.get("save_warned"):
                job["save_warned"] = True
                log(f"WARNING: cannot save {path.name} ({e.__class__.__name__}); close it if it is "
                    f"open in Excel. Scraping continues, the rows are kept in memory.")
            return None
        if not job.get("redirect"):  # one fallback file for the rest of this run, one warning
            job["redirect"] = alt
            log(f"WARNING: {path.name} is locked (open in Excel?) - saving to {alt.name} instead.")
        path = alt
    job["file"] = str(path)
    return path


def write_csv(dest, rows):
    with open(dest, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, COLUMNS, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)


def make_download(fmt):
    with lock:
        rows = list(job["rows"])
    if fmt == "json":
        return "application/json", json.dumps(rows, ensure_ascii=False, indent=2).encode("utf-8"), "json"
    if fmt == "xlsx":
        from openpyxl import Workbook
        wb = Workbook()
        ws = wb.active
        ws.append(COLUMNS)
        for r in rows:
            ws.append([r.get(c, "") for c in COLUMNS])
        buf = io.BytesIO()
        wb.save(buf)
        return ("application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                buf.getvalue(), "xlsx")
    buf = io.StringIO()
    w = csv.DictWriter(buf, COLUMNS, extrasaction="ignore")
    w.writeheader()
    w.writerows(rows)
    return "text/csv", ("﻿" + buf.getvalue()).encode("utf-8"), "csv"


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def authorized(self, query):
        return not TOKEN or query.get("token", [""])[0] == TOKEN

    def do_OPTIONS(self):
        self.send(204, "text/plain", b"")

    def send(self, code, ctype, body, extra=None):
        self.send_response(code)
        self.send_header("Access-Control-Allow-Origin", ALLOWED_ORIGIN)
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        u = urlparse(self.path)
        q = parse_qs(u.query)
        if u.path == "/":
            self.send(200, "text/html; charset=utf-8", (WEB / "index.html").read_bytes())
        elif u.path == "/config.js":
            self.send(200, "text/javascript", (WEB / "config.js").read_bytes())
        elif u.path == "/health":
            self.send(200, "text/plain", b"ok")
        elif u.path.startswith(("/api/", "/download")) and not self.authorized(q):
            self.send(401, "application/json", b'{"error":"bad token"}')
        elif u.path == "/api/config":
            body = {"regions": [{"name": r, "states": [
                        {"name": st, "districts": DISTRICTS[st],
                         "enabled": st in ENABLED_STATES,
                         "defaults": DEFAULT_DISTRICTS.get(st, [])} for st in sts if st in DISTRICTS]}
                        for r, sts in REGIONS.items()],
                    "keywords": KEYWORDS}
            self.send(200, "application/json", json.dumps(body).encode("utf-8"))
        elif u.path == "/api/state":
            log_from, row_from = int(q.get("log", ["0"])[0]), int(q.get("row", ["0"])[0])
            upd_from = int(q.get("upd", ["0"])[0])
            with lock:
                body = {"running": job["running"], "dupes": job["dupes"],
                        "progress": job["progress"], "complete": job["complete"],
                        "cached": job["cached"],
                        "updates": job["updates"][upd_from:], "upd_n": len(job["updates"]),
                        "logs": job["logs"][log_from:], "rows": job["rows"][row_from:],
                        "log_n": len(job["logs"]), "row_n": len(job["rows"])}
            self.send(200, "application/json", json.dumps(body).encode("utf-8"))
        elif u.path == "/download":
            ctype, data, ext = make_download(q.get("fmt", ["csv"])[0])
            self.send(200, ctype, data,
                      {"Content-Disposition": f'attachment; filename="centres.{ext}"'})
        else:
            self.send(404, "text/plain", b"not found")

    def do_POST(self):
        u = urlparse(self.path)
        length = int(self.headers.get("Content-Length", 0))
        data = json.loads(self.rfile.read(length) or b"{}")
        if not self.authorized(parse_qs(u.query)):
            return self.send(401, "application/json", b'{"error":"bad token"}')
        if u.path == "/api/start":
            state = str(data.get("state", ""))
            districts = [d for d in data.get("districts", []) if d in DISTRICTS.get(state, [])]
            keywords = [k for k in data.get("keywords", []) if k in KEYWORDS]
            if state not in ENABLED_STATES or not districts or not keywords:
                return self.send(400, "application/json",
                                 b'{"error":"pick a state, districts and keywords"}')
            max_results, want_email = int(data.get("max", 100000)), bool(data.get("emails"))
            key = cache_key(state, districts, keywords, max_results)
            saved = None if data.get("fresh") else find_saved(key, want_email)
            with lock:
                if job["running"]:
                    return self.send(409, "application/json", b'{"error":"already running"}')
                job.update(running=True, logs=[], rows=[], updates=[], dupes=0,
                           file="", progress=[0, 0], complete=False, cached=False,
                           save_warned=False, redirect=None)
                if saved:  # same selection scraped before: hand back the saved CSV, no browser
                    entry, rows = saved
                    job.update(rows=rows, file=entry["file"], complete=True, cached=True,
                               progress=[1, 1], running=False)
                    job["logs"].append(
                        f"[{time.strftime('%H:%M:%S')}] Already scraped on {entry['saved']} - "
                        f"loaded {len(rows)} saved records from {entry['file']} (no scraping). "
                        f"Tick 'Fresh scrape' to scrape again.")
            if saved:
                return self.send(200, "application/json", b'{"cached":true}')
            drop_saved(key)
            stop_flag.clear()
            threading.Thread(target=run_job, daemon=True, args=(
                state, districts, keywords, max_results, want_email,
                not data.get("show_browser"), key)).start()
            self.send(200, "application/json", b"{}")
        elif u.path == "/api/stop":
            stop_flag.set()
            log("Stop requested...")
            self.send(200, "application/json", b"{}")
        else:
            self.send(404, "text/plain", b"not found")


if __name__ == "__main__":
    srv = ThreadingHTTPServer(("0.0.0.0" if CLOUD else "127.0.0.1", PORT), Handler)
    print(f"Dashboard running at http://127.0.0.1:{PORT}  (Ctrl+C to quit)")
    if not CLOUD:
        threading.Timer(0.8, lambda: webbrowser.open(f"http://127.0.0.1:{PORT}")).start()
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
