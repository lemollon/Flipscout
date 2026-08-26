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
import math
import sys

from .analyzer import Candidate, Thresholds, analyze, analyze_csv, max_pay
from .cards import explain as explain_card, read as read_card
from .categories import format_goldmines
from .comps import Comp, load_memory, save_comp
from .fees import CONSERVATIVE, FeeModel
from .velocity import (
    FAST, CycleModel, Tier, VelocityThresholds, allocate, max_pay_for_velocity,
    realized_velocity, score_candidate,
)

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


def _cycle(args) -> CycleModel:
    """The dead-cash timeline the velocity math runs on."""
    if getattr(args, "fast", False):
        return FAST
    return CycleModel(
        prep_days=args.prep_days,
        ship_days=args.ship_days,
        payout_days=args.payout_days,
        default_days_to_sell=args.assume_days,
        handle_minutes=args.handle_min,
    )


def _vel_thresholds(args) -> VelocityThresholds:
    return VelocityThresholds(
        hot=args.hot,
        good=args.good,
        min_profit=args.min_profit,
        min_hourly=args.min_hourly,
    )


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
    if args.velocity is not None:
        # The same ceiling with the clock attached: a price that clears your
        # profit goal can still be a bad price if the item sits for a season.
        mv = max_pay_for_velocity(
            sale_price=args.sold, days_to_sell=args.days_to_sell,
            target_per_100_per_day=args.velocity, fees=_fee_model(args),
            thresholds=VelocityThresholds(min_profit=args.min_profit),
            shipping_cost=args.ship_cost, shipping_charged=args.ship_charge,
            extra_cost=args.extra,
        )
        print(mv.summary())
        return 0 if (m.max_price > 0 and mv.max_price > 0) else 2
    return 0 if m.max_price > 0 else 2


def cmd_velocity(args) -> int:
    """Score one item on capital velocity: profit per dollar per DAY.

    Exit codes are meant for scripting: 0 = worth the slot (HOT/GOOD),
    1 = SLOW or DEAD money, 2 = needs a sold comp first.
    """
    cand = Candidate(
        title=args.title,
        source_price=args.buy,
        observed_price=args.sold,
        shipping_cost=args.ship_cost,
        shipping_charged=args.ship_charge,
        extra_cost=args.extra,
        sold_count=args.sold_count,
        active_count=args.active_count,
        days_to_sell=args.days_to_sell,
    )
    cycle = _cycle(args)
    vt = _vel_thresholds(args)
    a = score_candidate(
        cand, provider=_provider(args), fees=_fee_model(args),
        thresholds=_thresholds(args), cycle=cycle, velocity_thresholds=vt,
    )
    print(a.detail())
    if a.tier is Tier.NEEDS_COMP:
        return 2

    # The ceiling that matters in the aisle: what could you have paid and still
    # kept this dollar working at the GOOD bar?
    ceiling = max_pay_for_velocity(
        sale_price=a.deal.sale_price, days_to_sell=a.days_to_sell,
        target_per_100_per_day=vt.good, fees=_fee_model(args), cycle=cycle,
        thresholds=vt, shipping_cost=args.ship_cost,
        shipping_charged=args.ship_charge, extra_cost=args.extra,
    )
    print("  " + ceiling.summary())
    return 0 if a.tier in (Tier.HOT, Tier.GOOD) else 1


def cmd_portfolio(args) -> int:
    """Spend a bankroll: which of these candidates actually earn their slot?"""
    from .analyzer import candidates_from_csv

    cycle = _cycle(args)
    vt = _vel_thresholds(args)
    provider = _provider(args)
    fees = _fee_model(args)
    thr = _thresholds(args)
    scored = [
        score_candidate(c, provider=provider, fees=fees, thresholds=thr,
                        cycle=cycle, velocity_thresholds=vt)
        for c in candidates_from_csv(args.path)
    ]
    if not scored:
        print(f"no candidates in {args.path}")
        return 2
    plan = allocate(scored, bankroll=args.bankroll, hours=args.hours,
                    min_tier=Tier(args.min_tier))
    print(plan.summary())
    return 0 if plan.bought else 1


def cmd_turns(args) -> int:
    """Realized velocity: what your capital ACTUALLY earned per day."""
    print(realized_velocity(stale_days=args.stale_days).report())
    return 0


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


def cmd_card(args) -> int:
    """Triage a sports-card title against the card-shop buy box.

    🚨 PRINTS A VERDICT, NEVER A PRICE - see flipscout.cards for why. This is
    the "should I even pick this one up" pass, which is the question a table of
    a thousand cards actually poses.
    """
    for title in args.titles:
        if len(args.titles) > 1:
            print(f"\n{title}")
        print(explain_card(read_card(title)))
    return 0


def cmd_cardcomp(args) -> int:
    """Measure a sports-card tier, and print the Model it becomes.

    🚨 THIS IS THE LAST MILE BETWEEN A VERDICT AND A CEILING. `cards` triages
    and refuses to invent a number; the book refuses to ship one that was not
    measured. So the only thing standing between the card scout and real max
    bids is a browser measurement per tier - and this makes that one paste.
    """
    from .cards import CARD_TIERS
    from .ebay_ui import EXTRACT_JS, build_report, load_raw, sold_url

    tiers = {t.key: t for t in CARD_TIERS}

    if not args.tier:
        print("SPORTS-CARD TIERS WAITING ON A MEASUREMENT\n")
        print("Each needs ONE browser paste. Do the sealed box first if you only\n"
              "do one - it is the only card product with no condition variable.\n")
        for t in CARD_TIERS:
            print(f"  {t.key}")
            print(f"      {t.label}")
            print(f"      {t.why}")
            print(f"      flipscout cardcomp {t.key}\n")
        return 0

    t = tiers.get(args.tier)
    if not t:
        print(f"error: unknown tier {args.tier!r}. Run `flipscout cardcomp` for "
              f"the list.", file=sys.stderr)
        return 1

    if not args.source:
        print(f"MEASURING: {t.label}\n")
        print("1. Open this in your normal Chrome:\n")
        print(f"   {sold_url(t.query)}\n")
        print("2. DevTools (F12) -> Console, paste this, Enter. It copies to your clipboard:")
        print("   " + "-" * 56)
        for line in EXTRACT_JS.strip().splitlines():
            print("   " + line)
        print("   " + "-" * 56)
        print(f"\n3. Save the clipboard to a file, then:")
        print(f'     flipscout cardcomp {t.key} --from comps.json')
        return 0

    text = sys.stdin.read() if args.source == "-" else open(args.source, encoding="utf-8").read()
    try:
        rows = load_raw(text)
    except ValueError as e:
        print(f"error: couldn't parse that as JSON ({e}).", file=sys.stderr)
        return 1

    rep = build_report(t.query, rows, resell_shipping=args.ship)
    for w in rep.warnings():
        print(f"  ! {w}")
    prices = sorted(r.all_in for r in (rep.clean or rep.rows))
    if not prices:
        print("no usable sold rows - nothing to measure.", file=sys.stderr)
        return 1
    # 🚨 NEAREST-RANK, NOT len//4. With 40 prices - ten at $100 and thirty at
    # $500 - `prices[40 // 4]` is index 10, the ELEVENTH value, which is $500:
    # it reports the expensive cohort as the floor. Off by one at exactly the
    # boundary, and in the expensive direction, since the whole point of
    # quoting p25 is to sit BELOW the cheap tail the title cannot separate out.
    p25 = round(prices[max(0, math.ceil(0.25 * len(prices)) - 1)], 2)
    med = rep.headline

    print(f"\n{t.label}")
    print(f"  n={len(prices)} clean of {len(rep.rows)} parsed")
    print(f"  p25 ${p25:,.2f}   median ${med:,.2f}   range ${prices[0]:,.2f}-${prices[-1]:,.2f}")

    # 🚨 THE FLOOR, NOT THE MIDDLE. Every card population carries a cheaper
    # cohort the title cannot separate out, and the book's own card tiers are
    # all pinned at p25 for that reason. A median-based comp is a guess with
    # money behind it.
    from .bidding import advise
    adv = advise(comp=p25, outbound_shipping=args.ship or 5.0,
                 inbound_shipping=args.inbound, target_profit=args.target)
    print(f"\n  At p25 the ceiling is ${adv.max_bid:,.2f} "
          f"(${args.target:,.0f} profit over ${args.inbound:,.0f} inbound).")
    if adv.max_bid <= 0:
        print("  🚨 $0.00 - this tier CANNOT clear the gate. Record it in "
              "DEAD_MODELS with these numbers rather than shipping it.")
        return 0

    print(f"\n  Paste into pricebook.MODELS:\n")
    print(f'    Model(')
    print(f'        key="{t.key}",')
    print(f'        label="{t.label}",')
    print(f'        comp={p25}, measured="{args.today}", sample={len(prices)},')
    print(f'        include=r"{t.include}",')
    print(f'        exclude=r"\\breprint\\b|\\bproxy\\b|\\bcustom\\b|\\bfake\\b|\\blot\\b|\\bbulk\\b",')
    print(f'        outbound_shipping={args.ship or 5.0}, category="sports-cards",')
    print(f'        comp_query="{t.query}", comp_used_only=False,')
    print(f'        specificity={t.specificity},')
    print(f'        note="FLOOR at p25 ${p25:,.2f} of a ${med:,.2f} median '
          f'(n={len(prices)}).",')
    print(f'    ),')
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


def cmd_bought(args) -> int:
    from .ledger import record_buy
    e = record_buy(args.title, paid=args.paid, source=args.source,
                   url=args.url, note=args.note)
    tag = e["model_label"] if e["model"] else "NOT IN BOOK - no comp to check against"
    comp = f" (comp ${e['comp_at_buy']:,.2f} at buy time)" if e.get("comp_at_buy") else ""
    print(f"ledger #{e['id']}: {e['title'][:60]}\n  paid ${e['paid']:,.2f} | {tag}{comp}")
    print(f"  close it later with: flipscout sold {e['id']} --gross <sale> --shipping <post>")
    return 0


def cmd_sold(args) -> int:
    from .ledger import record_sale
    e = record_sale(args.id, gross=args.gross, shipping=args.shipping)
    if not e:
        print(f"no open ledger entry #{args.id} - run `flipscout pnl` to list them")
        return 1
    print(f"ledger #{e['id']} SOLD: gross ${e['gross']:,.2f} -> net ${e['net']:,.2f} "
          f"-> profit ${e['profit']:,.2f} (paid ${e['paid']:,.2f})")
    if e.get("comp_at_buy"):
        share = e["gross"] / e["comp_at_buy"]
        print(f"  realized {share:.0%} of the ${e['comp_at_buy']:,.2f} comp"
              + (" - re-measure this model" if share < 0.85 else ""))
    return 0


def cmd_pnl(args) -> int:
    from .ledger import pnl
    print(pnl())
    return 0


def cmd_mybids(args) -> int:
    """Watch MY active ShopGoodwill bids; alert on outbid + the final-90min window."""
    from .mybids import load_env_file, run
    load_env_file()  # Scheduled Tasks get no shell profile; the webhook lives in .env
    res = run(csv_path=args.csv, window_min=args.window_min, dry=args.dry)
    print(f"{res['tracked']} tracked | {res['alerts']} alert(s)"
          + (f" via {', '.join(res['sent'])}" if res["sent"] else ""))
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

    # Velocity knobs: the operating model your capital actually runs on. Shared
    # by `velocity` and `portfolio` so one set of assumptions scores both.
    vel = argparse.ArgumentParser(add_help=False)
    vel.add_argument("--prep-days", type=float, default=2.0,
                     help="buy -> listed: clean/photo/write-up (default 2)")
    vel.add_argument("--ship-days", type=float, default=3.0,
                     help="sold -> delivered (default 3)")
    vel.add_argument("--payout-days", type=float, default=2.0,
                     help="delivered -> money spendable (default 2)")
    vel.add_argument("--assume-days", type=float, default=45.0,
                     help="days-to-sell assumed when nothing tells us (default 45)")
    vel.add_argument("--handle-min", type=float, default=25.0,
                     help="your hands-on minutes per flip (default 25)")
    vel.add_argument("--fast", action="store_true",
                     help="optimistic cycle (same-day listing, quick payout)")
    vel.add_argument("--hot", type=float, default=2.0,
                     help="$ per $100 per day for a HOT tier (default 2.00)")
    vel.add_argument("--good", type=float, default=0.75,
                     help="$ per $100 per day for a GOOD tier (default 0.75)")
    vel.add_argument("--min-hourly", type=float, default=20.0,
                     help="floor on profit per hour of YOUR time (default 20)")

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
    pm.add_argument("--velocity", type=float, default=None, metavar="PER100PERDAY",
                    help="also solve the ceiling that hits this velocity target "
                         "(e.g. 0.75) over --days-to-sell days")
    pm.add_argument("--days-to-sell", type=float, default=None,
                    help="days to sell, for --velocity (default: assume 45)")
    pm.set_defaults(func=cmd_maxpay)

    pv = sub.add_parser("velocity", parents=[common, vel],
                        help="score one item on profit per DOLLAR per DAY")
    pv.add_argument("title")
    pv.add_argument("--buy", type=float, required=True, help="local/source price you'd pay")
    pv.add_argument("--sold", type=float, default=None,
                    help="median eBay SOLD price (from the Sold items filter)")
    pv.add_argument("--days-to-sell", type=float, default=None,
                    help="your estimate of days to sell; beats the counts estimate")
    pv.add_argument("--ship-cost", type=float, default=0.0, help="postage YOU pay")
    pv.add_argument("--ship-charge", type=float, default=0.0, help="postage buyer pays you")
    pv.add_argument("--extra", type=float, default=0.0, help="supplies/refurb/gas per item")
    pv.add_argument("--sold-count", type=int, default=None, help="# sold in lookback")
    pv.add_argument("--active-count", type=int, default=None, help="# active listings")
    pv.add_argument("--memory", nargs="?", const=DEFAULT_MEMORY, default=None,
                    help="price-book file to auto-fill sold prices")
    pv.add_argument("--ebay", action="store_true",
                    help="fetch sold price + counts live from the eBay API")
    pv.set_defaults(func=cmd_velocity)

    pp = sub.add_parser("portfolio", parents=[common, vel],
                        help="spend a bankroll on the best-velocity candidates in a CSV")
    pp.add_argument("path", help="candidates CSV (same columns as `csv`, plus days_to_sell)")
    pp.add_argument("--bankroll", type=float, required=True,
                    help="cash you have to deploy right now")
    pp.add_argument("--hours", type=float, default=8.0,
                    help="hands-on hours you have this week (default 8)")
    pp.add_argument("--min-tier", default="GOOD", choices=["HOT", "GOOD", "SLOW"],
                    help="worst velocity tier you'll buy (default GOOD)")
    pp.add_argument("--memory", nargs="?", const=DEFAULT_MEMORY, default=None,
                    help="price-book file to auto-fill missing sold prices")
    pp.add_argument("--ebay", action="store_true",
                    help="fetch sold prices live from the eBay API")
    pp.set_defaults(func=cmd_portfolio)

    pt = sub.add_parser("turns",
                        help="realized velocity + parked capital from your ledger")
    pt.add_argument("--stale-days", type=int, default=60,
                    help="flag unsold positions older than this (default 60)")
    pt.set_defaults(func=cmd_turns)

    sub.add_parser("goldmines", help="print the goldmine-category buy-box cheat-sheet") \
       .set_defaults(func=cmd_goldmines)

    pcc = sub.add_parser(
        "cardcomp",
        help="measure a sports-card tier in the browser -> a real priced Model")
    pcc.add_argument("tier", nargs="?", help="tier key (omit to list them)")
    pcc.add_argument("--from", dest="source", default=None,
                     help="the pasted JSON file, or - for stdin")
    pcc.add_argument("--ship", type=float, default=5.0, help="your outbound postage")
    pcc.add_argument("--inbound", type=float, default=9.0, help="shipping TO you")
    pcc.add_argument("--target", type=float, default=20.0, help="target profit")
    pcc.add_argument("--today", default="", help="measurement date (YYYY-MM-DD)")
    pcc.set_defaults(func=cmd_cardcomp)

    pcard = sub.add_parser(
        "card", help="triage a sports-card title (chase/hit/rookie read, no price)")
    pcard.add_argument("titles", nargs="+", help="one or more card titles")
    pcard.set_defaults(func=cmd_card)

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

    # (ledger command handlers live near the parser tail; see below)
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

    # --- purchase ledger: realized P&L vs the book's comps -------------------
    pb = sub.add_parser("bought", help="record a purchase in the ledger")
    pb.add_argument("title", help="listing title (matched against the book)")
    pb.add_argument("--paid", type=float, required=True, help="all-in cost incl. fees/shipping")
    pb.add_argument("--source", default="", help="where it was bought")
    pb.add_argument("--url", default="")
    pb.add_argument("--note", default="")
    pb.set_defaults(func=cmd_bought)

    psold = sub.add_parser("sold", help="close a ledger entry with the real sale")
    psold.add_argument("id", type=int, help="ledger entry id (see `flipscout pnl`)")
    psold.add_argument("--gross", type=float, required=True, help="what the buyer paid, all-in")
    psold.add_argument("--shipping", type=float, default=0.0, help="your outbound postage")
    psold.set_defaults(func=cmd_sold)

    sub.add_parser("pnl", help="realized P&L and comp-vs-realized drift") \
       .set_defaults(func=cmd_pnl)

    pmb = sub.add_parser("mybids",
        help="watch YOUR ShopGoodwill bids: outbid alerts + final-90min siren")
    pmb.add_argument("--csv", default=None,
                     help="bids CSV (default: newest 'Auctions in Progress*.csv' in Downloads)")
    pmb.add_argument("--window-min", type=float, default=90.0,
                     help="endgame window in minutes (default 90)")
    pmb.add_argument("--dry", action="store_true", help="print alerts, don't send")
    pmb.set_defaults(func=cmd_mybids)

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
