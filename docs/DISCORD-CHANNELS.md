# Discord channels — one per subject

Alerts route by SUBJECT. Seven named channels plus the default deals channel.

🚨 **Every channel is optional, and an unset one is INVISIBLE — not an error.**
A routing rule may never make an alert vanish, so an unset channel falls back
(camcorders → cameras → the default deals channel). The only symptom of a
missing webhook is that those alerts turn up somewhere else. That has already
cost three rounds of "my cards are in the wrong place", so:

**`hunt` prints the truth every run.** Read these lines in the Actions log
before believing anything about where alerts go:

```
[hunt] alert destination: webhook 'Flipscout' -> channel 123 in guild 456
[hunt] cards destination: webhook 'Flipscout cards' -> channel 789 in guild 456
[hunt] watches destination: NOT SET - watches (Citizen / Seiko / G-Shock) alerts
                            fall back to the main channel. Set
                            FLIPSCOUT_WATCHES_WEBHOOK to split them out.
```

## The channels

| Channel | What lands there | Webhook (Secret) | Channel id (Variable) | Price-book categories |
|---|---|---|---|---|
| `#cards` | trading cards (Pokemon + sports) | `FLIPSCOUT_CARDS_WEBHOOK` | `FLIPSCOUT_CARDS_CHANNEL_ID` | `cards, pokemon-cards, sports-cards` |
| `#watches` | watches (Citizen / Seiko / G-Shock) | `FLIPSCOUT_WATCHES_WEBHOOK` | `FLIPSCOUT_WATCHES_CHANNEL_ID` | `watches` |
| `#cameras` | cameras + lenses | `FLIPSCOUT_CAMERAS_WEBHOOK` | `FLIPSCOUT_CAMERAS_CHANNEL_ID` | `cameras, lenses` |
| `#camcorders` | camcorders (Handycam / MiniDV / Hi8) | `FLIPSCOUT_CAMCORDERS_WEBHOOK` | `FLIPSCOUT_CAMCORDERS_CHANNEL_ID` | `cameras + a camcorder title` |
| `#ipods` | iPods + portable audio (Walkman, headphones) | `FLIPSCOUT_IPODS_WEBHOOK` | `FLIPSCOUT_IPODS_CHANNEL_ID` | `headphones, ipods, walkman` |
| `#games` | video games + consoles + Pokemon carts | `FLIPSCOUT_GAMES_WEBHOOK` | `FLIPSCOUT_GAMES_CHANNEL_ID` | `pokemon, videogames` |
| `#sales` | garage + estate sale digests (go-look lists, no prices) | `FLIPSCOUT_SALES_WEBHOOK` | `FLIPSCOUT_SALES_CHANNEL_ID` | *none — named by the caller* |

Everything else — calculators, medical, sewing, collections, and any category
added to the price book later — goes to the default channel
(`FLIPSCOUT_ALERT_WEBHOOK` / `FLIPSCOUT_DISCORD_CHANNEL_ID`). A new category is
never silently swallowed by an existing channel.

## 🚨 Two settings per channel, and they are not the same thing

* the **webhook** (a repo *Secret*) POSTS the card
* the **channel id** (a repo *Variable*) is what lets the bot put the
  🎯 / 🔥 / ❌ tap-to-arm chips on it

Set the webhook and skip the id and the cards arrive **un-armable** — the
seeding call 404s against the wrong channel, silently. The run log says so:
`no FLIPSCOUT_WATCHES_CHANNEL_ID - these post but arrive with no tap-to-arm
chips.`

## Setup, per channel

1. Discord → the channel → **Edit Channel → Integrations → Webhooks → New
   Webhook** → name it → **Copy Webhook URL**.
2. Channel id: enable **Settings → Advanced → Developer Mode**, then
   right-click the channel → **Copy Channel ID**.
3. Invite the same bot (`FLIPSCOUT_DISCORD_BOT_TOKEN`) to the channel with
   **Read Message History** + **Add Reactions**, or the chips never appear.
4. Push both:

```bash
gh secret   set FLIPSCOUT_WATCHES_WEBHOOK    -R lemollon/Flipscout   # paste the URL
gh variable set FLIPSCOUT_WATCHES_CHANNEL_ID --body <channel id> -R lemollon/Flipscout
```

Repeat for `CAMERAS`, `CAMCORDERS`, `IPODS`, `GAMES`, `CARDS`, `SALES`.

🚨 **A secret not mapped in `.github/workflows/watch.yml` is inert and
silent** — this repo's own scar: "three of them were set and silently inert for
a full run, and the log looked perfectly healthy." All fourteen names above are
already mapped, and `test_every_channel_has_a_label_and_a_workflow_mapping`
fails the build if a new channel is added without mapping it.

## `#sales` is the one caller-named channel

Every other channel is chosen by the price book's category, and `#camcorders`
by a title read. Neither can reach the garage/estate digests, because a digest
**has no listings behind it** — those sales carry no item prices at all
(`estates.py`, `garagesales.py`), so they never become priced candidates and
there is no `category` to route on. `hunt.post_estate_digest` and
`hunt.post_garage_digest` therefore pass `channel="sales"` to the notifier and
name the channel outright.

Both digests share the one channel on purpose: they are the same errand — drive
there Saturday, cash in hand, nothing ships — and neither ever carries a max
bid, so splitting them would be two half-empty channels.

🚨 **An online estate auction that resolves to a HiBid catalog does NOT come
here.** Those lots are swept and priced like everything else
(`hunt.estate_catalog_rows`), so an estate camera lot lands in `#cameras` with
its max bid. `#sales` is only the no-price "go look at these" calendars — if a
post has a 🎯 chip on it, it is in the wrong channel.

With `FLIPSCOUT_SALES_WEBHOOK` unset the digests keep landing in the default
deals channel, exactly where they went before this channel existed.

## Camcorders are the one title-based rule

A camcorder is *priced* as a camera (price-book category `cameras`; the book
holds one camcorder model, `sony_handycam`), so the category alone cannot
separate the two. `notify.CAMCORDER_RE` reads the title for the book's own
Handycam patterns plus the tape formats that ARE the value — Video8 / Hi8 /
MiniDV / Digital8, which sell because buyers want to digitise old cassettes.

It is deliberately narrow. A false positive only misfiles between two camera
channels; a broad `video` or `cam` would drag whole film SLRs across.

With `#camcorders` unset, a Handycam lands in `#cameras` — it is a camera —
not in the general deals feed.

## Adding an eighth channel

Three edits, all in one commit:

1. `flipscout/notify.py` — one line in `CHANNELS`, one in `CHANNEL_LABEL`, and
   the categories in `CATEGORY_CHANNEL`. If no price-book category can name the
   channel (as with `#sales`), skip `CATEGORY_CHANNEL` and pass
   `channel="<name>"` from the call site instead.
2. `.github/workflows/watch.yml` — map the webhook secret AND the channel-id
   variable.
3. This file's table.

`discordarm._cfg` polls every id in `CHANNELS` automatically, so arming needs
no change.
