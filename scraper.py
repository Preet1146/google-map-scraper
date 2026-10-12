"""Google Maps scraper for IELTS / English coaching, visa, immigration and
study-abroad centres across Indian districts.

    python scraper.py scrape --states "Punjab,Haryana" --emails
    python scraper.py export
    python scraper.py status

Results go to SQLite (output/places.db); progress is saved per
(state, district, category) search so an interrupted run resumes where it stopped.
"""
import argparse
import csv
import json
import random
import re
import sqlite3
import sys
import time
from pathlib import Path
from urllib.parse import quote, urlparse

from playwright.sync_api import TimeoutError as PWTimeout
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).parent
OUT = ROOT / "output"
DB_PATH = OUT / "places.db"
FIELDS = ["name", "address", "city", "district", "state", "pincode", "phone",
          "email", "website", "google_category", "rating", "reviews",
          "search_category", "maps_url"]

# Words that must appear in the address for a result to count as inside the state.
STATE_ALIASES = {
    "delhi": ["delhi"],
    "jammu and kashmir": ["jammu", "kashmir", "j&k"],
    "ladakh": ["ladakh", "leh", "kargil"],
    "uttarakhand": ["uttarakhand", "uttaranchal"],
}
FEATURE_RE = re.compile(r"!1s(0x[0-9a-f]+:0x[0-9a-f]+)")
EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}")
JUNK_EMAIL = re.compile(r"(\.(png|jpe?g|gif|webp|svg|css|js)$|sentry|wixpress|example\.|"
                        r"domain\.com|yourname|your@|@2x|u003e)", re.I)

CARDS_JS = """() => [...document.querySelectorAll('div[role="feed"] div.Nv2PK')].map(c => {
  const link = c.querySelector('a.hfpxzc');
  const web = c.querySelector('a[data-value="Website"]');
  const rat = c.querySelector('span.MW4etd');
  const rev = c.querySelector('span.UY7F9');
  const rows = [...c.querySelectorAll('.W4Efsd .W4Efsd')].map(r => r.innerText);
  return {
    href: link ? link.href : '',
    name: link ? (link.getAttribute('aria-label') || '') : '',
    website: web ? web.href : '',
    rating: rat ? rat.innerText.trim() : '',
    reviews: rev ? rev.innerText.replace(/[^0-9,]/g, '') : '',
    rows: rows,
    text: c.innerText
  };
})"""


# ---------------------------------------------------------------- storage
def db_connect():
    OUT.mkdir(exist_ok=True)
    con = sqlite3.connect(DB_PATH)
    con.execute("CREATE TABLE IF NOT EXISTS places (pkey TEXT PRIMARY KEY, " +
                ", ".join(f"{f} TEXT" for f in FIELDS) + ")")
    con.execute("CREATE TABLE IF NOT EXISTS done (q TEXT PRIMARY KEY, found INTEGER, ts TEXT)")
    return con


def place_key(url, rec):
    m = FEATURE_RE.search(url)
    if m:
        return m.group(1)
    return re.sub(r"\W+", "", (rec["name"] + rec["phone"] + rec["address"]).lower())


# ---------------------------------------------------------------- parsing
def in_state(address, state):
    a = address.lower()
    return any(k in a for k in STATE_ALIASES.get(state.lower(), [state.lower()]))


def parse_address(address, district, state):
    """Return (city, pincode). City = last address chunk before state/pincode."""
    pin = re.search(r"\b\d{6}\b", address)
    pin = pin.group(0) if pin else ""
    parts = [p.strip() for p in address.split(",") if p.strip()]
    skip = STATE_ALIASES.get(state.lower(), [state.lower()]) + ["india"]
    while parts:
        last = parts[-1].lower()
        if re.fullmatch(r"\d{6}", last) or any(last.startswith(s) for s in skip) \
                or re.fullmatch(r"[a-z .&]+\s*\d{6}", last):
            parts.pop()
        else:
            break
    city = re.sub(r"\b\d{6}\b", "", parts[-1]).strip(" -") if parts else ""
    if state == district:  # UT such as Chandigarh: remaining chunk is a sector, not a city
        city = district
    return city or district, pin


def find_emails(ctx, site):
    """Fetch homepage + common contact pages and pull out email addresses."""
    if not site:
        return ""
    base = "{0.scheme}://{0.netloc}".format(urlparse(site))
    found = []
    for url in (site, base + "/contact", base + "/contact-us", base + "/contact-us/"):
        try:
            r = ctx.request.get(url, timeout=12000, fail_on_status_code=False)
            if r.status != 200:
                continue
            body = r.text()
        except Exception:
            continue
        for e in EMAIL_RE.findall(body):
            e = e.lower().strip(".")
            if not JUNK_EMAIL.search(e) and e not in found:
                found.append(e)
        if found:
            break
    return "; ".join(found[:3])


# ---------------------------------------------------------------- scraping
def dismiss_consent(page):
    for label in ("Accept all", "I agree", "Reject all"):
        try:
            page.get_by_role("button", name=label).first.click(timeout=1500)
            return
        except Exception:
            pass


PHONE_RE = re.compile(r"(?:\+91[\s-]?)?(?:0\d{2,4}[\s-]?\d{6,8}|0?\d{5}[\s-]?\d{5})")
ICON_RE = re.compile(r"[-]")


def parse_card(c):
    """Turn one raw result card into a record using only what the card shows."""
    phone = PHONE_RE.search(c["text"])
    category, address = "", ""
    for row in c["rows"]:
        segs = [x.strip() for x in re.split(r"\s[·⋅]\s", row)]
        segs = [x for x in segs if x and not ICON_RE.fullmatch(x)]
        segs = [ICON_RE.sub("", x).strip() for x in segs]
        if not segs or PHONE_RE.search(row) or re.match(r"(Open|Closed|Closes|Opens)", segs[0]):
            continue
        if not category:
            category = segs[0]
            address = ", ".join(segs[1:])
            break
    return {"name": c["name"], "address": address, "phone": phone.group(0) if phone else "",
            "website": c["website"], "category": category, "rating": c["rating"],
            "reviews": c["reviews"]}


def collect_cards(page, query, max_results):
    """Open a Maps search, scroll the result feed to the end, and read the cards."""
    page.goto(f"https://www.google.com/maps/search/{quote(query)}?hl=en&gl=in",
              wait_until="domcontentloaded", timeout=45000)
    dismiss_consent(page)
    try:
        page.wait_for_selector('div[role="feed"]', timeout=15000)
    except PWTimeout:
        return []  # no results, or Google jumped straight to a single place
    feed = page.query_selector('div[role="feed"]')
    stale = last = 0
    while True:
        n = page.locator("div[role='feed'] div.Nv2PK").count()
        if n >= max_results or page.get_by_text("You've reached the end of the list").count():
            break
        stale = stale + 1 if n == last else 0
        if stale >= 5:
            break
        last = n
        feed.evaluate("e => e.scrollBy(0, e.scrollHeight)")
        page.wait_for_timeout(random.randint(900, 1500))
    return page.evaluate(CARDS_JS)[:max_results]


def run_scrape(args):
    all_states = json.loads((ROOT / "data/districts.json").read_text("utf-8"))
    cats = json.loads((ROOT / "data/categories.json").read_text("utf-8"))
    if args.categories:
        cats = [c.strip() for c in args.categories.split(",")]
    states = [s.strip() for s in args.states.split(",")] if args.states else list(all_states)
    lookup = {k.lower(): k for k in all_states}
    con = db_connect()

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=args.headless,
                                     args=["--disable-blink-features=AutomationControlled"])
        ctx = browser.new_context(locale="en-IN", viewport={"width": 1366, "height": 900},
                                  timezone_id="Asia/Kolkata")
        page = ctx.new_page()
        for st in states:
            state = lookup.get(st.lower())
            if not state:
                print(f"! unknown state '{st}' - see data/districts.json")
                continue
            districts = all_states[state]
            if args.districts:
                want = {d.strip().lower() for d in args.districts.split(",")}
                districts = [d for d in districts if d.lower() in want]
            for district in districts:
                for cat in cats:
                    query = f"{cat} in {district}, {state}"
                    if con.execute("SELECT 1 FROM done WHERE q=?", (query,)).fetchone():
                        continue
                    print(f"\n>> {query}")
                    try:
                        cards = collect_cards(page, query, args.max_results)
                    except Exception as e:
                        print(f"   search failed ({e.__class__.__name__}); will retry next run")
                        continue
                    print(f"   {len(cards)} cards read")
                    new = 0
                    for card in cards:
                        href = card["href"]
                        rec = parse_card(card)
                        if not rec["name"]:
                            continue
                        key = place_key(href, rec)
                        if con.execute("SELECT 1 FROM places WHERE pkey=?", (key,)).fetchone():
                            continue
                        # cards show only a street-level address, so city = the searched district
                        city = district
                        pin = (re.search(r"\b\d{6}\b", rec["address"]) or [""])[0]
                        row = {
                            "name": rec["name"], "address": rec["address"], "city": city,
                            "district": district, "state": state, "pincode": pin,
                            "phone": rec["phone"], "email": "", "website": rec["website"],
                            "google_category": rec["category"], "rating": rec["rating"],
                            "reviews": rec["reviews"], "search_category": cat,
                            "maps_url": href,
                        }
                        if args.emails and row["website"]:
                            row["email"] = find_emails(ctx, row["website"])
                        cur = con.execute(
                            f"INSERT OR IGNORE INTO places VALUES (?,{','.join('?' * len(FIELDS))})",
                            [key] + [row[f] for f in FIELDS])
                        new += cur.rowcount
                        print(f"   + {row['name'][:45]:45} | {row['phone']:16} | {row['website'][:35]}")
                    con.execute("INSERT OR REPLACE INTO done VALUES (?,?,datetime('now'))",
                                (query, new))
                    con.commit()
                    print(f"   saved {new} new")
        browser.close()


# ---------------------------------------------------------------- export
def run_export(args):
    con = db_connect()
    rows = con.execute(f"SELECT {','.join(FIELDS)} FROM places "
                       "ORDER BY state, district, name").fetchall()
    csv_path = OUT / "centres.csv"
    with open(csv_path, "w", newline="", encoding="utf-8-sig") as f:  # BOM so Excel reads it
        w = csv.writer(f)
        w.writerow(FIELDS)
        w.writerows(rows)
    print(f"{len(rows)} rows -> {csv_path}")
    try:
        from openpyxl import Workbook
        wb = Workbook()
        ws = wb.active
        ws.title = "Centres"
        ws.append(FIELDS)
        for r in rows:
            ws.append(list(r))
        ws.freeze_panes = "A2"
        wb.save(OUT / "centres.xlsx")
        print(f"-> {OUT / 'centres.xlsx'}")
    except ImportError:
        print("openpyxl not installed; skipped xlsx")


def run_status(args):
    con = db_connect()
    print("searches done:", con.execute("SELECT COUNT(*) FROM done").fetchone()[0])
    for st, n in con.execute("SELECT state, COUNT(*) FROM places GROUP BY state"):
        print(f"  {st:20} {n}")


def main():
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # names contain non-cp1252 chars
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("scrape")
    s.add_argument("--states", help='comma list, e.g. "Punjab,Haryana" (default: all in districts.json)')
    s.add_argument("--districts", help="limit to these districts (comma list)")
    s.add_argument("--categories", help="override data/categories.json (comma list)")
    s.add_argument("--max-results", type=int, default=120, help="max listings per search")
    s.add_argument("--emails", action="store_true", help="look for emails on each website")
    s.add_argument("--headless", action="store_true", help="hide the browser window")
    s.set_defaults(fn=run_scrape)
    sub.add_parser("export").set_defaults(fn=run_export)
    sub.add_parser("status").set_defaults(fn=run_status)
    args = ap.parse_args()
    try:
        args.fn(args)
    except KeyboardInterrupt:
        print("\nstopped - progress saved, rerun the same command to resume")


if __name__ == "__main__":
    main()
