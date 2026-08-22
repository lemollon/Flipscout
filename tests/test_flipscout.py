"""Tests for the flipscout sourcing analyzer: fee math, comps/sell-through, the
buy/skip/needs-comp decision, and the CSV workflow."""

import os

from flipscout.fees import FeeModel, net_proceeds
from flipscout.comps import Comp, EstimateComps, median_sold, load_memory, save_comp
from flipscout.analyzer import (
    Candidate, Thresholds, Verdict, analyze, analyze_csv, candidates_from_csv, max_pay,
)


# --- fees -------------------------------------------------------------------

def test_net_proceeds_basic_free_shipping():
    # $100 sale, free shipping (you eat $10 postage), default fees.
    np_ = net_proceeds(100.0, shipping_cost=10.0)
    # FVF 13.25% of 100 = 13.25, fixed 0.40, postage 10 -> net = 100 - 13.25 - .40 - 10
    assert round(np_.final_value_fee, 2) == 13.25
    assert np_.fixed_fee == 0.40
    assert round(np_.net, 2) == round(100 - 13.25 - 0.40 - 10, 2)


def test_fvf_charged_on_buyer_paid_shipping():
    # Buyer pays $10 shipping; FVF applies to the 110 order total, not just 100.
    np_ = net_proceeds(100.0, shipping_cost=10.0, shipping_charged=10.0)
    assert round(np_.final_value_fee, 2) == round(110 * 0.1325, 2)
    # Buyer-paid shipping offsets your postage, so net is higher than free-shipping.
    assert np_.net > net_proceeds(100.0, shipping_cost=10.0).net


def test_small_order_fixed_fee():
    np_ = net_proceeds(8.0)
    assert np_.fixed_fee == 0.30  # <= $10 order total


def test_negative_inputs_rejected():
    try:
        net_proceeds(-1.0)
    except ValueError:
        pass
    else:
        raise AssertionError("expected ValueError on negative price")


# --- comps ------------------------------------------------------------------

def test_sell_through():
    c = Comp("x", sold_price=50, sold_count=30, active_count=70)
    assert abs(c.sell_through - 0.30) < 1e-9


def test_sell_through_needs_both_counts():
    assert Comp("x", sold_price=50, sold_count=30).sell_through is None


def test_estimate_comps_uses_observed_then_known_then_unknown():
    prov = EstimateComps()
    prov.add(Comp("Widget", sold_price=40, source="file"))
    assert prov.lookup("anything", observed_price=99).sold_price == 99
    assert prov.lookup("widget").sold_price == 40          # case-insensitive hit
    assert prov.lookup("never seen").has_price is False    # honest miss


def test_median_sold_ignores_junk():
    assert median_sold([10, 0, 20, None, 30]) == 20
    assert median_sold([]) is None


# --- analyzer ---------------------------------------------------------------

def test_needs_comp_when_no_price():
    a = analyze(Candidate("Mystery box", source_price=10))
    assert a.verdict is Verdict.NEEDS_COMP
    assert a.net_profit is None


def test_clear_buy():
    a = analyze(Candidate("Switch OLED", source_price=120, observed_price=250,
                          shipping_cost=12, sold_count=800, active_count=400))
    assert a.verdict is Verdict.BUY
    assert a.net_profit > 20
    assert a.roi > 0

def test_skip_when_unprofitable():
    a = analyze(Candidate("Phone case lot", source_price=10, observed_price=18,
                          shipping_cost=4))
    # 18 - ~2.79 fees - 4 ship - 10 cost is barely positive / below bar -> not BUY
    assert a.verdict in (Verdict.SKIP, Verdict.MAYBE)
    assert a.net_profit < 10


def test_loss_is_skip():
    a = analyze(Candidate("Overpay", source_price=200, observed_price=100,
                          shipping_cost=10))
    assert a.verdict is Verdict.SKIP
    assert a.net_profit < 0


def test_slow_mover_is_maybe():
    # Profitable and high ROI, but terrible sell-through -> MAYBE, not BUY.
    a = analyze(Candidate("Niche collectible", source_price=20, observed_price=80,
                          shipping_cost=8, sold_count=5, active_count=500))
    assert a.sell_through < 0.30
    assert a.verdict is Verdict.MAYBE


def test_thresholds_are_tunable():
    cand = Candidate("Thin flip", source_price=20, observed_price=40, shipping_cost=5)
    strict = analyze(cand, thresholds=Thresholds(min_profit=50))
    assert strict.verdict is Verdict.SKIP


# --- max pay (walk-away price) ----------------------------------------------

def test_max_pay_is_a_true_ceiling():
    # Paying exactly the ceiling must still pass BOTH bars; a cent more must fail.
    m = max_pay(250.0, shipping_cost=12.0,
                thresholds=Thresholds(min_profit=10, min_roi=0.50))
    at = analyze(Candidate("x", source_price=m.max_price, observed_price=250,
                           shipping_cost=12))
    over = analyze(Candidate("x", source_price=m.max_price + 1.0, observed_price=250,
                             shipping_cost=12))
    assert at.verdict in (Verdict.BUY, Verdict.MAYBE)
    assert at.net_profit >= 10 - 1e-6 and at.roi >= 0.50 - 1e-6
    assert not (over.net_profit >= 10 and over.roi >= 0.50)


def test_max_pay_binding_constraint():
    # A high ROI bar bites before the flat profit bar on a pricey item.
    m = max_pay(250.0, thresholds=Thresholds(min_profit=10, min_roi=2.0))
    assert m.binding == "roi"
    assert m.max_price > 0


def test_max_pay_walk_away_when_impossible():
    # A $5 item can't clear a $10 profit floor at any price -> ceiling 0.
    m = max_pay(5.0, thresholds=Thresholds(min_profit=10, min_roi=0.50))
    assert m.max_price == 0.0
    assert m.binding == "none"


# --- comps memory -----------------------------------------------------------

def test_memory_round_trip(tmp_path):
    path = str(tmp_path / "book.json")
    save_comp(path, Comp("Switch OLED", sold_price=250, sold_count=800, active_count=400))
    prov = load_memory(path)
    got = prov.lookup("switch oled")
    assert got.sold_price == 250
    assert got.sell_through is not None
    # And it feeds the analyzer without an observed_price on the candidate.
    a = analyze(Candidate("Switch OLED", source_price=120), provider=prov)
    assert a.verdict is Verdict.BUY


def test_load_memory_missing_file_is_empty(tmp_path):
    prov = load_memory(str(tmp_path / "nope.json"))
    assert prov.lookup("anything").has_price is False


# --- eBay live API (no network: fake session) -------------------------------

def test_parse_browse_and_insights():
    from flipscout.ebay_api import parse_browse, parse_insights
    browse = {"total": 320, "itemSummaries": [
        {"price": {"value": "199.99"}}, {"price": {"value": "250.00"}},
        {"noprice": True}]}
    ac, asks = parse_browse(browse)
    assert ac == 320 and asks == [199.99, 250.0]

    ins = {"total": 42, "itemSales": [
        {"lastSoldPrice": {"value": "240"}}, {"lastSoldPrice": {"value": "260"}}]}
    sc, solds = parse_insights(ins)
    assert sc == 42 and solds == [240.0, 260.0]


class _FakeResp:
    def __init__(self, status, body):
        self.status_code = status
        self._body = body
    def json(self):
        return self._body
    def raise_for_status(self):
        if self.status_code >= 400:
            import requests
            raise requests.HTTPError(f"{self.status_code}")


class _FakeSession:
    """Routes eBay endpoints to canned JSON so the provider runs offline."""
    def __init__(self, insights_status=200):
        self.insights_status = insights_status
    def post(self, url, **kw):
        return _FakeResp(200, {"access_token": "T", "expires_in": 7200})
    def get(self, url, **kw):
        if "browse" in url:
            return _FakeResp(200, {"total": 400, "itemSummaries": [
                {"price": {"value": "230"}}, {"price": {"value": "270"}}]})
        if "marketplace_insights" in url:
            if self.insights_status != 200:
                return _FakeResp(self.insights_status, {})
            return _FakeResp(200, {"total": 800, "itemSales": [
                {"lastSoldPrice": {"value": "240"}},
                {"lastSoldPrice": {"value": "260"}}]})
        return _FakeResp(404, {})


def _cfg():
    from flipscout.ebay_api import EbayConfig
    return EbayConfig(client_id="id", client_secret="sec")


def test_ebay_provider_full_data_drives_a_buy():
    from flipscout.ebay_api import EbayApiComps
    prov = EbayApiComps(cfg=_cfg(), session=_FakeSession())
    comp = prov.lookup("Nintendo Switch OLED")
    assert comp.sold_price == 250.0           # median of 240/260
    assert comp.sold_count == 800 and comp.active_count == 400
    assert comp.source == "ebay_insights"
    a = analyze(Candidate("Switch", source_price=120, shipping_cost=12), provider=prov)
    assert a.verdict is Verdict.BUY
    assert abs(a.sell_through - 800 / 1200) < 1e-9


def test_ebay_insights_gated_degrades_to_needs_comp():
    # Insights 403 (app not approved) -> no sold price, but active_count still filled.
    from flipscout.ebay_api import EbayApiComps
    prov = EbayApiComps(cfg=_cfg(), session=_FakeSession(insights_status=403))
    comp = prov.lookup("Some item")
    assert comp.sold_price is None
    assert comp.active_count == 400
    assert comp.source == "ebay_browse"
    a = analyze(Candidate("Some item", source_price=50), provider=prov)
    assert a.verdict is Verdict.NEEDS_COMP


def test_ebay_observed_price_overrides_api():
    from flipscout.ebay_api import EbayApiComps
    prov = EbayApiComps(cfg=_cfg(), session=_FakeSession())
    comp = prov.lookup("x", observed_price=99.0)
    assert comp.sold_price == 99.0 and comp.source == "manual"


# --- goldmine categories ----------------------------------------------------

def test_goldmines_cheatsheet():
    from flipscout.categories import GOLDMINES, format_goldmines
    assert len(GOLDMINES) >= 5
    assert all(m.ship in ("easy", "local") for m in GOLDMINES)
    text = format_goldmines()
    assert "Power tools" in text and "SOLD comps" in text


# --- csv --------------------------------------------------------------------

def _sample_path():
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(here, "flipscout", "sample_items.csv")


def test_candidates_from_csv_parses_blanks():
    cands = candidates_from_csv(_sample_path())
    assert len(cands) == 8
    poang = next(c for c in cands if "Poang" in c.title)
    assert poang.observed_price is None      # blank -> unknown
    assert poang.sold_count is None


def test_analyze_csv_sorted_buys_first():
    results = analyze_csv(_sample_path())
    verdicts = [r.verdict for r in results]
    # NEEDS_COMP (the Poang with no price) must sort to the very end.
    assert verdicts[-1] is Verdict.NEEDS_COMP
    # BUYs come before SKIPs.
    order = {Verdict.BUY: 0, Verdict.MAYBE: 1, Verdict.SKIP: 2, Verdict.NEEDS_COMP: 3}
    assert order_nondecreasing([order[v] for v in verdicts])


def order_nondecreasing(xs):
    return all(a <= b for a, b in zip(xs, xs[1:]))


# --- flipscout cardcomp, added 2026-08-22 -----------------------------------

def _cardcomp(argv):
    import io, contextlib
    from flipscout.cli import main
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        rc = main(argv)
    return rc, buf.getvalue()


def test_cardcomp_lists_the_tiers_waiting_on_a_measurement():
    rc, out = _cardcomp(["cardcomp"])
    assert rc == 0
    assert "sports_sealed_box" in out and "sports_rpa" in out


def test_cardcomp_prints_a_sold_search_and_the_paste_script():
    rc, out = _cardcomp(["cardcomp", "sports_rpa"])
    assert rc == 0
    assert "LH_Sold=1" in out and "DevTools" in out


def test_cardcomp_turns_a_paste_into_a_priced_model(tmp_path):
    """The last mile: one paste -> a real Model with a real ceiling."""
    import json
    f = tmp_path / "c.json"
    f.write_text(json.dumps([
        {"title": f"2021 Panini Prizm Basketball Factory Sealed Hobby Box {i}",
         "price": 200.0 + i * 10, "shipping": 0.0, "sold": "Aug 1, 2026"}
        for i in range(40)]))
    rc, out = _cardcomp(["cardcomp", "sports_sealed_box", "--from", str(f),
                         "--today", "2026-08-22"])
    assert rc == 0
    assert 'key="sports_sealed_box"' in out
    assert 'category="sports-cards"' in out
    assert 'measured="2026-08-22"' in out


def test_cardcomp_quotes_the_p25_floor_not_the_median(tmp_path):
    """🚨 Every card population carries a cheaper cohort the title cannot
    separate out - which is why every card tier in the book is pinned at p25.
    A median-based comp is a guess with money behind it."""
    import json, re
    f = tmp_path / "c.json"
    f.write_text(json.dumps([
        {"title": f"2021 Panini Prizm Basketball Factory Sealed Hobby Box {i}",
         "price": p, "shipping": 0.0, "sold": "Aug 1, 2026"}
        for i, p in enumerate([100] * 10 + [500] * 30)]))
    rc, out = _cardcomp(["cardcomp", "sports_sealed_box", "--from", str(f)])
    comp = float(re.search(r"comp=([\d.]+)", out).group(1))
    assert comp == 100.0, "took the median (500) instead of the p25 floor"


def test_cardcomp_refuses_to_ship_a_tier_that_cannot_clear_the_gate(tmp_path):
    """The honest outcome for most of this category - see DEAD_MODELS."""
    import json
    f = tmp_path / "c.json"
    f.write_text(json.dumps([
        {"title": f"2021 Panini Prizm Basketball Factory Sealed Hobby Box {i}",
         "price": 12.0, "shipping": 0.0, "sold": "Aug 1, 2026"} for i in range(30)]))
    rc, out = _cardcomp(["cardcomp", "sports_sealed_box", "--from", str(f)])
    assert rc == 0
    assert "CANNOT clear the gate" in out and "DEAD_MODELS" in out
    assert "Model(" not in out
