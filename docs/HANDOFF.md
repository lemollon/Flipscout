# Setup handoff — always-on deal alerts (no hosting)

This sets up Flipscout's **always-on deal watcher**: a free hourly GitHub Actions job
that scans your watchlist and pushes new eBay arbitrage deals to your Discord (or
Slack). No server to host, nothing to pay or maintain.

## First: get your Discord webhook (~30 seconds)

1. In Discord, pick (or make) a channel for deals — e.g. `#flips`.
2. Channel name → **Edit Channel** → **Integrations** → **Webhooks** → **New Webhook**.
3. Name it "Flipscout", **Copy Webhook URL**. That URL is your `FLIPSCOUT_ALERT_WEBHOOK`.

(Slack works too: create an *Incoming Webhook* app and copy its URL.)

## Then: hand this prompt to your terminal coding agent

Paste the block below into Claude Code (or any CLI agent) in a fresh folder. It
tests against real eBay + your webhook *before* scheduling, so you'll see a real
alert land before you trust it.

```text
You are setting up "Flipscout"'s always-on deal watcher. NO web hosting / NO Render —
just the free GitHub Actions hourly job that pushes eBay arbitrage deals to my
Discord/Slack. Repo: https://github.com/lemollon/Flipscout (private). It already
contains the code and .github/workflows/watch.yml — do NOT rewrite the app.

RULES:
- Never commit or print secrets. Store them only via `gh secret set` (encrypted) and,
  for local testing, as shell env vars in this session (a .env is fine — it's gitignored).
- Ask me for any secret value; never invent one. If a step needs my browser (getting
  API keys, a Discord webhook), STOP and tell me exactly what to click.

STEP 0 — Preflight:
  git clone https://github.com/lemollon/Flipscout && cd Flipscout
  pip install -e . && pytest -q            # confirm green

STEP 1 — Get these from me:
  EBAY_CLIENT_ID, EBAY_CLIENT_SECRET   (developer.ebay.com -> your app's OAuth keys)
  FLIPSCOUT_WATCHLIST                  (my searches, comma-separated, e.g.
                                        "dewalt dcd771, canon powershot, sansui receiver")
  FLIPSCOUT_ALERT_WEBHOOK             (a Discord or Slack incoming-webhook URL)

STEP 2 — Prove it works LOCALLY before scheduling (real eBay + real webhook):
  export EBAY_CLIENT_ID=... EBAY_CLIENT_SECRET=... EBAY_ENV=production
  export FLIPSCOUT_WATCHLIST="..." FLIPSCOUT_ALERT_WEBHOOK="..."
  export FLIPSCOUT_MIN_PROFIT=20 FLIPSCOUT_MIN_ROI=0.6 FLIPSCOUT_TOP=10
  flipscout watch --dry     # prints the deal digest, sends nothing — sanity check
  flipscout watch           # actually posts to my webhook — confirm I got the message
  If eBay's Marketplace Insights (sold prices) isn't enabled on my app yet, deals may
  be empty; tell me so I can request that access. Tune thresholds with me until the
  digest looks right.

STEP 3 — Schedule it free on GitHub Actions:
  gh secret set EBAY_CLIENT_ID          -R lemollon/Flipscout
  gh secret set EBAY_CLIENT_SECRET      -R lemollon/Flipscout
  gh secret set FLIPSCOUT_WATCHLIST     -R lemollon/Flipscout
  gh secret set FLIPSCOUT_ALERT_WEBHOOK -R lemollon/Flipscout
  # optional tuning as repo Variables:
  gh variable set FLIPSCOUT_MIN_PROFIT --body 20 -R lemollon/Flipscout
  gh variable set FLIPSCOUT_MIN_ROI    --body 0.6 -R lemollon/Flipscout
  # trigger a test run and watch it:
  gh workflow run "Flipscout deal watch" -R lemollon/Flipscout
  gh run watch -R lemollon/Flipscout
  Confirm the run succeeded AND a deal alert hit my webhook. It then runs hourly.

STEP 4 (optional) — Local web UI for when I want the screenshot scan / live buttons:
  pip install -e ".[server,scan]"
  export ANTHROPIC_API_KEY=...     # only needed for screenshot scan
  uvicorn flipscout.server:app --port 8000     # open http://localhost:8000

REPORT BACK: that the local `flipscout watch` posted to my webhook, that the GitHub
Actions test run succeeded, and that hourly scheduling is active.
```

## What you need in hand

- **eBay app keys** (`EBAY_CLIENT_ID` / `EBAY_CLIENT_SECRET`) from developer.ebay.com.
- **Your Discord/Slack webhook URL** (above).
- No Anthropic key needed for alerts — only for the optional local screenshot scan.

## What runs where (nothing to host)

| Piece | Runs on |
|---|---|
| Hourly deal alerts | GitHub Actions (free) → your Discord |
| `flipscout` CLI (scan / item / watch) | your computer |
| Web UI (screenshot scan, live buttons) | your computer, on demand (`uvicorn`) |
