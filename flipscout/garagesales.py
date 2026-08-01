"""Garage/yard sales near home - a weekly "go look at these" digest.

Same philosophy as estates.py: these carry NO item prices, so this is
deliberately NOT a hunter and never emits a max bid. It answers "which sales
are worth a Saturday-morning drive", and the price book rides along as the
shopping list (cameras, calculators, Fluke meters, Featherweights).

Sources (all render schema.org microdata server-side; if the itemprop
attributes vanish, sales() returns [] and the digest just doesn't post,
fail-soft like every other source):

- yardsalesearch.com - probed 2026-07-30, takes a zip directly.
- gsalr.com - the 7/30 "404s guessable paths" verdict was wrong URL guessing;
  the real pattern is /garage-sales-<city>-<st>.html (city list in
  sitemap.xml?s=TX). No zip endpoint, so cities come from config. List pages
  carry full descriptions, which is where the item keywords live.
- garagesalefinder.com - /yard-sales/<zip>, same EstateSales.NET family as
  gsalr (its photos are served from gsalr.tlstatic.com), so the same sale
  often appears on both. merged_sales() dedupes across all three.

Craigslist's garage-sale category (gms) stays dead: it returns an EMPTY
ld+json ItemList (no-price posts are excluded from it).

Because gsalr/GSF descriptions name actual items, hot() checks each sale
against the price book (exact model mention) and a coarse category-word list;
the digest floats those to the top with the reason. Still never a max bid -
a mention is a reason to drive, not a price.

Pickup-only by nature, which is the point (Leron, 2026-07-30: no longer
trusting anybody to ship).
"""

from __future__ import annotations

import datetime as _dt
import html as _html
import re
from typing import Optional

import requests

_TIMEOUT = 30
_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")
_URL = "https://www.yardsalesearch.com/garage-sales.html?zip={}"

# One sale card starts at its itemprop="name" heading; parse each chunk
# independently so one malformed card can't take down the sweep.
_CARD_SPLIT = re.compile(r'<h2 itemprop="name">')
_FIELD = {
    "url": re.compile(r'<a itemprop="url" href="([^"]+)"'),
    "title": re.compile(r'<a itemprop="url" href="[^"]+">([^<]+)</a>'),
    "street": re.compile(r'itemprop="streetAddress">([^<]+)<'),
    "city": re.compile(r'itemprop="addressLocality">([^<]+)<'),
    "start": re.compile(r'itemprop="startDate" content="([^"]+)"'),
    "end": re.compile(r'itemprop="endDate" content="([^"]+)"'),
    "desc": re.compile(r'itemprop="description"[^>]*>\s*(.*?)\s*</', re.S),
}


class YardSaleSearch:
    name = "garagesales"

    def __init__(self, zip_code: str, session: Optional[requests.Session] = None):
        self.zip = zip_code
        self.session = session or requests.Session()

    def fetch(self) -> str:
        r = self.session.get(_URL.format(self.zip),
                             headers={"User-Agent": _UA}, timeout=_TIMEOUT)
        r.raise_for_status()
        return r.text

    def sales(self, html: Optional[str] = None,
              today: Optional[_dt.date] = None) -> list[dict]:
        """Upcoming/active sales, soonest first. [] on any failure."""
        try:
            html = html if html is not None else self.fetch()
        except Exception:
            return []
        today = today or _dt.date.today()
        out = []
        for chunk in _CARD_SPLIT.split(html)[1:]:
            chunk = chunk[:4000]         # a card is ~2KB; don't scan the page tail
            row = {k: (_html.unescape(p.search(chunk).group(1)).strip()
                       if p.search(chunk) else "") for k, p in _FIELD.items()}
            row["desc"] = _clean_desc(row.get("desc", ""))
            row["source"] = self.name
            if not row["title"] or not row["url"]:
                continue
            try:
                if _dt.date.fromisoformat(row["end"]) < today:
                    continue             # already over
            except ValueError:
                pass                     # undated sales stay in - go look
            out.append(row)
        out.sort(key=lambda r: r.get("start") or "9999")
        return out


def _clean_desc(s: str) -> str:
    """Strip markup and list-page chrome ("Read More →") out of a description."""
    s = re.sub(r"<[^>]+>", " ", s)
    s = re.sub(r"(read\s*more\s*→?|…)\s*$", "", s.strip(), flags=re.I)
    return re.sub(r"\s+", " ", s).strip()[:500]


class Gsalr:
    """gsalr.com city page. One Gsalr per city slug ("katy-tx")."""

    name = "gsalr"
    _URL = "https://gsalr.com/garage-sales-{}.html"
    _CARD = re.compile(r'itemscope itemtype="https?://schema\.org/Event"')
    _F = {
        "url": re.compile(r'<a href="(https://gsalr\.com/[^"]+)"[^>]*itemprop="url"'),
        "title": re.compile(r'itemprop="url"[^>]*>([^<]+)</a>'),
        "street": re.compile(r'itemprop="streetAddress">([^<]+)<'),
        "city": re.compile(r'itemprop="addressLocality">([^<]+)<'),
        "start": re.compile(r'itemprop="startDate" content="(\d{4}-\d\d-\d\d)'),
        "end": re.compile(r'itemprop="endDate" content="(\d{4}-\d\d-\d\d)'),
        "desc": re.compile(r'itemprop="description"[^>]*>\s*(.*?)\s*</', re.S),
    }

    def __init__(self, city_slug: str, session: Optional[requests.Session] = None):
        self.city_slug = city_slug
        self.session = session or requests.Session()

    def fetch(self) -> str:
        r = self.session.get(self._URL.format(self.city_slug),
                             headers={"User-Agent": _UA}, timeout=_TIMEOUT)
        r.raise_for_status()
        return r.text

    def sales(self, html: Optional[str] = None,
              today: Optional[_dt.date] = None) -> list[dict]:
        return _parse_cards(self, html, today)


class GarageSaleFinder:
    """garagesalefinder.com - takes the zip directly."""

    name = "garagesalefinder"
    _URL = "https://garagesalefinder.com/yard-sales/{}"
    _CARD = re.compile(r'class="row collapse record"')
    _F = {
        "url": re.compile(r'<a class="sale-url" href="([^"]+)"'),
        "title": re.compile(r'<a class="sale-url" href="[^"]+"[^>]*>([^<]+)</a>'),
        # One flat string: "19222 TX-249, Houston, TX 77070". Kept whole in
        # street; city stays "" rather than being guessed out of it.
        "street": re.compile(r'itemprop="address"[^>]*>([^<]+)<'),
        "start": re.compile(r'itemprop="startDate" content="(\d{4}-\d\d-\d\d)'),
        "end": re.compile(r'itemprop="endDate" content="(\d{4}-\d\d-\d\d)'),
        "desc": re.compile(r'itemprop="description"[^>]*>\s*(.*?)\s*</', re.S),
    }

    def __init__(self, zip_code: str, session: Optional[requests.Session] = None):
        self.zip = zip_code
        self.session = session or requests.Session()

    def fetch(self) -> str:
        r = self.session.get(self._URL.format(self.zip),
                             headers={"User-Agent": _UA}, timeout=_TIMEOUT)
        r.raise_for_status()
        return r.text

    def sales(self, html: Optional[str] = None,
              today: Optional[_dt.date] = None) -> list[dict]:
        return _parse_cards(self, html, today)


def _parse_cards(src, html: Optional[str], today: Optional[_dt.date]) -> list[dict]:
    """Shared card walk for the two microdata list pages. [] on any failure."""
    try:
        html = html if html is not None else src.fetch()
    except Exception:
        return []
    today = today or _dt.date.today()
    out = []
    for chunk in src._CARD.split(html)[1:]:
        chunk = chunk[:6000]
        row = {k: (_html.unescape(p.search(chunk).group(1)).strip()
                   if p.search(chunk) else "") for k, p in src._F.items()}
        row.setdefault("street", ""); row.setdefault("city", "")
        row["desc"] = _clean_desc(row.get("desc", ""))
        row["source"] = src.name
        if not row["title"] or not row["url"]:
            continue
        try:
            if _dt.date.fromisoformat(row["end"]) < today:
                continue
        except ValueError:
            pass                     # undated sales stay in - go look
        out.append(row)
    return out


def _dedupe_key(row: dict) -> str:
    """gsalr and garagesalefinder share a backend, so the same sale shows up
    on both with the same title. A distinctive title alone is the key; short
    generic ones ("Garage Sale") get city+start appended so two different
    Saturday sales don't merge."""
    t = re.sub(r"[^a-z0-9]", "", row.get("title", "").lower())
    if len(t) >= 12:
        return t
    return t + re.sub(r"[^a-z0-9]", "", (row.get("city", "") + row.get("start", "")).lower())


def merged_sales(feeds: list) -> list[dict]:
    """All feeds, deduped, soonest first. A row with a street beats its twin
    without one (gsalr hides addresses until close to the date)."""
    best: dict[str, dict] = {}
    for f in feeds:
        for row in f.sales():
            row.setdefault("desc", ""); row.setdefault("source", getattr(f, "name", "?"))
            k = _dedupe_key(row)
            if k not in best or (row.get("street") and not best[k].get("street")):
                best[k] = row
    out = list(best.values())
    out.sort(key=lambda r: r.get("start") or "9999")
    return out


# Coarse category nouns from the book's territory. A hit means "somebody with
# this kind of stuff" - worth the drive even without an exact model name.
_HOT = re.compile(
    r"\b(cameras?|lenses|film camera|computers?|laptops?|electronics|"
    r"power tools?|sewing machines?|calculators?|typewriters?|"
    r"video ?games?|nintendo|playstation|game ?cube|"
    r"multimeters?|micrometers?|calipers?|machinist|test equipment|"
    r"vinyl records?|record collection)\b", re.I)


def hot(row: dict) -> str:
    """Why this sale outranks the rest: an exact book model beats a category
    word beats nothing. Returns '' for a plain sale."""
    text = f"{row.get('title', '')} {row.get('desc', '')}"[:600]
    try:
        from .pricebook import match
        m = match(text)
        if m:
            return m.model.label
    except Exception:
        pass
    kw = _HOT.search(text)
    return kw.group(1).lower() if kw else ""


def expand_desc(row: dict, session: Optional[requests.Session] = None) -> None:
    """Fetch the sale's own page for the full description. All three sites
    mark it itemprop="description" there too. Fail-soft: the list-page desc
    stays if anything goes wrong. Only called for sales the digest will
    actually show, so this is ~a dozen fetches once a day."""
    try:
        r = (session or requests).get(row["url"], headers={"User-Agent": _UA},
                                      timeout=_TIMEOUT)
        m = re.search(r'itemprop="description"[^>]*>\s*(.*?)\s*</(div|span|p)>',
                      r.text, re.S)
        full = _clean_desc(_html.unescape(m.group(1))) if m else ""
        if len(full) > len(row.get("desc", "")):
            row["desc"] = full
    except Exception:
        pass


def digest(sales: list[dict], zip_code: str, cap: int = 12,
           expand=None) -> str:
    """The drive-by list WITH what each sale says it has - the title alone
    ("Garage Sale Friday") tells you nothing worth driving for. `expand` is
    called on each shown row to pull the full description (expand_desc live,
    None in tests)."""
    if not sales:
        return ""
    flagged = [(hot(s), s) for s in sales]
    # Hot sales first (model hits before keyword hits is free: model labels
    # come from the book, keywords are lowercase - no ordering needed beyond
    # hot-before-cold), each group still soonest-first.
    flagged.sort(key=lambda p: (not p[0],))
    if expand is not None:
        for _, s in flagged[:cap]:
            expand(s)
        # A fuller description can surface a book model the truncated one hid.
        flagged = [(hot(s), s) for _, s in flagged]
        flagged.sort(key=lambda p: (not p[0],))
    n_hot = sum(1 for r, _ in flagged if r)
    lines = [f"🏡 **Garage sales near {zip_code}** - {len(sales)} upcoming"
             + (f", {n_hot} mention book territory" if n_hot else "") + ". "
             f"No listed prices, so no max bids: this is the drive-by list. "
             f"Shopping list = the book: cameras, TI-84 CEs, Fluke/Mitutoyo, "
             f"Featherweights, iPods, Gunne Sax. Cash, pickup only.", ""]
    for reason, s in flagged[:cap]:
        when = s.get("start") or "?"
        if s.get("end") and s["end"] != s.get("start"):
            when += f" → {s['end']}"
        where = ", ".join(x for x in (s.get("street"), s.get("city")) if x)
        tag = f" 🎯 {reason}" if reason else ""
        lines.append(f"• **{s['title'][:70]}**{tag} ({when})")
        lines.append(f"  {where}  <{s['url']}>")
        if s.get("desc"):
            lines.append(f"  ↳ {s['desc'][:240]}")
    if len(sales) > cap:
        lines.append(f"…and {len(sales) - cap} more.")
    return "\n".join(lines)


def split_for_discord(body: str, limit: int = 1900) -> list[str]:
    """Discord hard-rejects messages over 2000 chars and notify() truncates at
    1990 - which would silently eat the descriptions this digest now carries.
    Split on sale boundaries so each part stands alone."""
    if len(body) <= limit:
        return [body] if body else []
    parts, cur = [], ""
    for block in re.split(r"\n(?=• )", body):
        if cur and len(cur) + len(block) + 1 > limit:
            parts.append(cur)
            cur = block
        else:
            cur = f"{cur}\n{block}" if cur else block
    if cur:
        parts.append(cur)
    return parts
