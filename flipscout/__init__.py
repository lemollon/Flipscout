"""flipscout — a ToS-safe eBay sourcing profit analyzer.

You source locally (Facebook Marketplace, thrift stores, garage sales, clearance
racks) by browsing yourself, at human speed. This package does the *math* that
tells you whether an item is worth buying: given what you'd pay for it and what
it sells for on eBay, it computes the net profit after eBay/payment fees and
shipping, the return on investment, and — when you have the numbers — how fast
it actually sells (sell-through).

Nothing here scrapes Facebook. The `comps` module talks to eBay's *official*
API when you add credentials; until then it runs in "estimate mode" on the sold
prices you type in from a normal (free) eBay sold-listings search.

Modules:
  fees     — the eBay managed-payments fee model + net-proceeds math.
  comps    — pluggable eBay comparable-sales provider (offline + live stub).
  analyzer — combine sourcing cost + comps + fees into a scored deal.
  cli      — analyze a single item or a CSV of candidates from the terminal.
"""

# --------------------------------------------------------------------------
# 🚨 MAKE THE CONSOLE SURVIVE NON-ASCII.
#
# Windows hands Python a cp1252 stdout, and cp1252 cannot encode "🚨" - or a
# degree sign, or a curly quote. Any print() carrying one raises
# UnicodeEncodeError and takes the whole command down.
#
# That is not theoretical: `python -m flipscout.fbsweep --help` crashed
# outright on 2026-08-18, because the help text it prints is this package's own
# docstrings and those are full of 🚨 markers. Alert bodies, auction titles
# ("Truecraft 6″ Wrench") and eBay listing names hit the same wall.
#
# errors="replace" is deliberate over "strict": a mangled character in a log
# line is a cosmetic problem, while a crashed sniper at T-180s is a lost lot.
# Guarded because pytest and piped output replace these streams with objects
# that have no reconfigure().
# --------------------------------------------------------------------------
import sys as _sys

for _stream in (_sys.stdout, _sys.stderr):
    try:
        if (getattr(_stream, "encoding", "") or "").lower() not in ("utf-8", "utf8"):
            _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
del _sys, _stream

from .fees import FeeModel, net_proceeds
from .comps import Comp, CompsProvider, EstimateComps, load_memory, save_comp
from .analyzer import (
    Candidate, DealAnalysis, MaxPay, analyze, analyze_csv, max_pay, Verdict,
)

__all__ = [
    "FeeModel",
    "net_proceeds",
    "Comp",
    "CompsProvider",
    "EstimateComps",
    "load_memory",
    "save_comp",
    "Candidate",
    "DealAnalysis",
    "MaxPay",
    "Verdict",
    "analyze",
    "analyze_csv",
    "max_pay",
]
