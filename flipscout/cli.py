"""Command-line front end. Two modes:

  Single item (quick check while you're standing in the store):
    python -m flipscout.cli item "Nintendo Switch OLED" --buy 120 --sold 250 \
        --ship-cost 12 --sold-count 800 --active-count 400

  Batch (score a spreadsheet you built while sourcing):
    python -m flipscout.cli csv flipscout/sample_items.csv

Everything runs in estimate mode: you supply the eBay SOLD price (--sold), which
you get for free from eBay's "Sold items" search filter. No API key, no scraping.
"""

from __future__ import annotations

import argparse
import sys

from .analyzer import Candidate, Thresholds, analyze, analyze_csv, max_pay
from .categories import format_goldmines
from .comps import Comp, load_memory, save_comp
from .fees import CONSERVATIVE, FeeModel

DEFAULT_MEMORY = "flipscout/comps_memory.json"


def _fee_model(args) -> FeeModel:
    if getattr(args, "conservative", False):
        return CONSERVATIVE
    return FeeModel(
        final_value_pct=args.fvf,
        promoted_pct=args.promoted,
        international_pct=args.intl,
    )


def _thresholds(args) -> Thresholds:
    return Thresholds(
        min_profit=args.min_profit,
        min_roi=args.min_roi,
    )


def _print_detail(a) -> None:
    print(a.summary())
    for n in a.notes:
        print(f"             - {n}")


def _provider(args):
    """Pick a comps source: --ebay (live API) beats --memory (price book)."""
    if getattr(args, "ebay", False):
        from .ebay_api import EbayApiComps  # lazy: needs creds + requests
        return EbayApiComps()
    if getattr(args, "memory", None):
        return load_memory(args.memory)
    return None


def cmd_item(args) -> int:
    cand = Candidate(
        title=args.title,
        source_price=args.buy,
        observed_price=args.sold,
        shipping_cost=args.ship_cost,
        shipping_charged=args.ship_charge,
        extra_cost=args.extra,
        sold_count=args.sold_count,
        active_count=args.active_count,
    )
    a = analyze(cand, provider=_provider(args), fees=_fee_model(args),
                thresholds=_thresholds(args))
    _print_detail(a)
    if a.verdict.value == "NEEDS_COMP":
        return 2
    return 0


def cmd_maxpay(args) -> int:
    """The fast, in-the-aisle question: what's the most I can pay?"""
    m = max_pay(
        sale_price=args.sold,
        fees=_fee_model(args),
        thresholds=_thresholds(args),
        shipping_cost=args.ship_cost,
        shipping_charged=args.ship_charge,
        extra_cost=args.extra,
    )
    print(m.summary())
    return 0 if m.max_price > 0 else 2


def cmd_watch(args) -> int:
    """Run the watchlist once and alert on new deals (the always-on job, one shot)."""
    from .watch import load_config, run_watch
    from .notify import notify

    def dry(text):
        print(text)
        return []

    cfg = load_config()
    if args.queries:
        cfg["queries"] = args.queries
    res = run_watch(cfg, notifier=dry if args.dry else notify)
    print(f"\n{res['new']} new / {res['scanned']} scanned"
          + (f", sent via {res['sent']}" if res["sent"] else ""))
    return 0


def cmd_goldmines(args) -> int:
    """Print the starter buy-box cheat-sheet."""
    print(format_goldmines())
    return 0


def cmd_scan(args) -> int:
    """Arbitrage scan: find underpriced eBay listings for your searches."""
    from .ebay_api import EbayApiComps  # needs creds + requests
    from .scanner import scan
    from .sources import build_sources
    ebay = EbayApiComps()
    sources = build_sources(args.source.split(","), ebay)
    hits = scan(args.queries, sources, comp_source=ebay,
                fees=_fee_model(args), thresholds=_thresholds(args),
                buy_shipping=args.buy_ship, resell_shipping=args.ship_cost,
                local=args.local, zip_code=args.zip, effort_minutes=args.minutes,
                max_days=args.max_days, min_sell_through=args.min_st,
                limit_per_query=args.per_query)
    if not hits:
        where = "local-pickup listings" if args.local else "listings"
        print(f"No arbitrage found ({where} priced below sold value by your bar).")
        return 0
    kind = "local pickup" if args.local else "ships to you"
    print(f"{len(hits)} deal(s) ({kind}), best $/hour first:\n")
    for h in hits:
        print(f"[{h.source:>8}] {h.summary()}")
        if args.links and h.url:
            print(f"             {h.url}")
    return 0


def cmd_remember(args) -> int:
    """Save a comp to your personal price book so this item is instant next time."""
    save_comp(args.memory, Comp(
        query=args.title,
        sold_price=args.sold,
        sold_count=args.sold_count,
        active_count=args.active_count,
        source="memory",
    ))
    print(f"Saved '{args.title}' @ ${args.sold:.2f} to {args.memory}")
    return 0


def cmd_hunt(args) -> int:
    """Sweep every headless source, price what matches the book, alert with bids."""
    from .hunt import load_config, run, sweep, evaluate, to_alert

    cfg = load_config()
    if args.sources:
        cfg["sources"] = [s.strip() for s in args.sources.split(",") if s.strip()]
    if args.target is not None:
        cfg["target_profit"] = args.target

    if args.dry:
        rows = sweep(cfg)
        cands = evaluate(rows, cfg)
        print(f"{len(rows)} listings swept; {len(cands)} priceable\n")
        for c in cands:
            a, r = c["advice"], c["row"]
            print(f"[{r['source']:8s}] {c['model'].label:<22} {a.summary()}")
            print(f"           {r['title'][:72]}")
            print(f"           {r.get('url','')}")
        return 0

    res = run(cfg)
    print(res)
    return 0


def cmd_comp(args) -> int:
    """Real eBay SOLD comps via your own browser (no dev key — ours was rejected).

    Two steps, because eBay serves the real page only to a genuine top-level
    navigation: it WAF-challenges `requests` and even in-page fetch(). See
    flipscout/ebay_ui.py for the measurements behind that.
    """
    from .ebay_ui import EXTRACT_JS, build_report, load_raw, sold_url

    if not args.source:
        print(f"1. Open this in your normal Chrome (logged in is fine, not required):\n")
        print(f"   {sold_url(args.query)}\n")
        print("2. Open DevTools (F12) -> Console, paste this, hit Enter.")
        print("   It copies the results to your clipboard:")
        print("   " + "-" * 56)
        for line in EXTRACT_JS.strip().splitlines():
            print("   " + line)
        print("   " + "-" * 56)
        print("\n3. Save the clipboard to a file and run:")
        print(f'     flipscout comp "{args.query}" --from comps.json')
        print("   (or pipe it:  clip contents | flipscout comp \"...\" --from -)")
        return 0

    if args.source == "-":
        text = sys.stdin.read()
    else:
        with open(args.source, encoding="utf-8") as f:
            text = f.read()

    try:
        raw = load_raw(text)
    except ValueError as e:
        print(f"error: couldn't parse that as JSON ({e}). Re-copy the console output.",
              file=sys.stderr)
        return 1

    report = build_report(args.query, raw, fees=_fee_model(args),
                          resell_shipping=args.ship_cost, require_sold=True)
    print(report.render(target_profit=args.target))

    if args.remember and report.headline:
        save_comp(args.memory, Comp(
            query=args.query, sold_price=report.headline,
            sold_count=len(report.clean), source="ebay_ui",
        ))
        print(f"\nsaved to price book: {args.memory}")
    return 0


def cmd_csv(args) -> int:
    results = analyze_csv(args.path, provider=_provider(args),
                          fees=_fee_model(args), thresholds=_thresholds(args))
    if not results:
        print("No candidates found in CSV.")
        return 1
    buys = sum(1 for r in results if r.verdict.value == "BUY")
    print(f"Scored {len(results)} candidates  |  {buys} BUY\n")
    for a in results:
        _print_detail(a)
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="flipscout",
        description="eBay sourcing profit analyzer (estimate mode, ToS-safe).",
    )
    # Shared knobs.
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--fvf", type=float, default=0.1325,
                        help="eBay final value fee fraction (default 0.1325)")
    common.add_argument("--promoted", type=float, default=0.0,
                        help="Promoted Listings ad rate fraction (default 0)")
    common.add_argument("--intl", type=float, default=0.0,
                        help="international fee fraction (default 0)")
    common.add_argument("--conservative", action="store_true",
                        help="use the stress-case fee model")
    common.add_argument("--min-profit", type=float, default=10.0,
                        help="minimum net profit to call it a BUY (default 10)")
    common.add_argument("--min-roi", type=float, default=0.50,
                        help="minimum ROI to call it a BUY (default 0.50)")

    sub = p.add_subparsers(dest="cmd", required=True)

    pi = sub.add_parser("item", parents=[common], help="analyze one item")
    pi.add_argument("title")
    pi.add_argument("--buy", type=float, required=True, help="local/source price you'd pay")
    pi.add_argument("--sold", type=float, default=None,
                    help="median eBay SOLD price (from the Sold items filter)")
    pi.add_argument("--ship-cost", type=float, default=0.0, help="postage YOU pay")
    pi.add_argument("--ship-charge", type=float, default=0.0, help="postage buyer pays you")
    pi.add_argument("--extra", type=float, default=0.0, help="supplies/refurb/gas per item")
    pi.add_argument("--sold-count", type=int, default=None, help="# sold in lookback")
    pi.add_argument("--active-count", type=int, default=None, help="# active listings")
    pi.add_argument("--memory", nargs="?", const=DEFAULT_MEMORY, default=None,
                    help="price-book file to auto-fill sold prices (default "
                         f"{DEFAULT_MEMORY} when flag given bare)")
    pi.add_argument("--ebay", action="store_true",
                    help="fetch sold price + counts live from the eBay API "
                         "(needs EBAY_CLIENT_ID / EBAY_CLIENT_SECRET)")
    pi.set_defaults(func=cmd_item)

    pm = sub.add_parser("maxpay", parents=[common],
                        help="highest price to pay for a given eBay sold price")
    pm.add_argument("--sold", type=float, required=True, help="median eBay SOLD price")
    pm.add_argument("--ship-cost", type=float, default=0.0, help="postage YOU pay")
    pm.add_argument("--ship-charge", type=float, default=0.0, help="postage buyer pays you")
    pm.add_argument("--extra", type=float, default=0.0, help="supplies/refurb/gas per item")
    pm.set_defaults(func=cmd_maxpay)

    sub.add_parser("goldmines", help="print the goldmine-category buy-box cheat-sheet") \
       .set_defaults(func=cmd_goldmines)

    pw = sub.add_parser("watch", help="run your watchlist once and alert on new deals")
    pw.add_argument("queries", nargs="*", help="override the FLIPSCOUT_WATCHLIST searches")
    pw.add_argument("--dry", action="store_true", help="print the digest, don't send alerts")
    pw.set_defaults(func=cmd_watch)

    ps = sub.add_parser("scan", parents=[common],
                        help="arbitrage scan: find underpriced eBay listings (needs eBay keys)")
    ps.add_argument("queries", nargs="+", help="one or more searches, e.g. \"dewalt drill\"")
    ps.add_argument("--source", default="ebay",
                    help="comma list of buying sources: ebay, goodwill (default ebay)")
    ps.add_argument("--local", action="store_true", help="only local-pickup listings you can go grab")
    ps.add_argument("--zip", default=None, help="your ZIP, to center local results (with --local)")
    ps.add_argument("--minutes", type=int, default=None, help="handling time/flip for the $/hr rank")
    ps.add_argument("--max-days", type=float, default=None,
                    help="drop items estimated to take longer than N days to sell")
    ps.add_argument("--min-st", type=float, default=None,
                    help="drop items below this sell-through, e.g. 0.4")
    ps.add_argument("--ship-cost", type=float, default=0.0, help="postage you'd pay to reship")
    ps.add_argument("--buy-ship", type=float, default=0.0, help="postage added when you buy")
    ps.add_argument("--per-query", type=int, default=None, help="cap hits shown per search")
    ps.add_argument("--links", action="store_true", help="print the listing URL for each hit")
    ps.set_defaults(func=cmd_scan)

    pcomp = sub.add_parser(
        "comp", parents=[common],
        help="real eBay SOLD comps via your browser, segmented by condition")
    pcomp.add_argument("query", help='what to comp, e.g. "donkey kong 64 nintendo 64"')
    pcomp.add_argument("--from", dest="source", default=None,
                       help="file with the pasted console JSON, or - for stdin. "
                            "Omit to print the URL + console snippet first.")
    pcomp.add_argument("--target", type=float, default=20.0,
                       help="profit you want to clear, for the max-pay line (default 20)")
    pcomp.add_argument("--ship-cost", type=float, default=0.0,
                       help="postage YOU would pay to ship it to the buyer")
    pcomp.add_argument("--remember", action="store_true",
                       help="save the headline comp to your price book")
    pcomp.add_argument("--memory", default=DEFAULT_MEMORY, help="price-book file")
    pcomp.set_defaults(func=cmd_comp)

    ph = sub.add_parser("hunt",
        help="sweep every source for priceable deals and alert with open/max bids")
    ph.add_argument("--sources", default=None, help="goodwill,hibid (default both)")
    ph.add_argument("--target", type=float, default=None, help="profit per flip (default 20)")
    ph.add_argument("--dry", action="store_true", help="print candidates, don't alert")
    ph.set_defaults(func=cmd_hunt)

    pr = sub.add_parser("remember", help="save a comp to your price book")
    pr.add_argument("title")
    pr.add_argument("--sold", type=float, required=True, help="median eBay SOLD price")
    pr.add_argument("--sold-count", type=int, default=None)
    pr.add_argument("--active-count", type=int, default=None)
    pr.add_argument("--memory", default=DEFAULT_MEMORY, help="price-book file")
    pr.set_defaults(func=cmd_remember)

    pc = sub.add_parser("csv", parents=[common], help="analyze a CSV of candidates")
    pc.add_argument("path")
    pc.add_argument("--memory", nargs="?", const=DEFAULT_MEMORY, default=None,
                    help="price-book file to auto-fill missing sold prices")
    pc.add_argument("--ebay", action="store_true",
                    help="fetch sold prices live from the eBay API")
    pc.set_defaults(func=cmd_csv)

    return p


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except RuntimeError as e:  # e.g. missing eBay credentials
        print(f"error: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
