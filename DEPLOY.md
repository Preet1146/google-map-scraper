# Deploy: Vercel (dashboard page) + Render (scraper backend)

Vercel only serves the static page in `web/`. The scraper (Python + Chromium) runs on Render.

## 1. Backend on Render
1. Push this folder to a GitHub repo.
2. Render → New → Blueprint → pick the repo (uses `render.yaml`, Docker, Singapore region).
3. Set env vars on the service:
   - `DASH_TOKEN` = a long random secret (the dashboard asks for it once)
   - `ALLOWED_ORIGIN` = your Vercel URL, e.g. `https://my-scraper.vercel.app` (set after step 2)
4. Note the service URL, e.g. `https://maps-scraper.onrender.com`. Check `/health` returns `ok`.

## 2. Frontend on Vercel
1. Edit `web/config.js`: `window.API_BASE = "https://maps-scraper.onrender.com";`
2. Vercel → Add New Project → same repo. Framework "Other"; `vercel.json` already sets the output folder to `web`.
3. Deploy, open the URL, enter the `DASH_TOKEN` when prompted.

## Notes
- Render's free plan (512 MB) is too small for Chromium; use Starter or bigger.
- Job state and CSV files live in memory / on the container disk; download results when a run ends. A restart loses them.
- One scrape runs at a time per server.
- Google may block or captcha data-centre IPs. If searches start returning no results, run it locally (`start_dashboard.bat`) instead.
- Local use is unchanged: `python dashboard.py` or `start_dashboard.bat`.
