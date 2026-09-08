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
  your goal, and (with a days-to-sell guess) the tighter ceiling that keeps the
  cash moving.
- **Capital velocity** — a HOT / GOOD / SLOW / DEAD strip showing what the item
  earns *per $100 per day* of the cash it ties up. See
  [High-frequency flipping](#high-frequency-flipping--profit-per-dollar-per-day).
- **Goldmine categories** cheat-sheet, tunable **fees & goals**, and a **price
  book** that remembers your comps (saved in the browser, nothing leaves the
  device).

### Capture — stop retyping listings

The manual part of sourcing is retyping every item. Capture kills that while
keeping you on the right side of Facebook's ToS: **you** browse, logged in and at
human speed — Flipscout only reads the listing you already opened. Nothing crawls
or auto-navigates Facebook.

- **On your phone (screenshot):** tap **Capture a listing → 📷 Scan a screenshot**,
  pick a screenshot of the listing, and it reads the title + asking price off the
  image and scores it. The most natural phone capture — no text-selecting. Needs the
  server running (see below); it uses a **vision model** (`ANTHROPIC_API_KEY`) or
  **Tesseract OCR** as a free fallback.
- **On your phone (paste):** in the Facebook app, copy the listing text (title +
  price), open Flipscout, paste, tap **Capture & score**. Works fully offline for the
  parse; auto-fetches the eBay sold price if your lookup server is running.
- **On a computer — bookmarklet:** drag **Fees &amp; goals → Desktop capture →
  “Flipscout Capture”** to your bookmarks bar. On any Marketplace listing, click it
  and Flipscout opens with the name and asking price filled.
- **On a computer — paste or drag a screenshot:** region-screenshot the listing
  (Win: `Win+Shift+S`, Mac: `⌘+Ctrl+Shift+4`), then **paste it (`Ctrl/⌘+V`)**
  anywhere in Flipscout, or **drag the image** onto the capture card. It reads the
  title + price off the image and scores it. (Needs the server running.)
- **Deep links:** the app reads `#name=…&buy=…&sold=…` from the URL, so the
  bookmarklet, a shared link, or a phone Shortcut can all hand off to it.

> Facebook changes its page markup often, so the bookmarklet's auto-grab is
> best-effort — if a field comes across blank, paste the listing instead. The
> parser is deliberately forgiving (handles “Marketplace - …”, “Used · …”, price
> with commas, etc.).

### Live sold-price lookups in the web app (optional)

The **eBay ⤵** button next to the sold-price field looks the price up for you —
but only when the little backend is running. Why a backend? A browser can't call
eBay directly: it would expose your API secret in the page, and eBay blocks
cross-origin browser calls (CORS). The server holds the secret and serves the app
from the same origin, so the page's request is same-origin and works.

```bash
pip install -e ".[server,scan]"          # scan adds screenshot support
export EBAY_CLIENT_ID=...  EBAY_CLIENT_SECRET=...
export ANTHROPIC_API_KEY=...             # for 📷 screenshot scanning (best quality)
uvicorn flipscout.server:app --port 8000
# open http://localhost:8000  →  the eBay button + screenshot scan now work
```

`GET /api/scan` reads a screenshot into `{name, price, condition}` — via a Claude
vision model when `ANTHROPIC_API_KEY` is set, else local Tesseract OCR (`pip install
".[scan]"` + the `tesseract` binary). Without either, it returns a clear 503 and you
just paste or type instead.

**Uploading a photo from your phone.** The upload button works on mobile (it opens
your photo library), but the scan runs on the server — so your phone has to reach a
running Flipscout server. Easiest: run it on your computer bound to your network and
open it from the phone on the same Wi-Fi:

```bash
uvicorn flipscout.server:app --host 0.0.0.0 --port 8000
# then on your phone open  http://<your-computer-LAN-IP>:8000  (e.g. 192.168.1.20)
```

Opening the hosted **artifact** on your phone can't scan (artifacts block outbound
calls) — there, paste the listing text or type the item + eBay sold price instead.

Without the server (opening the file directly, or the hosted artifact), the app
stays **fully usable in estimate mode** — the button just says it can't reach the
server. Live is additive, never required. `flipscout/server.py` exposes
`GET /api/comps?q=...`; point the app at a remote server via **Fees & goals → eBay
lookup server** if you host it elsewhere.

## Deploy (one always-on URL for your phone)

Hosting the server gives you a single `https://…onrender.com` link where the
**screenshot scan** and **live eBay lookups** work from any device — no local
setup, nothing to keep running. `render.yaml` is included:

1. Get free keys: an **eBay** app (<https://developer.ebay.com/>) and an
   **Anthropic API key** (<https://console.anthropic.com/>).
2. In **Render** → **New +** → **Blueprint** → pick the **flipscout** repo.
   Render reads `render.yaml` and asks for three secrets: `ANTHROPIC_API_KEY`,
   `EBAY_CLIENT_ID`, `EBAY_CLIENT_SECRET`. Paste them → **Apply**.
3. Open the URL Render gives you (add it to your phone's home screen). The 📷
   scan and eBay button now work — everything server-side is live.

> Free plan sleeps after ~15 min idle (first request wakes it in ~30-50s). The
> URL is public, so treat it as personal — anyone with the link can spend your
> API quota. Ask me to add a simple access code if you want to lock it down.

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

## Find deals (arbitrage scanner)

The hands-off "deals come to me" engine. Give it searches; it pulls the current
active listings, compares each to the sold median, and ranks the ones priced low
enough to flip after fees — no browsing, no typing. Runs on live eBay data (needs
the API keys).

```bash
# ships to you (the widest pool):
flipscout scan "dewalt dcd771" "sansui receiver" --links
#   $151/hr  $113.10  ROI 189%  buy $60.00 → sell $200.00  DeWalt DCD771 (underpriced)

# local pickup near you (things you can go grab):
flipscout scan "power tools" --local --zip 98101 --links
```

Ranked by **$/hour** — profit ÷ your handling time — so the feed optimizes *money
for the least labor*, not just raw profit (`--minutes` to tune your pace). In the
web app it's the **Find deals** tab (`GET /api/deals?q=a,b,c&local=&zip=`), with a
**"local pickup near me"** toggle.

**Velocity — don't sit on inventory.** Every deal shows an estimated **days-to-sell**
(≈ active listings × 90 ÷ items sold in 90 days) and sell-through. Filter slow
movers out with `--max-days 30` / `--min-st 0.4` (web: the "sells within N days"
box; watcher: `FLIPSCOUT_MAX_DAYS`). The single-item verdict also flags anything
estimated over ~60 days and knocks a BUY down to MAYBE.

**Sources — buy anywhere, comp against eBay.** The scanner separates *where you buy*
from *where you sell*: it lists items from each buying source and prices every one
against eBay sold data. Built in:

- **`ebay`** — underpriced BINs / ending auctions, *shipped-to-you* or *local-pickup
  near your ZIP*. Fully reliable (official API).
- **`goodwill`** — ShopGoodwill.com national online auctions (thrift prices, ships to
  you). Its buyer API is unofficial, so the adapter is **best-effort and fail-soft**
  (errors return nothing rather than breaking the scan) — verify live once deployed.

```bash
flipscout scan "dewalt drill" "vintage receiver" --source ebay,goodwill --links
```

Adding a source is one small file: implement `active_listings(query) -> [{title,
price, url, item_id}]` + a `name` (see `sources.py`); the engine comps and ranks it.
Facebook / OfferUp / Mercari have no API (that's what manual capture is for);
Craigslist blocks cloud servers — so those stay manual.

## Always-on alerts (deals come to you)

Set a **watchlist** once; a scheduler scans it every hour and pushes only the *new*
deals (best $/hour first) to your phone — you never open the app to hunt.

It runs free on **GitHub Actions** (`.github/workflows/watch.yml`) — no server to
host. One-time setup, repo **Settings → Secrets and variables → Actions**:

| Secret | What |
|---|---|
| `EBAY_CLIENT_ID`, `EBAY_CLIENT_SECRET` | your eBay app keys |
| `FLIPSCOUT_WATCHLIST` | your searches, comma/newline separated |
| `FLIPSCOUT_ALERT_WEBHOOK` | a **Discord or Slack** incoming-webhook URL (easiest) |

Alerts route **one Discord channel per subject** — cards, watches, cameras,
camcorders, iPods, games — each an optional pair of a `*_WEBHOOK` secret and a
`*_CHANNEL_ID` variable, falling back to the main channel when unset. Full
table and setup: **[docs/DISCORD-CHANNELS.md](docs/DISCORD-CHANNELS.md)**.

Tune with optional repo **Variables** (`FLIPSCOUT_MIN_PROFIT`, `FLIPSCOUT_MIN_ROI`,
`FLIPSCOUT_SOURCES`, `FLIPSCOUT_TOP`, `FLIPSCOUT_LOCAL`, `FLIPSCOUT_ZIP`). The
workflow persists a seen-cache so you're not re-alerted on the same item. Test it
locally first with `flipscout watch --dry`.

> Prefer email? Set `FLIPSCOUT_SMTP_HOST/PORT/USER/PASS` + `FLIPSCOUT_ALERT_TO`.
> (Render's cron is a paid add-on, which is why the default scheduler is Actions.)

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

## Sports cards (the card-shop buy box)

Cards are the one category where a title tells you almost everything *except*
the price. A card shop sorts a box in seconds on five rules — era, hits, chase
cards, rookies, brand — and `flipscout card` is those five rules:

```bash
flipscout card "2018 Panini Prizm Luka Doncic Silver Prizm RC Auto /99"
```

```
CHASE - pull it out and photograph it.  (score 117)
  - AUTOGRAPH - a hit card. Cannot be in every pack, so it is never base.
  - SERIAL NUMBERED /99 - the range where the serial starts carrying the price.
  - PARALLEL / INSERT - a chase card: in packs, but not every pack.
  - ROOKIE / first-year card - the year the hobby actually pays for.
  ...
```

> **It gives a verdict, never a price.** A title cannot carry condition, and
> condition is most of a raw card's value — so this tells you what to *look at*,
> not what to pay. The rules, where they came from, and the one place the code
> departs from the shop's advice: **[docs/CARD-BUY-BOX.md](docs/CARD-BUY-BOX.md)**.

The short version: **avoid 1980–1999** (junk wax — printed without limit),
ignore **base cards** (they're in every pack), and hunt **autos, serial-numbered
cards, parallels and rookies**. Two named cards escape the era rule — a
**Jordan 86** and a **Kobe 96** — and so does any **9 or 10 grade**, because
condition is the only scarcity junk wax has left.

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

## High-frequency flipping — profit per dollar per *day*

Everything above answers *"is this item profitable?"*. That stops being the right
question about a week into sourcing seriously, because by then you have more
deals than money. The right question is the one a trading desk asks:

> **how much does each dollar of my capital earn, per day it is tied up?**

A **$200** profit on a **$400** buy that takes eight months is a *worse* use of
$400 than a **$25** profit on a **$40** buy that clears in three weeks — the
second recycles that $40 seventeen times a year. Margin flatters slow inventory.
Only velocity exposes it:

```
velocity = net profit ÷ total cost ÷ hold days      →  reported as $ per $100 per day
```

Three things fall out of taking that seriously, and they're the whole feature:

1. **Hold days are not days-to-sell.** Your cash is dead from the moment you hand
   it over to the moment eBay pays out: prep + listing + transit + the payout
   hold. That routinely adds two weeks to a "sells in 9 days" item.
2. **Capital isn't the only budget.** Every flip costs the same ~25 minutes of
   handling whether it clears $8 or $80, so a tiny fast flip can have a superb
   velocity and still be a bad evening. There's a dollars floor and an hourly
   floor, and either one caps a deal at SLOW.
3. **The binding constraint is the whole answer.** Out of money first → find
   cheaper, faster flips. Out of hours first → buy fewer, bigger ones. Out of
   *deals* first → more of both is wasted; go source.

### `velocity` — score one item as a use of cash

```bash
python -m flipscout.cli velocity "Switch OLED console" --buy 120 --sold 250 --ship-cost 12 --days-to-sell 9
```
```
Switch OLED console
  verdict      BUY / velocity HOT
  buy          $120.00  (+$12.00 ship/extras)
  sells for    $250.00   fees $33.52
  net profit   $84.47   ROI 64%
  hold         16 days  (~9d to sell + prep/ship/payout)
  velocity     $4.00 per $100 per day   (22.8 turns/yr, 1460%/yr simple)
  your time    25 min -> $202.74/hour
  PAY <= $ 181.28  for a $250.00 item you'll hold ~16d at $0.75/$100/day (needs 12% ROI; ...)
```

Exit codes are script-friendly: **0** = worth the slot (HOT/GOOD), **1** = SLOW or
DEAD money, **2** = needs a sold comp first. Leave `--days-to-sell` off and it
falls back to the sold/active-count estimate, then to a deliberately pessimistic
45 days — and says so, rather than quietly guessing.

| Tier | Means |
|---|---|
| **HOT** | ≥ $2.00 per $100 per day. Buy it before someone else does. |
| **GOOD** | ≥ $0.75. Solidly beats parking the cash. |
| **SLOW** | ≥ $0.25, *or* a fast flip that trips the $ / hourly floor. |
| **DEAD** | Loses money, or ties your cash up for a rounding error. |

### `portfolio` — spend a bankroll, not an item

The actual high-frequency question. You have $200 and three hours this week;
which of tonight's finds do you buy?

```bash
python -m flipscout.cli portfolio deals.csv --bankroll 200 --hours 3
```
```
PLAN  3 buy(s), $179.00 of $200.00 deployed, 1.2 of 3.0 hours
         HOT  Levi 501 W34                     buy $   8.00  profit $  13.36  hold  21d  $4.54/$100/day  17.4 turns/yr  $ 32.06/hr
         HOT  Nintendo Switch OLED console     buy $ 120.00  profit $  84.47  hold  16d  $4.00/$100/day  22.8 turns/yr  $202.74/hr
         HOT  Carhartt jacket XL               buy $  20.00  profit $  27.33  hold  32d  $2.59/$100/day  11.4 turns/yr  $ 65.58/hr
  -> $125.16 profit when it all clears; $47.39/week run-rate; $3.62/$100/day blended
  -> BINDING CONSTRAINT: capital. You ran out of money before deals. Favour cheaper,
     faster flips (or sell what's sitting) — more sourcing won't help today.
  skipped 3:
    Vintage Pyrex bowl set        needs $30.00, only $21.00 left
    Dyson V8 vacuum               needs $113.00, only $21.00 left
    Lego Millennium Falcon 75257  DEAD at $0.21/$100/day — below the GOOD bar
```

Every skip carries its reason, because *"why didn't it buy that one"* is the
question you'll actually have. `--min-tier SLOW` loosens the gate; `--hours`
is the labor budget that decides whether you're capital- or time-limited.

### `turns` — what your capital *actually* earned

`velocity` is a forecast. The ledger isn't. `turns` reads your real buys and
sales, computes velocity the way a fund would (total profit over total
**capital-days**, so one big slow position drags the number down instead of
hiding inside an average), and names the money that has stopped working:

```bash
python -m flipscout.cli turns
```
```
REALIZED VELOCITY  2 closed flip(s)
  profit         $186.50 over $2,738 capital-days
  velocity       $6.81 per $100 per day (HOT)
  avg hold       56 days -> 6.6 turns/yr on that slot

    #  held      paid    profit  $/100/day  item
    1   19d $   40.00 $  110.00      14.47  Sansui receiver
    2   92d $   21.50 $   76.50       3.87  Gunne Sax dress

  $125.00 parked in 2 unsold item(s)
  🚨 $90.00 has been sitting >60 days in 1 item(s) — that capital is earning
     $0.00/day. Reprice or dump:
     #4    177d  $   90.00  Lego bulk lot
```

Record the inputs with `flipscout bought` / `flipscout sold` (see `pnl`).

### `maxpay --velocity` — the walk-away price, with a clock on it

The identity worth internalising: **requiring `v` per $100 per day over `d` days
held is exactly requiring an ROI of `v × d`.** Velocity isn't new math — it's ROI
with the clock finally attached. So the same in-the-aisle question gets a second,
often much tighter answer:

```bash
python -m flipscout.cli maxpay --sold 250 --ship-cost 12 --velocity 0.75 --days-to-sell 120
```
```
PAY <= $ 132.32  to flip a $250.00 item (then ~$72.16 profit, 50% ROI; limited by roi)
PAY <= $  98.87  for a $250.00 item you'll hold ~127d at $0.75/$100/day (needs 95% ROI; ...)
```

Your profit goal says $132. Four months of dead cash says $99. The lower number
is your real ceiling.

### In the web app

The phone tab shows it too: a **HOT / GOOD / SLOW / DEAD** strip under the usual
verdict with `$ per $100 per day`, days of dead cash, turns a year and dollars an
hour of your time — plus a "days to sell" box under *More detail*, and the
velocity ceiling on the **Max price** tab. Tune prep/transit/payout days, handling
minutes and the tier lines under **Fees & goals → Capital velocity**.

Both sides run the *same* arithmetic, and `tests/test_web_velocity_parity.py`
lifts the JS straight out of `web/index.html` and runs it under node against the
Python to keep it that way.

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
- **velocity** — profit ÷ cost ÷ **hold days**, shown as `$ per $100 per day`. The one
  number that ranks deals correctly once capital, not opportunity, is what you're short
  of. `$1.00/$100/day` is roughly a 365% simple annual return on that slot.
- **hold days** — buy → spendable cash: prep + days-to-sell + transit + eBay's payout
  hold. Always longer than days-to-sell, which is why slow items are worse than they look.

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
| days_to_sell | `--days-to-sell` | your own estimate of days to sell (velocity) |

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
| `maxpay`    | highest price to pay for a given eBay sold price (`--velocity` adds the time-aware ceiling) |
| `velocity`  | score one item on profit per dollar per **day** |
| `portfolio` | spend a bankroll on the best-velocity candidates in a CSV |
| `turns`     | realized velocity + parked capital, from your ledger |
| `scan`      | arbitrage scan — find underpriced listings, ranked by $/hour (needs eBay keys) |
| `watch`     | run your watchlist once and alert on new deals (the always-on job) |
| `remember`  | save a comp to your personal price book |
| `goldmines` | print the margin-ranked buy-box cheat-sheet |
| `card`      | triage a sports-card title — chase/hit/rookie read, no price |
| `cardcomp`  | measure a sports-card tier in the browser → a real priced model |

## Layout

```
flipscout/
  fees.py       eBay managed-payments fee model + net-proceeds math
  comps.py      comps provider interface + offline EstimateComps + price book
  ebay_api.py   live eBay provider: OAuth + Browse + Marketplace Insights
  analyzer.py   combine cost + comps + fees -> scored verdict; maxpay; CSV
  velocity.py   capital velocity: $/$100/day, hold cycle, bankroll allocation
  ledger.py     what you actually bought and sold (feeds `turns` and `pnl`)
  categories.py goldmine-category cheat-sheet
  cards.py      sports-card triage: the card-shop buy box, as regexes
  cli.py        the `flipscout` command (item|csv|maxpay|remember|goldmines|card)
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
