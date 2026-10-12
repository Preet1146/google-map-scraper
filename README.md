# Google Map Scraper — India Coaching & Consultancy Centres

Scrapes Google Maps for **IELTS / English coaching, visa, immigration and study-abroad centres** across Indian regions, states and districts. For each business it collects the name, phone, website, email (optional), address, rating and Maps link, and exports the results to CSV / Excel.

It comes with two interfaces:

- **Web dashboard** (`dashboard.py`) — pick a region → state → districts, press *Run Scraper*, watch rows appear live, and get a CSV automatically.
- **Command-line scraper** (`scraper.py`) — scriptable, resumable, stores everything in SQLite.

Built with [Playwright](https://playwright.dev/python/) (Chromium) and the Python standard library.

## Features

- Covers all Indian states and union territories, grouped into North / South / East / West / Central / North East regions (`data/regions.json`, `data/districts.json`).
- Searches every selected district with every keyword in `data/categories.json`:
  `IELTS coaching institute`, `English speaking course institute`, `visa consultants`, `immigration consultants`, `study abroad consultants`.
- Optional email extraction: visits each business's website and its `/contact` pages.
- De-duplicates places by Google's feature id.
- **Resumable** — the CLI records each finished `(state, district, category)` search, so an interrupted run continues where it stopped.
- **Result caching** in the dashboard — re-running the same selection serves the saved CSV; tick *Fresh scrape* to scrape again.
- Deployable: dashboard on Vercel, scraper backend on Render (Docker).

## Quick start (Windows)

1. Install [Python 3.10+](https://www.python.org/downloads/) and tick **Add python.exe to PATH**.
2. Double-click **`start_dashboard.bat`**. The first run installs the dependencies and Chromium (a few minutes); later runs start immediately.
3. Your browser opens <http://127.0.0.1:8000>. Choose a region, a state, tick districts, and press **Run Scraper**.
4. When it finishes, the CSV downloads automatically and is also saved under `output/`.

### Manual setup (any OS)

```bash
pip install -r requirements.txt
python -m playwright install chromium
python dashboard.py        # opens http://127.0.0.1:8000
```

## Command-line usage

```bash
# Scrape (a Chromium window opens unless --headless is given)
python scraper.py scrape --states "Punjab,Haryana" --emails

# Limit to specific districts / keywords
python scraper.py scrape --states Punjab --districts "Ludhiana,Jalandhar" \
                         --categories "IELTS coaching institute" --max-results 60 --headless

# Write output/centres.csv and output/centres.xlsx
python scraper.py export

# Show how many places have been collected per state
python scraper.py status
```

| Option | Description |
| --- | --- |
| `--states` | Comma-separated list of states (default: all in `data/districts.json`) |
| `--districts` | Restrict to these districts |
| `--categories` | Override the keywords in `data/categories.json` |
| `--max-results` | Max listings per search (default `120`) |
| `--emails` | Look for email addresses on each business's website |
| `--headless` | Hide the browser window |

Press `Ctrl+C` to stop; progress is saved, and re-running the same command resumes.

## Output

Columns: `name`, `address`, `city`, `district`, `state`, `pincode`, `phone`, `email`, `website`, `google_category`, `rating`, `reviews`, `search_category`, `maps_url`.

| Path | Contents |
| --- | --- |
| `output/places.db` | SQLite database used by the CLI |
| `output/centres.csv`, `output/centres.xlsx` | Output of `scraper.py export` |
| `output/<state>/*.csv` | Dashboard results, one file per run |
| `output/saved_index.json` | Index used by the dashboard to reuse earlier runs |

`output/` is git-ignored.

## Project layout

```
scraper.py            Playwright scraper + CLI (scrape / export / status)
dashboard.py          Local web server and job runner for the dashboard
web/                  Dashboard front end (index.html, config.js)
data/                 regions.json, districts.json, categories.json
start_dashboard.bat   One-click Windows launcher
Dockerfile            Container image for the backend
render.yaml           Render blueprint
vercel.json           Vercel config (serves web/)
DEPLOY.md             Deployment guide
```

## Deployment

The static dashboard (`web/`) can be hosted on Vercel while the scraper runs on Render in Docker. See [DEPLOY.md](DEPLOY.md) for the step-by-step guide.

Environment variables used by `dashboard.py`:

| Variable | Purpose |
| --- | --- |
| `PORT` | Server port (default `8000`; setting it also enables cloud mode) |
| `DASH_TOKEN` | If set, every API call must carry this secret |
| `ALLOWED_ORIGIN` | CORS origin of the front end (e.g. your Vercel URL) |

## Tips and limitations

- Start small — one state and a few districts. A whole large state can take hours.
- Keep the console window open while a run is in progress, and don't keep the output CSV open in Excel.
- Google may show no results, a consent page or a captcha if it detects heavy automated use, especially from data-centre IPs. If that happens, stop and retry later, or run locally.
- The scraper depends on Google Maps' current page structure; CSS selectors in `scraper.py` may need updating if Google changes it.
- Listing cards only show a street-level address, so `city` is set to the searched district.

## Disclaimer

Scraping Google Maps may violate Google's Terms of Service. Use this tool responsibly, at a low request rate, and comply with applicable laws and data-protection rules (including India's DPDP Act) when handling and contacting the collected business data. You are responsible for how you use it.
