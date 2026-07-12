# flipscout — eBay sourcing profit analyzer

A tool for the actual hard part of reselling: **deciding what's worth buying.**
Anyone can find cheap stuff. The winners are the flips where the eBay sale price,
*minus fees and shipping*, comfortably beats what you paid — and that sell fast
enough that your cash isn't stuck. This does that math for you in one line.

## Web app (phone + computer)

`web/index.html` is a **self-contained** page — no server, no build, works
**offline**. Open it directly in any browser (double-click the file, or on your
phone: put it in iCloud/Drive and open it, then *Add to Home Screen*). It runs the
exact same math as the CLI:

- **Should I buy?** — type buy price + eBay sold price; get a live BUY / MAYBE /
  SKIP / NEEDS-COMP verdict with profit, ROI, and sell-through.
- **Max price** — the in-the-aisle question: the highest you can pay and still hit
  your goal.
- **Goldmine categories** cheat-sheet, tunable **fees & goals**, and a **price
  book** that remembers your comps (saved in the browser, nothing leaves the
  device).

### Live sold-price lookups in the web app (optional)

The **eBay ⤵** button next to the sold-price field looks the price up for you —
but only when the little backend is running. Why a backend? A browser can't call
eBay directly: it would expose your API secret in the page, and eBay blocks
cross-origin browser calls (CORS). The server holds the secret and serves the app
from the same origin, so the page's request is same-origin and works.

```bash
pip install -e ".[server]"
export EBAY_CLIENT_ID=...  EBAY_CLIENT_SECRET=...
uvicorn flipscout.server:app --port 8000
# open http://localhost:8000  →  the eBay button now fills sold price + counts
```

Without the server (opening the file directly, or the hosted artifact), the app
stays **fully usable in estimate mode** — the button just says it can't reach the
server. Live is additive, never required. `flipscout/server.py` exposes
`GET /api/comps?q=...`; point the app at a remote server via **Fees & goals → eBay
lookup server** if you host it elsewhere.

## Install (CLI)

```bash
git clone https://github.com/lemollon/flipscout.git
cd flipscout
pip install -e ".[dev]"     # installs the `flipscout` command + pytest
pytest -q                    # 26 tests
```

After install you can use the `flipscout` command directly (or `python -m
flipscout.cli`), e.g. `flipscout item "..." --buy 40 --sold 95`.

## Why it doesn't scrape Facebook

Facebook Marketplace / Shop has **no public API**, and automated scraping of it
**violates Facebook's Terms of Service** — a fast way to get your account banned.
So this tool never touches Facebook. Instead you source the way that's always been
allowed: **you** browse (Marketplace, thrift, garage sales, clearance) at human
speed, and when you spot something, you feed it here to find out if it's a real
flip. Same workflow works for anything you find anywhere.

For pricing it uses **eBay's own data**, two ways:
- **Estimate mode (no keys):** search eBay, tick the **"Sold items"** filter, read
  the median price, and type it in with `--sold`.
- **Live mode (`--ebay`):** with free eBay developer keys, the tool fetches sold
  prices and listing counts itself. See **Live eBay API** below.

### Live eBay API

Get free app keys at <https://developer.ebay.com/> (create an app → OAuth
client credentials), then:

```bash
export EBAY_CLIENT_ID=...  EBAY_CLIENT_SECRET=...   # EBAY_ENV=sandbox to test
python -m flipscout.cli item "DeWalt DCD771 drill" --buy 40 --ebay
```

`flipscout/ebay_api.py` handles OAuth (token cached until expiry), the **Browse**
API (active listing count + asking prices), and **Marketplace Insights** (actual
sold prices, last ~90 days). It degrades honestly:

> ⚠️ Marketplace Insights is a **Limited-Release** API — eBay must approve your
> app for it. Until approved, the sold-price call returns 403 and the tool falls
> back to **NEEDS_COMP** (you still get the active count from Browse). It never
> passes off *asking* prices as *sold* prices.

## Quick start

Check one item while you're standing in the store:

```bash
python -m flipscout.cli item "Nintendo Switch OLED" \
    --buy 120 --sold 250 --ship-cost 12 --sold-count 800 --active-count 400
```
```
       BUY  Nintendo Switch OLED   buy $120.00  sell $250.00  profit $84.47  ROI 64%  ST 67%
```

Or score a whole spreadsheet you built while sourcing:

```bash
python -m flipscout.cli csv flipscout/sample_items.csv
```

## Where the money is (goldmine categories)

There's no secret hot-items list — viral items get swarmed and the margin dies.
The durable edge is **category patterns**: Facebook sellers price to get rid of
things, eBay prices by brand/model demand, and the gap is your margin. Print the
starter buy-box and learn a few cold:

```bash
python -m flipscout.cli goldmines
```

The stable winners: **power tools** (best profit-per-effort), **bulky items nobody
wants to ship** (furniture, gym gear — the shipping pain IS the edge, via local
pickup), **game consoles/retro**, **tech** (scam-check for activation locks),
**vintage/branded clothing**, and **LEGO/cards/collectibles**. The pattern: bulky
= less competition; branded tools/tech = deep demand + easy comps; vintage =
seller mispricing. Best flips overlap two (a branded tool at an estate sale).

> A cheat-sheet is a starting point, never a buy signal. The only research that
> decides a purchase is the **sold data on that specific item** — check eBay's
> Sold filter (or run `item`) every time.

## Make the most of your driving time

There's infinite inventory on Marketplace — the skill is spending minutes only on
the few items that pay. A workflow that keeps you fast:

1. **Sell-through before price.** Live in categories that *move*. Cash that sits
   isn't cash. Sort finds by ST, not by headline profit.
2. **Have a "buy box."** Learn 5–10 categories cold so you can judge them on sight;
   scroll past everything else.
3. **Saved searches, not the feed.** Let new listings in your box come to you.
4. **Know your walk-away number before you message** — see `maxpay` below.
5. **Batch the driving.** Collect BUYs, then route them into one loop. The drive is
   your biggest time cost; amortize it across pickups.
6. **Never research the same thing twice** — build a price book (`remember` below).

### `maxpay` — the in-the-aisle question

Standing in front of an item, you don't want a full analysis — you want *"what's
the most I can pay?"* Glance at the eBay sold price, and:

```bash
python -m flipscout.cli maxpay --sold 250 --ship-cost 12
```
```
PAY <= $132.32  to flip a $250.00 item (then ~$72.16 profit, 50% ROI; limited by roi)
```

Now you know your ceiling before you send a single message — and exactly how hard
to haggle. Tune the goal with `--min-profit` / `--min-roi`.

### Price book — research once, reuse forever

The same models recur constantly. Save a comp and every future sighting is instant
(no `--sold` needed):

```bash
python -m flipscout.cli remember "Switch OLED console" --sold 250 --sold-count 800 --active-count 400
python -m flipscout.cli item "Switch OLED console" --buy 120 --ship-cost 12 --memory
```

`--memory` (bare) uses `flipscout/comps_memory.json`; pass a path to keep several
books. Works on `csv` too, auto-filling any blank `observed_price`.

## The verdicts

| Verdict | Meaning |
|---|---|
| **BUY** | Clears your profit **and** ROI bar, and sells fast enough. |
| **MAYBE** | Profitable but thin, or a slow seller — cash may sit. |
| **SKIP** | Loses money after fees, or too little upside for the cash. |
| **NEEDS_COMP** | No sold price yet — go look one up before deciding. |

## What the numbers mean

- **profit** — eBay net proceeds (after the ~13.25% final value fee, the $0.40 per‑order
  fee, and *your* shipping cost) minus what you paid and any extras. Real take‑home.
- **ROI** — profit ÷ cash you tie up. `$27 profit` on a `$20` buy is a great **83% ROI**;
  the same `$27` on a `$90` buy is only 30% — same profit, worse use of your money.
- **ST (sell‑through)** — sold ÷ (sold + active). How fast it moves vs. the competition.
  Get both counts from eBay's **Sold** and **Active** search filters.

## Inputs (CLI flags / CSV columns)

| Field | Flag | Meaning |
|---|---|---|
| title | *(positional)* | what it is / your eBay search |
| source_price | `--buy` | what you'd pay locally |
| observed_price | `--sold` | median eBay **sold** price |
| shipping_cost | `--ship-cost` | postage **you** pay |
| shipping_charged | `--ship-charge` | postage the **buyer** pays (0 = free shipping) |
| extra_cost | `--extra` | supplies, refurb, gas, per item |
| sold_count | `--sold-count` | # sold in lookback (for ST) |
| active_count | `--active-count` | # active listings (for ST) |

Tune the bar with `--min-profit`, `--min-roi`, `--fvf` (your category's fee),
`--promoted` (ad rate), or `--conservative` for a stress-case fee model.

## CSV format

Header row required. Only `title` and `source_price` are mandatory; leave any
other cell blank for "unknown". See `sample_items.csv`. Blank `observed_price`
→ the item comes back as **NEEDS_COMP** so you know to go price it.

## Commands

| Command | What it does |
|---|---|
| `item`      | score one candidate (`--ebay` for live sold data) |
| `csv`       | score a spreadsheet of candidates, best first |
| `maxpay`    | highest price to pay for a given eBay sold price |
| `remember`  | save a comp to your personal price book |
| `goldmines` | print the goldmine-category buy-box cheat-sheet |

## Layout

```
flipscout/
  fees.py       eBay managed-payments fee model + net-proceeds math
  comps.py      comps provider interface + offline EstimateComps + price book
  ebay_api.py   live eBay provider: OAuth + Browse + Marketplace Insights
  analyzer.py   combine cost + comps + fees -> scored verdict; maxpay; CSV
  categories.py goldmine-category cheat-sheet
  cli.py        the `flipscout` command (item|csv|maxpay|remember|goldmines)
  sample_items.csv
tests/test_flipscout.py     26 tests
pyproject.toml  ·  .github/workflows/ci.yml  ·  LICENSE
```

## Roadmap (when you're ready)

1. **Get eBay Marketplace Insights approval** so live sold prices flow in every
   time (Browse works immediately; Insights is a Limited-Release grant).
2. **Category fee table** — FVF varies by category; map title → category → rate.
3. **Phone-friendly web UI** — score items from your phone while sourcing.
4. **Route planner** — cluster your BUY list into one efficient pickup loop.

> Fee rates change and vary by category — treat results as "good enough to decide
> whether to buy," not accounting. Verify against your eBay fee page.
