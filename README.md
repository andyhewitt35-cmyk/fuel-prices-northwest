# North West fuel prices

Open `fuel-prices-northwest.html` in any browser (Mac or phone). Everything is built in; the map needs internet, the table works offline.

To refresh the prices:  `python3 fetch_prices.py`  (needs only Python 3, no installs; `template.html` must sit next to it).
It rebuilds `fuel-prices-northwest.html` and `fuel-data-northwest.json`.

Sources: the government Fuel Finder CSV (all UK forecourts; it only answers UK connections, so a public hourly mirror is used as backup),
plus the old CMA retailer feeds that are still online (scheme closed 1 May 2026). Test: `node test.js` (playwright-core + Chrome).

## Publishing (GitHub Pages, free HTTPS, refreshes hourly)
1. `gh auth login --hostname github.com --git-protocol https --web --scopes workflow` (one-time, browser device code)
2. `./publish.sh` → creates public repo `fuel-prices-northwest`, enables Pages (Actions), pushes, waits for the first deploy.
URL: `https://<github-user>.github.io/fuel-prices-northwest/`. `.github/workflows/publish.yml` re-fetches prices hourly 05:20–21:20 UTC.
