"""Goldmine categories — a starter "buy box" for Facebook-Marketplace -> eBay flips.

These are durable *category patterns*, not a hot-items list (hot items rotate and
get swarmed). The edge is structural and stable: Facebook sellers price to get rid
of things; eBay prices by brand/model demand; the gap is your margin.

This is a starting cheat-sheet to learn cold, NOT a substitute for checking sold
comps on the specific item. Always verify with eBay's Sold filter before buying —
that's what analyzer.analyze / cli `item` are for.

`ship` field: how you move it.
  "easy"   -> boxes and ships cheaply; nationwide buyers.
  "local"  -> bulky/heavy; local pickup or freight. The shipping pain IS the edge.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Goldmine:
    name: str
    why: str          # why Facebook underprices it / why eBay demand is deep
    examples: str     # brands/models to learn
    ship: str         # "easy" | "local"
    watch: str        # the main gotcha


GOLDMINES: list[Goldmine] = [
    Goldmine(
        name="Power tools",
        why="Huge brand demand, compact, estate/retiree sellers dump them cheap. "
            "Best profit-per-effort category there is.",
        examples="DeWalt, Milwaukee, Makita, Snap-on, Festool, Bosch",
        ship="easy",
        watch="Test that it powers on; batteries/chargers add value, verify they hold charge.",
    ),
    Goldmine(
        name="Bulky items nobody wants to ship",
        why="The purest Facebook edge: the shipping pain that scares off competition "
            "is exactly why it's cheap locally. Motivated sellers clearing space.",
        examples="Solid-wood furniture, gym equipment (racks, dumbbells, barbells, "
                 "cable machines), appliances, treadmills",
        ship="local",
        watch="Confirm you can transport it. Flip local or offer freight/local pickup on eBay.",
    ),
    Goldmine(
        name="Game consoles & retro gaming",
        why="Deep, constant demand with clear comps; retro and sealed appreciate.",
        examples="Switch/PS5/Xbox, retro (SNES, N64, GameCube), sealed games, CIB titles",
        ship="easy",
        watch="Test it works; count controllers/cables; retro means check for corrosion.",
    ),
    Goldmine(
        name="Tech / electronics",
        why="High value, fast comps, always in demand.",
        examples="MacBooks, iPads, iPhones, GPUs, AirPods, mechanical keyboards",
        ship="easy",
        watch="SCAM-PRONE: check for iCloud/activation lock, blacklisted IMEI, stolen goods. "
              "Meet safe, verify it fully wipes and signs out before you pay.",
    ),
    Goldmine(
        name="Vintage & branded clothing / sneakers",
        why="Seller sees 'old clothes'; eBay sees brand and rarity. High margin, cheap "
            "to ship. Slower per item but stackable into volume.",
        examples="Carhartt, Patagonia, Levi's (redline/big-E), vintage band tees, "
                 "brand-name sneakers, designer",
        ship="easy",
        watch="Authenticate (fakes are everywhere); check flaws, sizing, measurements.",
    ),
    Goldmine(
        name="LEGO, trading cards & collectibles",
        why="Retired LEGO sets appreciate; strong steady collector demand.",
        examples="Retired LEGO sets, sealed sets, sports/Pokemon cards, coins, comics",
        ship="easy",
        watch="Completeness matters hugely; sealed >> opened. Beware reprints/fakes on cards.",
    ),
]


def format_goldmines(mines: list[Goldmine] = GOLDMINES) -> str:
    """A readable cheat-sheet you can print while sourcing."""
    out = ["GOLDMINE CATEGORIES  (Facebook Marketplace -> eBay)",
           "Learn these cold. Still check SOLD comps on the specific item before buying.\n"]
    for m in mines:
        tag = "SHIPS EASY" if m.ship == "easy" else "LOCAL/BULKY"
        out.append(f"[{tag:^11}] {m.name}")
        out.append(f"    why:   {m.why}")
        out.append(f"    learn: {m.examples}")
        out.append(f"    watch: {m.watch}\n")
    out.append("The pattern: bulky = less competition + pickup margin; branded tools/tech")
    out.append("= deep demand + easy comps; vintage/collectible = seller mispricing.")
    out.append("Best flips sit where two overlap (e.g. a branded tool at an estate sale).")
    return "\n".join(out)
