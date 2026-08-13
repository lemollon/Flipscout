"""2026-08-13: the breadth pack.

The Discord digest was dominated by a handful of high-volume models
(Starrett, Mitutoyo, film cameras, camcorders - sorted by profit_at_open and
released top-N) while whole categories with measured live supply went
unpriced. Two fixes, tested here:

  1. New price-book models: iPods beyond the Classic (nano/touch, plus a
     capacity-unknown catch-all for the Classic/Video), watches, a Bose
     headphone, a film lens, and Walkman. Every comp below is MEASURED
     2026-08-13 (n=123 per eBay used-solds query).
  2. A per-model cap on the fresh-alert selection so one model can no longer
     monopolize a run - the rest defer to the next run instead of vanishing.
"""

from flipscout.pricebook import match, search_terms
from flipscout import hunt


# --- iPods beyond Classic ----------------------------------------------------

def test_ipod_classic_with_capacity_still_wins_over_the_catchall():
    # The capacity models (specificity 30) must still win when GB is in the
    # title - the new catch-all (specificity 15) exists only to catch what
    # they miss.
    assert match("Apple iPod Classic 160GB").model.key == "ipod_classic_160"


def test_ipod_classic_no_capacity_hits_the_new_catchall():
    assert match("Apple iPod Classic - Black").model.key == "ipod_classic_nocap"


def test_tripod_never_matches_any_ipod_model():
    # "ipod" is a substring of "tripod" - every ipod include below needs a
    # word boundary or this silently prices a camera tripod as an iPod.
    assert match("Vintage Camera Tripod with Carrying Case") is None


def test_ipod_nano_and_touch_price():
    assert match("Apple iPod Nano 6th Generation 8GB Blue").model.key == "ipod_nano"
    assert match("Apple iPod Touch 5th Gen 32GB Space Gray").model.key == "ipod_touch"


def test_ipod_nano_armband_case_rejected():
    assert match("Apple iPod Nano armband case only") is None


# --- watches ------------------------------------------------------------------

def test_gshock_prices_and_rejects_bezel_band_accessory():
    assert match("Casio G-Shock GA-2100 Black Resin Watch").model.key == "casio_gshock"
    assert match("G-Shock bezel and band set") is None


def test_seiko_automatic_needs_a_corroborating_noun():
    # A BRAND IS NOT A MODEL - bare "Seiko" is also cheap quartz.
    assert match("Seiko 5 Automatic Divers Watch SNK809").model.key == "seiko_automatic"
    assert match("Seiko watch band bracelet") is None
    assert match("Seiko dress watch") is None


def test_gshock_lookalike_phrasing_rejected():
    # Same failure mode as Gunne Sax STYLE: a no-name knockoff that
    # advertises itself honestly as "G-Shock STYLE" still matched on the
    # bare brand text. Live catch: a $2.25 WR50M digital with no Casio
    # branding at all.
    assert match("Digital Gold And Black G-Shock Style Digital Watch") is None
    # the real thing must still match
    assert match("Casio G-Shock DW-5600E").model.key == "casio_gshock"


def test_seiko_lookalike_phrasing_rejected():
    assert match("Seiko style automatic skeleton watch") is None
    assert match("Seiko 5 Automatic SNK809").model.key == "seiko_automatic"


# --- headphones -----------------------------------------------------------

def test_qc35_prices_and_rejects_replacement_ear_pads():
    assert match("Bose QuietComfort 35 II Wireless Headphones").model.key == "bose_qc35"
    assert match("QC35 Black").model.key == "bose_qc35"
    assert match("Bose QC35 replacement ear pads") is None


# --- lenses -----------------------------------------------------------------

def test_canon_fd_50_14_prices_and_rejects_a_bare_lens_cap():
    assert match("Canon FD 50mm f/1.4 SSC Lens").model.key == "canon_fd_50_14"
    assert match("Canon FD front lens cap") is None


# --- walkman ------------------------------------------------------------------

def test_sony_walkman_prices():
    assert match("Sony Walkman WM-D6C Professional Cassette Player").model.key == "sony_walkman"


# --- search terms sweep the new categories -----------------------------------

def test_breadth_pack_search_terms_are_swept():
    terms = search_terms()
    for t in ("ipod nano", "ipod touch", "sony walkman", "walkman lot",
              "bose quietcomfort", "casio g-shock", "g shock watch",
              "seiko automatic", "seiko watch lot", "canon fd",
              "vintage camera lens", "camera lens lot"):
        assert t in terms, t


# --- the per-model digest cap ------------------------------------------------

class FakeHunter:
    name = "goodwill"

    def __init__(self, rows):
        self._rows = rows

    def search(self, query, limit=40):
        return list(self._rows)


def _row(id_, title, price):
    return {"source": "goodwill", "id": id_, "title": title,
            "url": f"https://example.test/{id_}", "price": price,
            "min_bid": price, "increment": 1.0, "bids": 0,
            "handling": 0.0, "image": "i", "ends": ""}


def test_per_model_cap_backfills_and_leaves_overflow_unseen(monkeypatch):
    # 6 TI-84 Plus CE candidates (would normally sweep the whole digest) plus
    # 2 Starrett candidates.
    ce_rows = [_row(f"ce{i}", "TI-84 Plus CE Graphing Calculator", 5.0 + i)
               for i in range(6)]
    other_rows = [_row(f"st{i}", "Starrett Combination Square", 10.0 + i)
                  for i in range(2)]
    rows = ce_rows + other_rows

    saved = {}

    def fake_save(path, seen):
        saved["seen"] = set(seen)

    monkeypatch.setattr(hunt, "_save_seen", fake_save)
    monkeypatch.setattr(hunt, "_load_seen", lambda p, **kw: set())

    sent = {}

    def fake_notify(alerts, content="", **kw):
        sent["alerts"] = alerts
        return ["webhook"]

    cfg = {"sources": ["goodwill"], "target_profit": 5.0, "inbound_shipping": 0.0,
           "top": 10, "max_per_model": 3, "state_file": "nonexistent.json"}
    res = hunt.run(cfg, hunters=[FakeHunter(rows)], notifier=fake_notify)

    alerts = sent["alerts"]
    ids_alerted = {a["url"].rsplit("/", 1)[-1] for a in alerts}
    ce_ids_alerted = {i for i in ids_alerted if i.startswith("ce")}
    st_ids_alerted = {i for i in ids_alerted if i.startswith("st")}

    # capped at 3 CE picks, backfilled with both Starrett candidates
    assert len(ce_ids_alerted) == 3
    assert len(st_ids_alerted) == 2
    assert len(alerts) == 5
    assert res["new"] == 5

    # the 3 capped-out CE candidates must stay OUT of the seen-cache, so they
    # queue for the next run instead of being silently dropped
    ce_ids_capped = {r["id"] for r in ce_rows} - ce_ids_alerted
    assert len(ce_ids_capped) == 3
    for cid in ce_ids_capped:
        assert f"goodwill:{cid}" not in saved["seen"]
    for cid in ce_ids_alerted | st_ids_alerted:
        assert f"goodwill:{cid}" in saved["seen"]


def test_per_model_cap_default_is_three(monkeypatch):
    import os
    monkeypatch.delenv("FLIPSCOUT_MAX_PER_MODEL", raising=False)
    assert hunt.load_config(os.environ)["max_per_model"] == 3


def test_per_model_cap_reads_env(monkeypatch):
    import os
    monkeypatch.setenv("FLIPSCOUT_MAX_PER_MODEL", "5")
    assert hunt.load_config(os.environ)["max_per_model"] == 5
