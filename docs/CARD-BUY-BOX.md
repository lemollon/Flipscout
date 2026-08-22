# The card buy box

Source: Leron's friend, who works the counter at a card shop, 2026-08-22.

He gave the whole thing in about ninety seconds, and it turned out to be five
rules — every one of them readable from a listing title. This page is those
rules, what the code does with them, and the one place the code deliberately
disagrees with him.

Code: [`flipscout/cards.py`](../flipscout/cards.py) · tests:
[`tests/test_cards.py`](../tests/test_cards.py) · try it:

```
flipscout card "2018 Panini Prizm Luka Doncic Silver Prizm RC Auto /99"
```

## 🚨 This does not price anything, on purpose

`pricebook` has one law: a model ships only with a **measured** comp. The
honest reading of the advice below is that none of it produces a dollar figure
— it produces the thing the shop actually does in the first two seconds: *keep
this one, bin that one*.

So `cards.read()` returns a **verdict**, never a price, and nothing in it is
allowed to move a bid ceiling.

| module | answers |
|---|---|
| `pricebook` | "this is worth $112.50, don't pay over $34" (measured) |
| `cards` | "this is worth **photographing**" (triage) |

That is worth shipping on its own, because the losing move in this category is
not mispricing a card — it is sinking an hour into a shoebox of 1990 Score. A
card table at an estate sale is a thousand titles and one of them matters.
Five is a number a human can price by hand.

The three Pokémon tiers in `pricebook` are the only cards in this repo carrying
a number, because they are the only ones anyone measured. `cards.read()`
recognises a TCG title and **hands it straight back** rather than second-guess
a measured comp with a triage score.

## The five rules

### 1. Avoid the 80s and 90s

> "You just want to avoid 80s and '90s, so anything in the 2000s or later."

This is the **junk wax era** and it is the most reliable rule in the hobby: the
manufacturers printed to meet demand that never came, so supply is effectively
infinite and a mint 1990 common is worth less than the sleeve you put it in.

**The escape is a named card, not a date.** Asked directly whether he really
avoids the whole of both decades, 2026-08-22:

> "80's and 90s unless **Kobe 96** or **Jordan 86**"

So the veto runs **1980–1999**, and two specific cards are excepted by name:

| card | why |
|---|---|
| **Jordan, 1986** | the 1986 Fleer rookie — the card of the era |
| **Kobe, 1996** | the 1996–97 rookie year |

An earlier version of this file invented a **1987 cutoff** instead, reasoning
that the print-run explosion starts that year and a literal "avoid the 80s"
bins the Fleer Jordan. He was asked, and his answer is both more faithful and
more accurate: a 1986 Donruss common is exactly as worthless as a 1991 one, and
no date range separates them — what separates them is being one of two
particular rookies. The 1987 boundary is gone.

**It is a card, not a player and not a year.** A 1991 Fleer Jordan takes the
full veto; so does a 1998 Kobe; so does a 1986 Donruss common. That pairing is
also what makes a bare surname safe to match — `jordan` alone would catch
Jordan Love, but paired with 1986 it can only be the one Jordan.

A named exception waives the era rule *outright*, and waives its bulk-era brand
penalty too: 1986 Fleer is the exact product that matters, so scoring it as a
junk brand would contradict the exception in the same breath as granting it.

**Adding more is one line each** in `_ERA_EXCEPTIONS` plus one test — if he
names others (a Griffey 89, a Rice 84, a LeBron 03), they go straight in.

**The other exception:** a **9 or 10 grade beats the era veto**. Junk wax
was printed without limit, so the only scarcity it has left is *condition* —
which is exactly what a slab certifies. A PSA 10 1989 rookie is a real card in
a decade of coasters.

Pre-1980 is a different game entirely: printed small, and thrown away.

> Nothing in the code now departs from the shop's advice. The one place it did
> is the section above, and he settled it.

### 2 & 3. Chase cards and hit cards, not base cards

> "If you see cards that are just base, those are base cards and they're easily
> found, they come in all packs. So you're looking for chase cards and hit
> cards. [A chase card is] something inside packs that don't come in every
> pack."

- **Hit** — an autograph or a memorabilia swatch. Manufactured scarce: it
  *physically cannot* be in every pack, so it is the one signal that needs no
  other evidence. A patch outranks a plain jersey relic.
- **Chase** — parallels, inserts, refractors, short prints. In packs, but not
  every pack.
- **Base** — the default verdict. No auto, no serial, no parallel, no rookie:
  this is what the box is full of, and the reader says so out loud.

### 2b. Numbered cards

> "They're autographed cards, they're numbered cards, stuff like that is what
> you're looking for."

Scored by how small the run is: `/10` is case-hit scarce, `/99` is where the
serial starts carrying the price, `/500` is numbered in name only. `1/1` beats
everything.

**The trap this category sets:** `124/165` on a junk-wax card is *where it sits
in the set*, printed on the front of nearly every base card of that era.
Serial numbering did not exist before about **1996**, so a slash pair on an
older card is never read as a print run — without that guard the whole junk-wax
bin turns into false hits.

### 4. Rookies

> "There's rookies — like when you find rookies, those are worth more."

`RC`, "rookie", "1st Bowman", "Young Guns", `RPA`. A rookie patch auto fires
all three rules at once, which is why it sits at the top of the hobby.

### 5. Brands

> "There's brands of cards that are more expensive than other cards."

Three tiers, because that is as fine as a title can honestly cut it:
ultra-premium (National Treasures, Flawless, Immaculate), premium
(Chrome, Prizm, Optic, Select), and the bulk-era brands whose base product was
printed without limit (Score, Fleer, Pinnacle, Skybox).

**Brand is a bump, never a verdict.** A National Treasures *base* card is still
a base card. The code enforces this literally: a positive brand bump scores
zero unless a real signal fired first. A *negative* brand note always stands —
a warning needs no permission.

## The rule the code cannot finish

> "There's players that are better than other players."

True, and mostly unencodable from a title. A real player list is thousands of
names, it re-ranks every season, and a stale one is worse than none — it would
confidently pass over whoever broke out last year.

So the code carries a deliberately **short** list of names whose value has been
stable for a decade or more, it only ever **adds**, and the absence of a name
is never held against a card. This is the part of the friend's answer that ends
with "there's so much knowledge that goes into knowing" — and he is right, so
the code does not pretend otherwise.

## What a verdict means

| verdict | do this |
|---|---|
| `CHASE` | pull it out of the box and photograph it |
| `LOOK` | one signal fired; a human has to finish this one |
| `PASS` | the rules say this is what the box is full of |
| `PRICED` | it's a TCG single — ask `pricebook`, it has a measured comp |
| `UNKNOWN` | not proved to be a trading card, so no opinion offered |

## Two things it refuses outright

- **Reprints, proxies, customs.** That is the whole card, whoever is on it.
- **Piles.** Lots, binders, boxes, complete sets. Same finding `pricebook`
  already recorded for Pokémon lots: p25 $10.72, median $25.18, max $1,061 on
  n=65 — a hundred-fold spread the title cannot resolve. Buy those off the
  photos or not at all.

## Where it shows up

- `flipscout card "<title>"` — the standing-at-the-table check.
- Alerts: any listing `hunt` alerts on that proves it is a sports card gains
  one `Card read:` line. It never touches the ceiling — those numbers come from
  a measured comp, and letting a triage score move a bid would put an
  unmeasured guess behind money.

Measured 2026-08-22: **0 of the 521 listings** on the live board draw a card
line, which is the contract — a reader that speaks up on cameras and
calculators turns every alert into wallpaper. That is pinned as a test.

## The #cards channel

Card alerts go to their own Discord channel; everything else stays in the main
one. Two repo secrets, both optional — with neither set, everything lands in
the main channel exactly as before:

```bash
gh secret set FLIPSCOUT_CARDS_WEBHOOK    -R lemollon/Flipscout   # the webhook URL
gh variable set FLIPSCOUT_CARDS_CHANNEL_ID --body <channel id> -R lemollon/Flipscout
```

**Both are needed, and they are not the same thing.** The webhook is how alerts
are *posted*; the channel id is how the bot *places the 🎯 / 🔥 / ❌ chips* on
them. Set only the webhook and cards arrive un-armable — the seeding call 404s
against the default channel and nothing visible says so. `describe_webhook()`
prints the channel id a webhook resolves to:

```bash
python -c "from flipscout.notify import describe_webhook; print(describe_webhook('<url>'))"
```

Routing rules:

- A listing the book priced as a **card category** (`pokemon-cards`) routes on
  that — authoritative, because it is what the money was computed from.
- Anything else whose **title reads as a sports card** routes on the reader.
- 🚨 **An unset cards webhook falls back to the main channel.** A routing rule
  may never make an alert vanish; silence is the failure that looks exactly
  like the watcher having died.

`discordarm` polls every configured channel, so arming works in both.

## What actually arrives there

Three things now, where before there was one:

1. **Priced Pokémon card alerts** — the graded and vintage-chase tiers, with real ceilings. These are *newly reachable*; see the bug below.
2. **Card scout finds** — sports cards no model can price, posted with **no comp and no ceiling**, clearly labelled. `hunt.scout_cards()` runs on the listings `evaluate()` threw away, and on both of `run()`'s exits — the day nothing clears the priced bar is exactly the day a card table is worth walking.
3. **A sold-price lookup on every card**, priced or not (below).

### 🚨 The bug that made all of this unreachable

`ACCESSORY_EXCLUDE` — a **universal, non-overridable** guard that `match()` runs once per listing before any model — rejected the bare word **"card"**. Measured 2026-08-22, five of seven realistic graded-Pokémon titles were thrown out before any model was consulted:

```
REJECT  Pokemon Charizard PSA 10 Card
REJECT  1999 Pokemon Base Set Charizard Holo PSA 9 Trading Card
REJECT  Pokemon Card PSA 10 Umbreon VMAX Alt Art
MATCH   Pokemon Charizard PSA 10          <- survived only by not saying "card"
```

The rule is right and its reasons are real — a Pokémon *promo card* must never price against a $145 cartridge comp, and cameras legitimately read "w/ SD Card", which is why it is bundle-aware. It was simply in the wrong place: the card tiers were added after it, and no model can override a universal guard. So they were built, comped over three browser sessions (n=111/256/142), and made unreachable in the same file.

Now applied **by category**, exactly as `_CAMERA_JUNK` and `_CONSOLE_JUNK` are. Every non-card model keeps the identical guard; the card tiers are freed from it. Pinned by tests on both sides.

### And nothing was searching for cards

Of 133 search terms, the only ones containing "card" or "pokemon" were *video game* searches. The measured card tiers could only fire by accident, when a card happened to land in a Game Boy search. 16 card terms added (149 total, ~+12% eBay Browse calls — inside observed headroom, but watch the error counter).

## Value assessment: the lookup, not a number

Leron, 2026-08-22: *"There is no value assessment on the cards."*

Fair — a verdict with no number attached is half an answer. This still refuses to **invent** a number. What it does instead is aim the lookup precisely: every card alert and every `flipscout card` run now links the eBay **SOLD** search for *that exact card*, built from the identity the title states.

```
2018 panini prizm luka doncic silver prizm rc auto /99
```

Not `pokemon card` (returns the category, worthless) and not the raw title (returns nothing, because sellers stuff it with hype no other listing shares). Both failure modes are silent — a link with zero results looks exactly like one that works — so hype is stripped, the query is capped at 12 words, and **the grade is preserved wherever it sat**, because it is the biggest price variable a title carries and it usually sits past the cap.

That is the same promise every priced alert makes — *check the claim in one click* — minus the claim we have not earned.

## Getting real ceilings on sports cards

Leron, 2026-08-22: *"I'm looking for deals. I want value."*

Graded **Pokémon** cards produce that today — real comp, real ceiling, real
profit — and they only started to as of the guard fix above. Worked example:

```
1999 Pokemon Base Set Charizard Holo PSA 9 Trading Card   (currently $18)
  comp $112.50 (n=111, measured 2026-08-20)
  at $18.00 -> clears $64.19        MAX BID $63.19
```

**Sports cards need one browser paste per tier.** Comps here are measured by
hand on purpose (`ebay_ui`), so five tiers are defined and waiting:

```
flipscout cardcomp                       # the list
flipscout cardcomp sports_sealed_box     # prints the search + the paste script
flipscout cardcomp sports_sealed_box --from comps.json --today 2026-08-22
```

The last step prints a finished `Model(...)` to drop into `pricebook.MODELS`.
The tiers carry the *matching* half of a model — the regex that says "this
listing is one of these" — and **no number at all**, because the book's one law
is that a model ships with a measured comp.

| tier | why |
|---|---|
| `sports_sealed_box` | 🚨 **do this one first** — the only card product with no condition variable |
| `sports_rpa` | rookie + patch + auto: the top of the hobby, all three stated in the title |
| `sports_rookie_auto` | an auto can't be in every pack; paired with the year the hobby pays for |
| `sports_low_numbered` | under /25 the print run itself carries the price |
| `sports_graded_rookie` | the shape that already works for Pokémon — the grade *is* the comp |

It quotes the **p25 floor**, never the median: every card population carries a
cheaper cohort the title cannot separate out, which is why all three Pokémon
tiers are pinned at p25 too. And it **refuses to emit a model** whose ceiling
comes out at $0.00, telling you to record it in `DEAD_MODELS` instead — the
honest outcome for most of this category.

## 🚨 Why this can't be automated



No sports card carries a ceiling, because none has been measured. Comps here
are pulled **by hand through a real browser** (see `ebay_ui` — eBay WAF-blocks
scripts, and the sold-data API is closed to new users), so measuring is a
session of Leron's time per tier, not something the tool can do for itself.

**Sealed wax is the place to spend it.** A sealed box is the one card product a
title fully identifies, with no condition variable at all — the only card
category that could ever carry a stable measured comp. Search terms for it are
already in, so there is a population to measure when that happens.

## Feeding it more

The rules above are regexes in one file with a test each. New brands, new
parallel names, new players are one line plus one test. What it still cannot
see, and what the photos are for:

- **Condition on a raw card** — the biggest variable in the category, and a
  title never states it. This is why a grade scores so heavily: it is the only
  time condition is in the words.
- **Centering, edges, surface** — the difference between a PSA 9 and a PSA 10,
  which is often several times the money.
- **Which player is hot this season.**
