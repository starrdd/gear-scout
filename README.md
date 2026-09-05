# Gear Scout

A small, cross-market music-gear deal tracker. Python 3.9 or newer; no packages, database, or subscription to install. It writes a browser dashboard, structured results, and an alert history. Nothing is purchased automatically.

The dashboard includes direct searches for eBay, Facebook Marketplace, OfferUp, Craigslist Los Angeles and Orange County, Reverb, Guitar Center, Music Go Round, Sweetwater Used, Mercari, and ShopGoodwill. It also has a private in-browser calculator for scoring any listing you find. Only eBay listing retrieval is automated; the other sources use their normal saved-search and notification controls because they do not offer a suitable public buyer-search API.

## Try it now

Open Terminal in this folder and run:

```sh
python3 tracker.py --demo --open
```

Or double-click **Try Demo.command** on a Mac. The demo uses invented examples and does not connect to eBay or send alerts. A prebuilt demo is included at `demo/report.html`.

## Enable real eBay checks

1. Register at https://developer.ebay.com/ and obtain your **production** App ID (Client ID) and Cert ID (Client Secret).
2. Confirm your keyset has production Browse API access. eBay documents eligibility and approval requirements at https://developer.ebay.com/api-docs/buy/buy-requirements.html . A developer account alone does not guarantee access. Sandbox keys do not fetch real listings.
3. Set the credentials in Terminal. These prompts avoid putting your secret into command history (macOS zsh):

```sh
read 'EBAY_CLIENT_ID?Production Client ID: '
read -s 'EBAY_CLIENT_SECRET?Production Client Secret: '
export EBAY_CLIENT_ID EBAY_CLIENT_SECRET
```

4. From this folder, start monitoring:

```sh
python3 tracker.py --watch --desktop --open
```

Leave Terminal open and the computer awake. Checks run hourly; Ctrl+C stops them. To prevent sleep while plugged in on macOS, you can run `caffeinate -i python3 tracker.py --watch --desktop --open` instead. Credentials last only in this Terminal session. Never put them in config.json or share them.

For a single check: `python3 tracker.py --open`.

Desktop notifications are optional and macOS-only. Their visibility depends on macOS notification settings. Terminal alerts and `data/alerts.jsonl` work without them. The dashboard reloads every five minutes, but only the running script fetches new data. It does not start automatically after reboot.

**Without API access:** the demo dashboard's watchlist contains real eBay search links, including nearby searches. You can use eBay saved-search notifications while obtaining access; automatic scoring of live listings requires the API credentials. There is no HTML scraping fallback.

## Personalize config.json

Open `config.json` in a text editor. Changes take effect on the next check.

- `zip_code`: **90503**, a Torrance / South Bay starting point. Replace with your ZIP.
- `radius_miles`: **50** for local pickup searches. The shipped search covers US items deliverable to your ZIP.
- `mode`: `both`, `shipped`, or `pickup`. When both modes find the same listing, the report uses the cheaper known option. A pickup total assumes you collect it; travel costs are excluded.
- `condition_ids`: default `["3000"]` (Used). Add `"1500"` for open box or `"2500"` for seller refurbished if appropriate to the category. Parts/not working is intentionally disallowed.
- `interval_minutes`: default **60**, minimum **15**.
- `alert_discount_percent`: default **20** below your used benchmark.
- Seller thresholds: **98% positive**, at least **10** feedback entries.
- Each search has a `query`, exact-model `title_pattern`, `max_price` (item plus known shipping, before tax), and `typical_used_price`.

### Starting budgets (USD)

| Gear | Max total | Used benchmark |
|---|---:|---:|
| Yamaha P-125 | 350 | 425 |
| Yamaha P-225 | 450 | 525 |
| Yamaha P-45 | 250 | 300 |
| Yamaha P-71 | 250 | 300 |
| Roland FP-10 | 325 | 375 |
| Roland FP-30X | 450 | 525 |
| Kawai ES110 | 400 | 475 |
| Kawai ES120 | 500 | 575 |
| AKG C214 | 225 | 275 |

**These are editable starter assumptions, not researched current sold-price averages.** Calibrate them against recent comparable sold listings, accounting for condition and included accessories. The tracker does not retrieve sold comps. Benchmarks should represent comparable pre-tax totals.

To add studio gear, copy a search object, change its unique name, query, prices, and title pattern. For example:

```json
{
  "name": "Focusrite Scarlett 2i2 4th Gen",
  "query": "Focusrite Scarlett 2i2 4th gen",
  "title_pattern": "(?i)focusrite.*2i2.*4th",
  "max_price": 140,
  "typical_used_price": 170
}
```

The example prices are also assumptions. Model matching uses regular expressions; backslashes must be doubled inside JSON strings. Broad queries can miss valid title variants or include mismatches, so review results and tune patterns. Current exclusion rules conservatively skip titles mentioning covers, cases, adapters, repair, or untested gear; this can also exclude legitimate bundles with those words. Remove a pattern if you want to inspect those manually.

## How deal scoring and alerts work

`discount = 100 × (1 − (item price + shipping) / typical used price)`

Listings are grouped by their landed-price discount:

- **Exceptional find:** 50% or more below the benchmark
- **Strong value:** 30–49.9% below
- **Good buy:** 15–29.9% below
- **Fair price:** from 14.9% below through 10% above
- **Above market:** more than 10% above

Unknown shipping produces **Needs details** instead. Exceptional prices still carry a warning to check for scams, missing parts, and hidden faults.

A good-deal alert requires a known total within budget, discount at or above the threshold, and adequate seller feedback. Discounts over 55% are held for manual review. Unknown shipping stays unscored. The cheapest returned USD shipping option is used; verify that option and the final checkout amount for your address. Tax, travel, optional extras, and negotiated offers are excluded.

Auction-only items are excluded so an opening bid cannot masquerade as a purchase price. Fixed-price listings may also offer bidding or negotiation; scoring uses the advertised fixed price.

Alerts occur once per eBay item ID, then again only after a further decrease of at least $5 from its last alerted total. Alerts are persisted across restarts; demo and live state are separate. Changing delivery mode or thresholds does not clear alert history. Delete `data/alert-state.json` if you deliberately want alerts to start fresh. Avoid running multiple tracker processes against the same output folder.

In GitHub live mode, every newly detected **Exceptional find** creates an issue assigned to the repository owner. GitHub can deliver assigned-issue notifications by email and through the GitHub mobile app. Demo listings never create issues.

Before buying a keyboard, test every key and velocity response, sustain, speakers and headphone jacks; check for sticking or unusual mechanical noises and confirm the power supply and included stand/pedal. Review the actual listing and seller; the score is a screening aid.

## Files and troubleshooting

- `data/report.html`: latest live dashboard. Open it in any browser.
- `data/results.json`: latest check, timestamp, candidates, and errors.
- `data/alerts.jsonl`: append-only local alert history.
- `data/alert-state.json`: duplicate suppression state.
- `demo/`: separate invented examples.

Failed searches are displayed as errors, not silently treated as successful empty searches. The latest report does not retain stale listings as current. A partial run may still show successful searches; read its error banners. HTTP 401 means credentials/token trouble, 403 commonly means insufficient API access, and 429 means rate limiting. The watch loop waits until its next interval after failures; no rapid retries. Credentials and tokens are not written to reports or state.

Each search/delivery mode fetches up to three pages of 200 newest results. A banner warns if the cap is reached. Defaults make 18 requests per hourly check when each result set fits on one page; up to 54 with pagination. Observe your eBay application's quota. No API calls happen between checks, and no checks happen while the script is stopped or the machine sleeps.

## Verification and API references

Run `python3 -m unittest -v` from this folder. Tests use fixtures and mocked API responses; they cover shipping, budgets, model/accessory exclusions, seller checks, duplicate alerts, pagination, safe HTML output, failure visibility, and demo isolation. Live authentication and listing retrieval need your production credentials and have not been verified in this delivery.

Official documentation:
- https://developer.ebay.com/api-docs/buy/browse/resources/item_summary/methods/search
- https://developer.ebay.com/api-docs/buy/static/ref-buy-browse-filters.html
- https://developer.ebay.com/api-docs/static/oauth-client-credentials-grant.html
- https://developer.ebay.com/api-docs/buy/buy-requirements.html
