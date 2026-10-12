INDIA COACHING-CENTRE SCRAPER  -  quick start (Windows)
=========================================================

What it does
  Finds IELTS / English-course / visa / immigration / study-abroad centres on
  Google Maps for any Indian region -> state -> district, with phone, website,
  email and address. Results download as CSV automatically.

First time only
  1. Install Python 3.10 or newer from https://www.python.org/downloads/
     (tick "Add python.exe to PATH" in the installer).
  2. Unzip this folder anywhere (for example your Desktop).
  3. Double-click  start_dashboard.bat
     The first start installs the needed packages and a browser (a few
     minutes, needs internet). Later starts open straight away.

Using it
  1. Your browser opens http://127.0.0.1:8000  (if not, open that address).
  2. Pick a REGION, then a STATE, then tick the DISTRICTS you want.
  3. Press "Run Scraper". Rows appear live. When it finishes the CSV is
     downloaded automatically and also saved in the  output  folder.
  4. Run the same selection again later: it loads the saved file instead of
     scraping again. Switch on "Fresh scrape" to scrape again.

Tips
  - Start small (one state, a few districts). A whole big state takes hours.
  - Keep the black console window open while it runs. Close it to quit.
  - Do not keep the CSV open in Excel while a run is in progress.
  - If Google starts showing no results, stop and try again later.
