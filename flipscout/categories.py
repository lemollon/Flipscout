"""Goldmine categories — a starter "buy box" ranked for margin, for FB / thrift ->
eBay flips.

Durable *category patterns*, not a hot-items list (hot items rotate and get
swarmed). The edge is structural: sellers price to get rid of things; eBay prices by
brand/model demand; the gap is your margin. Each entry carries a rough buy→sell band
so you know the shape before you check the specific item.

Two rules baked into the picks: keep **shipping under ~40% of the sale price**, and
aim for **25%+ net margin** after eBay's ~13% fee. A cheat-sheet is a starting point,
never a buy signal — the sold data on the specific item is (use `item` / `scan`).

`ship`: "easy" ships cheaply nationwide; "local" is bulky (local pickup is the edge).
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Goldmine:
    name: str
    band: str         # rough buy -> sell / margin shape
    why: str
    examples: str
    ship: str         # "easy" | "local"
    watch: str


GOLDMINES: list[Goldmine] = [
    Goldmine(
        name="Vintage & branded clothing",
        band="$3–5 → $40–80 · 60–120% margin",
        why="Seller sees 'old clothes'; eBay pays for the brand. Best margin-per-effort; "
            "cheap to ship, stacks into volume.",
        examples="Carhartt, Patagonia, Levi's (redline/big-E), vintage band tees, "
                 "wool/cashmere, Y2K (Von Dutch, Juicy, Baby Phat)",
        ship="easy",
        watch="Authenticate (fakes everywhere); check flaws, sizing, measurements.",
    ),
    Goldmine(
        name="Y2K digital cameras",
        band="$10–20 → $40–120 · sells in days",
        why="Gen-Z fad for early-2000s point-and-shoots; move fast, ship tiny.",
        examples="Canon PowerShot, Nikon Coolpix, Sony Cyber-shot (~2005–2010)",
        ship="easy",
        watch="Test it powers on + takes a photo; needs the right battery/SD — include them.",
    ),
    Goldmine(
        name="Retro gaming (complete-in-box)",
        band="loose cart $8 → CIB $40 · systems 3–5×",
        why="CIB sells 5–10× a loose cart; deep steady demand, clear comps.",
        examples="NES/SNES/N64/GameCube, sealed games, CIB titles, handhelds",
        ship="easy",
        watch="Test it; count controllers/cables; retro means check for corrosion.",
    ),
    Goldmine(
        name="Power tools",
        band="$20–40 → $90–200 · high $/effort",
        why="Huge brand demand, compact, estate/retiree sellers dump them cheap.",
        examples="DeWalt, Milwaukee, Makita, Snap-on, Festool, Bosch",
        ship="easy",
        watch="Test it powers on; batteries/chargers add value — check they hold charge.",
    ),
    Goldmine(
        name="Vintage & pro audio",
        band="$30 → $200+ · home-studio boom",
        why="Thrift-underpriced; content creators want the classic gear.",
        examples="Pioneer/Marantz/Sansui receivers, mixers, studio mics, monitors, "
                 "vintage speakers",
        ship="local",
        watch="Heavy — factor shipping or flip local; test all channels/inputs.",
    ),
    Goldmine(
        name="Small OEM parts & accessories",
        band="$1–5 → $20–60 · tiny ship",
        why="Buyer needs the EXACT part; low competition, featherweight shipping.",
        examples="Proprietary cables/adapters, remotes, ink/toner, laptop parts, "
                 "OEM auto parts (buyers search by part #), phone cases/chargers",
        ship="easy",
        watch="Match the exact model/part number; note compatibility precisely.",
    ),
    Goldmine(
        name="Jewelry, cards & collectibles",
        band="thrift lot → multiples · very high %",
        why="Often priced as 'costume'; sold by hallmark, silver weight, or singles.",
        examples="Sterling (by weight), vintage costume jewelry, Pokémon/sports card "
                 "lots (sell the singles), coins, small LEGO minifigs",
        ship="easy",
        watch="Verify hallmarks/authenticity; sealed ≫ opened; beware reprints/fakes.",
    ),
    Goldmine(
        name="Bulky items nobody wants to ship",
        band="local pickup · low % but big $",
        why="The purest local edge: the shipping pain that scares off competition is "
            "exactly why it's cheap locally.",
        examples="Solid-wood furniture, gym equipment, appliances, treadmills",
        ship="local",
        watch="Confirm you can transport it. Flip local or offer freight/pickup.",
    ),
]


def format_goldmines(mines: list[Goldmine] = GOLDMINES) -> str:
    """A readable, margin-ranked cheat-sheet you can print while sourcing."""
    out = ["GOLDMINE CATEGORIES  (FB / thrift -> eBay, ranked for margin)",
           "Rule: shipping < 40% of sale, aim for 25%+ net. Still check SOLD comps "
           "on the specific item.\n"]
    for m in mines:
        tag = "SHIPS EASY" if m.ship == "easy" else "LOCAL/BULKY"
        out.append(f"[{tag:^11}] {m.name}   ({m.band})")
        out.append(f"    why:   {m.why}")
        out.append(f"    learn: {m.examples}")
        out.append(f"    watch: {m.watch}\n")
    out.append("Best % margin = small/light (clothing, cameras, parts) — needs volume.")
    out.append("Best absolute $ = tools, vintage audio, consoles — fewer, bigger sales.")
    return "\n".join(out)
