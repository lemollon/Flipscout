"""Real eBay SOLD comps from the public site - no developer key, no scraping bot.

Why this module exists
----------------------
The eBay developer account was rejected, so Browse/Marketplace-Insights are closed
to us (see ebay_api.py). The public site still shows everything we need. But it is
not fetchable by a script:

  * `requests` + browser headers  -> WAF challenge after ~1 response.
  * `fetch()`/XHR from inside an already-loaded eBay tab -> also challenged; eBay
    fingerprints `Sec-Fetch-Mode: navigate` vs `cors`.
  * A real top-level browser navigation -> full page, ~1.8MB, every sold card.

So the transport is your own browser, and this module is the brain: you navigate
(one click), paste `EXTRACT_JS` into DevTools, and paste the JSON back. Zero new
dependencies, nothing to keep logged in, and no automation to rot when eBay
reshuffles its markup.

Why not just take the median
----------------------------
Because the median lies, and it lies expensively. Measured 2026-07-24 on
"donkey kong 64 nintendo 64":

    overall median $29.99   <- what a naive tool reports
    loose cart     $28.16   (n=8)
    complete/CIB  $199.00   (n=4)     <- 7x the loose price
    full range    $2.83 - $1500.00

and every one of the cheapest Buy-It-Now listings was a Japanese region-locked
import - cheap for a reason, not mispriced. A single number across that spread is
worse than no number, so `summarize()` segments by condition and flags the
contaminants (imports, repros, parts, lots, graded slabs, rare variants) instead
of averaging them together.

Usage:
    flipscout comp "donkey kong 64 nintendo 64"
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from statistics import median
from typing import Iterable, Optional
from urllib.parse import quote_plus

from .fees import FeeModel, net_proceeds

EBAY = "https://www.ebay.com/sch/i.html"

# Below roughly this resale price, eBay's fixed costs (13.25% + $0.40 + postage)
# eat the whole trade no matter how cheaply you buy. Measured, not guessed: a $6
# sale nets -$0.20. See the 2026-07-24 findings in the module docstring.
STRUCTURAL_FLOOR = 25.0


def sold_url(query: str, ipg: int = 60) -> str:
    """eBay search restricted to SOLD + COMPLETED - the only prices that are real."""
    return f"{EBAY}?_nkw={quote_plus(query)}&LH_Sold=1&LH_Complete=1&_ipg={ipg}"


def active_url(query: str, ipg: int = 60, cheapest_first: bool = True) -> str:
    """Active Buy-It-Now listings; `cheapest_first` sorts by price + shipping."""
    sop = "&_sop=15" if cheapest_first else ""
    return f"{EBAY}?_nkw={quote_plus(query)}&LH_BIN=1{sop}&_ipg={ipg}"


# --- what you paste into the browser console -------------------------------
# Kept deliberately dumb: pull text, hand judgement to Python where it's testable.
EXTRACT_JS = r"""
copy(JSON.stringify([...document.querySelectorAll('li.s-item, li.s-card')].map(c => {
  const txt = c.innerText || '';
  const price = (txt.match(/\$([0-9][0-9,]*\.\d{2})/) || [])[1];
  if (!price) return null;
  const JUNK = /^sold\b|^opens in|^new listing$|out of 5 stars|^shop on ebay|^or best offer|^sponsored|^was:|^free (shipping|delivery)|^brand new|^pre-owned$/i;
  const title = (txt.split('\n').map(s => s.trim())
      .filter(s => s.length > 12 && !JUNK.test(s))[0] || '')
      .replace(/^new listing/i, '').trim();
  return {
    title,
    price: +price.replace(/,/g, ''),
    sold: (txt.match(/Sold\s+(\w{3}\s+\d{1,2},\s*\d{4})/) || [])[1] || null,
    shipping: /Free (shipping|delivery)/i.test(txt) ? 0
            : +((txt.match(/\+\$([0-9.]+)\s*(shipping|delivery)/) || [])[1] || 0)
  };
}).filter(Boolean)))
"""


# --- condition & contamination ---------------------------------------------

CONDITION_PATTERNS = [
    ("graded", r"\b(wata|vga|psa\s*\d|cgc|bgs|graded|slab)\b"),
    ("sealed", r"\b(factory sealed|still sealed|sealed|brand new in box|new sealed|nib)\b"),
    ("cib",    r"\b(cib|complete in box|complete w|complete with|w/ manual|with manual|"
               r"complete|boxed|in original box|with box|w/ box)\b"),
    # `cib`/`sealed`/`graded` are matched first, so a bare "cartridge"/"disc"
    # mention here is safe to read as loose (a boxed copy would have matched above).
    ("loose",  r"\b(cart(ridge)? only|disc only|game only|loose|no manual|no box|"
               r"cartridge|unboxed)\b"),
]

# Each of these silently wrecked a comp in live testing on 2026-07-24.
CONTAMINANTS = [
    ("import",  r"\b(jpn|japan|japanese|ntsc-?j|pal\b|region\s*lock|import|europe|uk version)\b",
                "region-locked import - cheap because it won't play on US hardware"),
    ("repro",   r"\b(repro|reproduction|custom|fan\s*made|aftermarket|bootleg|not authentic)\b",
                "reproduction/bootleg - not the real thing"),
    ("parts",   r"\b(for parts|parts only|not working|as-?is|untested|broken|damaged|repair)\b",
                "sold as parts/untested - not a working-item comp"),
    # NOTE: deliberately NOT `\d+\s*(games|cards)` - that misfired on "Donkey Kong
    # 64 Games For Nintendo N64", where the 64 belongs to the product's own name.
    ("lot",     r"\b(lot of|\blot\b|bundle|set of \d+|\d+\s*(?:game|card|book|comic)s"
                r"\s+(?:lot|bundle|set))\b",
                "multi-item lot - price covers several things, not one"),
    ("variant", r"\[.*?\]|\b(not for resale|player'?s choice|greatest hits|black label|"
                r"limited edition|collector'?s edition)\b",
                "special variant - priced differently from the standard item"),
]


def classify(title: str) -> str:
    """Condition bucket for one listing title. Order matters: graded beats sealed
    beats complete beats loose, because titles stack adjectives."""
    t = (title or "").lower()
    for name, pat in CONDITION_PATTERNS:
        if re.search(pat, t):
            return name
    return "unknown"


def contaminants(title: str) -> list[str]:
    """Which comp-wrecking flags this title trips (possibly none)."""
    t = (title or "").lower()
    return [name for name, pat, _ in CONTAMINANTS if re.search(pat, t)]


CONTAMINANT_HELP = {name: help_ for name, _, help_ in CONTAMINANTS}


@dataclass(frozen=True)
class SoldRow:
    title: str
    price: float
    sold: Optional[str] = None
    shipping: float = 0.0
    condition: str = "unknown"
    flags: tuple = ()

    @property
    def all_in(self) -> float:
        """What a buyer actually paid - eBay's headline price hides shipping."""
        return round(self.price + (self.shipping or 0.0), 2)


# eBay salts the result grid with promo/filler cards. Live output on 2026-07-24
# produced rows titled exactly "or Best Offer" with no sold date - they'd drag a
# median down if counted as sales.
JUNK_TITLE = re.compile(
    r"^(or best offer|shop on ebay|sponsored|new listing|results matching|"
    r"check each listing|see similar items)\b|^\s*$", re.I)


def parse_rows(raw: Iterable[dict], require_sold: bool = False) -> list[SoldRow]:
    """Turn the pasted JSON into classified rows, dropping junk entries.

    `require_sold=True` (used for sold-comp lookups) additionally drops any row
    without a sold date - on a SOLD search, a row with no date is not a sale.
    """
    out: list[SoldRow] = []
    for r in raw or []:
        try:
            price = float(r.get("price"))
        except (TypeError, ValueError):
            continue
        if price <= 0:
            continue
        title = re.sub(r"^new listing", "", (r.get("title") or "").strip(), flags=re.I).strip()
        if JUNK_TITLE.match(title) or len(title) < 8:
            continue
        if require_sold and not r.get("sold"):
            continue
        try:
            ship = float(r.get("shipping") or 0.0)
        except (TypeError, ValueError):
            ship = 0.0
        out.append(SoldRow(title=title, price=price, sold=r.get("sold"),
                           shipping=ship, condition=classify(title),
                           flags=tuple(contaminants(title))))
    return out


def _stats(prices: list[float]) -> dict:
    if not prices:
        return {"n": 0, "median": None, "low": None, "high": None}
    s = sorted(prices)
    return {"n": len(s), "median": round(median(s), 2),
            "low": round(s[0], 2), "high": round(s[-1], 2)}


@dataclass
class CompReport:
    query: str
    rows: list[SoldRow] = field(default_factory=list)
    fees: FeeModel = field(default_factory=FeeModel)
    resell_shipping: float = 0.0

    # -- the numbers -------------------------------------------------------
    @property
    def clean(self) -> list[SoldRow]:
        """Rows with no contamination flags - the only ones worth pricing off."""
        return [r for r in self.rows if not r.flags]

    @property
    def headline(self) -> Optional[float]:
        """Median of clean rows. Falls back to all rows only if everything is
        flagged (and `warnings` will say so)."""
        base = self.clean or self.rows
        return _stats([r.all_in for r in base])["median"]

    def by_condition(self) -> dict:
        out = {}
        for r in self.clean:
            out.setdefault(r.condition, []).append(r.all_in)
        return {k: _stats(v) for k, v in sorted(out.items(), key=lambda kv: -len(kv[1]))}

    def flag_counts(self) -> dict:
        counts: dict = {}
        for r in self.rows:
            for f in r.flags:
                counts[f] = counts.get(f, 0) + 1
        return dict(sorted(counts.items(), key=lambda kv: -kv[1]))

    def max_pay(self, sale_price: Optional[float] = None,
                target_profit: float = 20.0) -> Optional[float]:
        """Most you can pay and still clear `target_profit`, after eBay's cut and
        your postage. This is the number that decides the buy."""
        sale = sale_price if sale_price is not None else self.headline
        if not sale:
            return None
        net = net_proceeds(sale, fees=self.fees, shipping_cost=self.resell_shipping).net
        return round(net - target_profit, 2)

    def warnings(self) -> list[str]:
        w: list[str] = []
        if not self.rows:
            return ["no sold listings parsed - check the paste, or the search has no solds"]
        if not self.clean:
            w.append("EVERY sold listing tripped a flag - treat the headline as unusable")
        elif len(self.clean) < 5:
            w.append(f"only {len(self.clean)} clean comps - thin sample, low confidence")

        conds = self.by_condition()
        priced = {k: v for k, v in conds.items() if v["median"]}
        if len(priced) > 1:
            lo = min(priced.values(), key=lambda v: v["median"])
            hi = max(priced.values(), key=lambda v: v["median"])
            if lo["median"] and hi["median"] / lo["median"] >= 2:
                w.append(
                    f"condition spread is {hi['median']/lo['median']:.1f}x "
                    f"(${lo['median']:.2f} to ${hi['median']:.2f}) - the overall median is "
                    f"meaningless here; price the CONDITION you're actually buying")

        for f, n in self.flag_counts().items():
            share = n / len(self.rows)
            if share >= 0.2:
                w.append(f"{n}/{len(self.rows)} ({share:.0%}) look like {f}: {CONTAMINANT_HELP[f]}")

        h = self.headline
        if h is not None and h < STRUCTURAL_FLOOR:
            net = net_proceeds(h, fees=self.fees, shipping_cost=self.resell_shipping).net
            w.append(
                f"BELOW THE FLOOR: at ${h:.2f} a sale nets ${net:.2f} after fees+postage. "
                f"Items under ~${STRUCTURAL_FLOOR:.0f} can't profit no matter how cheap the buy.")
        return w

    # -- output ------------------------------------------------------------
    def render(self, target_profit: float = 20.0) -> str:
        L = [f"eBay SOLD comps - {self.query}", "=" * 58]
        if not self.rows:
            L.append("no rows parsed.")
            for w in self.warnings():
                L.append(f"  ! {w}")
            return "\n".join(L)

        allst = _stats([r.all_in for r in self.rows])
        L.append(f"{allst['n']} sold listings  |  range ${allst['low']:.2f} - ${allst['high']:.2f}"
                 f"  |  {len(self.clean)} clean / {len(self.rows) - len(self.clean)} flagged")
        if self.headline:
            L.append(f"HEADLINE (clean median, incl. shipping): ${self.headline:.2f}")
        L.append("")
        L.append("by condition (clean rows only):")
        conds = self.by_condition()
        if not conds:
            L.append("  - nothing clean to segment")
        for name, s in conds.items():
            if s["median"] is None:
                continue
            L.append(f"  {name:<8} n={s['n']:<3} median ${s['median']:>8.2f}"
                     f"   range ${s['low']:.2f} - ${s['high']:.2f}")

        flags = self.flag_counts()
        if flags:
            L.append("")
            L.append("excluded as contaminated:")
            for f, n in flags.items():
                L.append(f"  {f:<8} {n:>3}  ({CONTAMINANT_HELP[f]})")

        L.append("")
        L.append(f"max you can pay (to clear ${target_profit:.0f} after "
                 f"{self.fees.final_value_pct:.2%} FVF + ${self.resell_shipping:.2f} postage):")
        for name, s in conds.items():
            if s["median"] is None:
                continue
            mp = self.max_pay(s["median"], target_profit)
            verdict = f"${mp:,.2f}" if mp and mp > 0 else "IMPOSSIBLE - fees exceed the spread"
            L.append(f"  if it's {name:<8} (sells ${s['median']:>8.2f})  ->  pay at most {verdict}")

        w = self.warnings()
        if w:
            L.append("")
            L.append("warnings:")
            for x in w:
                L.append(f"  ! {x}")
        return "\n".join(L)


def build_report(query: str, raw: Iterable[dict], fees: Optional[FeeModel] = None,
                 resell_shipping: float = 0.0, require_sold: bool = False) -> CompReport:
    return CompReport(query=query, rows=parse_rows(raw, require_sold=require_sold),
                      fees=fees or FeeModel(), resell_shipping=resell_shipping)


def load_raw(text: str) -> list[dict]:
    """Accept the pasted clipboard JSON (array, or {rows:[...]}), tolerating
    stray whitespace/quotes from a terminal paste."""
    text = (text or "").strip()
    if not text:
        return []
    if text[0] in "\"'" and text[-1] == text[0]:
        text = text[1:-1]
    data = json.loads(text)
    if isinstance(data, dict):
        data = data.get("rows") or data.get("items") or []
    return data if isinstance(data, list) else []
