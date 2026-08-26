"""Tests for flipscout.velocity — the high-frequency layer.

The thesis under test: margin ranks deals wrong once capital is the scarce
thing. Every test here either pins a piece of that arithmetic or pins one of
the two floors (dollars, hours) that stop "fast" from meaning "good".
"""

import pytest

from flipscout.analyzer import Candidate, Verdict, analyze, candidates_from_csv
from flipscout.fees import FeeModel
from flipscout.velocity import (
    FAST, CycleModel, Tier, VelocityThresholds, allocate, max_pay_for_velocity,
    rank, realized_velocity, score, score_candidate,
)


def _scored(title="thing", buy=40.0, sold=95.0, days=9.0, ship=0.0, extra=0.0,
            cycle=CycleModel(), thresholds=VelocityThresholds(), minutes=None):
    return score_candidate(
        Candidate(title=title, source_price=buy, observed_price=sold,
                  shipping_cost=ship, extra_cost=extra),
        days_to_sell=days, cycle=cycle, velocity_thresholds=thresholds,
        handle_minutes=minutes,
    )


# --- the cycle: dead cash is longer than days-to-sell ------------------------

def test_hold_days_add_prep_ship_and_payout_to_days_to_sell():
    c = CycleModel(prep_days=2, ship_days=3, payout_days=2)
    assert c.overhead_days == 7
    assert c.hold_days(9) == 16          # not 9 — that's the whole point


def test_unknown_sell_speed_uses_the_pessimistic_default_and_says_so():
    a = _scored(days=None)
    assert a.days_assumed is True
    assert a.hold_days == CycleModel().hold_days(None) == 52
    assert any("assumed" in n.lower() for n in a.notes)


def test_fast_cycle_is_strictly_more_optimistic():
    assert FAST.hold_days(9) < CycleModel().hold_days(9)
    assert _scored(cycle=FAST).velocity > _scored().velocity


def test_zero_overhead_cycle_does_not_divide_by_zero():
    # A caller can zero every knob; a crash in the aisle is worse than a clamp.
    c = CycleModel(prep_days=0, ship_days=0, payout_days=0)
    a = _scored(days=0, cycle=c)
    assert a.velocity is not None and a.velocity > 0


# --- the core number --------------------------------------------------------

def test_velocity_is_profit_per_dollar_per_day():
    a = _scored(buy=40, sold=95, days=9)
    expected = a.deal.net_profit / a.deal.total_cost / a.hold_days
    assert a.velocity == pytest.approx(expected)
    assert a.per_100_per_day == pytest.approx(expected * 100)
    assert a.turns_per_year == pytest.approx(365 / a.hold_days)
    assert a.annual_return == pytest.approx(a.deal.roi * a.turns_per_year)
    assert a.profit_per_day == pytest.approx(a.deal.net_profit / a.hold_days)


def test_fatter_margin_can_still_be_the_worse_slot():
    """The reason this module exists: $200 on $400 over eight months loses to
    $25 on $40 over three weeks, even though every margin number prefers it."""
    fat = _scored("big slow flip", buy=400, sold=680, days=240)
    quick = _scored("small fast flip", buy=40, sold=95, days=21)
    assert fat.deal.net_profit > quick.deal.net_profit      # margin says fat
    assert quick.velocity > fat.velocity                    # velocity says quick
    assert rank([fat, quick])[0] is quick


def test_hourly_is_profit_over_your_hands_on_time():
    a = _scored(minutes=30)
    assert a.hourly == pytest.approx(a.deal.net_profit / 0.5)


# --- tiers, and the floors that cap them ------------------------------------

@pytest.mark.parametrize("days, expected", [
    (2, Tier.HOT),      # ~7 days held -> huge
    (60, Tier.GOOD),
    (150, Tier.SLOW),
    (600, Tier.DEAD),
])
def test_tiers_track_how_long_the_cash_is_stuck(days, expected):
    assert _scored(buy=40, sold=95, days=days).tier is expected


def test_a_losing_flip_is_dead_not_fast():
    a = _scored(buy=90, sold=95, days=1)
    assert a.deal.net_profit < 0
    assert a.tier is Tier.DEAD
    assert any("loses money" in n.lower() for n in a.notes)


def test_tiny_profit_cannot_ride_a_short_hold_into_hot():
    """$3 that turns in a week is a superb ratio and a bad evening."""
    a = _scored(buy=2.0, sold=8.0, days=1, thresholds=VelocityThresholds(min_profit=8.0))
    assert a.per_100_per_day > VelocityThresholds().hot   # the ratio IS hot
    assert a.tier is Tier.SLOW                            # the dollars are not
    assert any("floor" in n.lower() for n in a.notes)


def test_labor_floor_caps_a_flip_that_pays_badly_per_hour():
    a = _scored(buy=15, sold=32, days=5,
                thresholds=VelocityThresholds(min_profit=0.0, min_hourly=60.0))
    assert a.hourly < 60.0
    assert a.tier is Tier.SLOW
    assert any("/hour" in n for n in a.notes)


def test_no_comp_means_no_velocity_at_all():
    a = score_candidate(Candidate("mystery lamp", 25.0))
    assert a.deal.verdict is Verdict.NEEDS_COMP
    assert a.tier is Tier.NEEDS_COMP
    assert a.velocity is None and a.hourly is None
    assert "sold comp" in a.detail()


def test_summary_and_detail_render_without_blowing_up():
    for a in (_scored(), _scored(days=None), score_candidate(Candidate("x", 5.0))):
        assert a.title in a.summary()
        assert a.title in a.detail()


# --- the ceiling: what can I pay and keep this dollar working? ---------------

def test_max_pay_for_velocity_round_trips_to_exactly_the_target():
    cycle = CycleModel()
    m = max_pay_for_velocity(sale_price=95.0, days_to_sell=9.0,
                             target_per_100_per_day=1.50, cycle=cycle,
                             shipping_cost=6.0, extra_cost=1.0)
    a = score_candidate(
        Candidate("t", source_price=m.max_price, observed_price=95.0,
                  shipping_cost=6.0, extra_cost=1.0),
        days_to_sell=9.0, cycle=cycle,
    )
    assert a.per_100_per_day == pytest.approx(1.50, abs=1e-6)
    assert m.binding == "velocity"


def test_requiring_a_velocity_is_requiring_an_roi_with_a_clock_on_it():
    m = max_pay_for_velocity(sale_price=95.0, days_to_sell=9.0,
                             target_per_100_per_day=1.50)
    assert m.required_roi == pytest.approx(0.015 * m.hold_days)
    assert m.at_max_roi == pytest.approx(m.required_roi, abs=1e-6)


def test_a_fast_seller_earns_a_higher_ceiling_than_a_slow_one():
    fast = max_pay_for_velocity(95.0, days_to_sell=5, target_per_100_per_day=1.0)
    slow = max_pay_for_velocity(95.0, days_to_sell=120, target_per_100_per_day=1.0)
    assert fast.max_price > slow.max_price


def test_dollar_floor_binds_when_the_velocity_target_is_trivial():
    m = max_pay_for_velocity(95.0, days_to_sell=9,
                             target_per_100_per_day=0.01,
                             thresholds=VelocityThresholds(min_profit=25.0))
    assert m.binding == "profit"
    assert m.at_max_profit == pytest.approx(25.0, abs=1e-6)


def test_impossible_target_says_walk_away():
    m = max_pay_for_velocity(20.0, days_to_sell=400, target_per_100_per_day=5.0,
                             shipping_cost=12.0)
    assert m.max_price == 0.0
    assert m.binding == "none"
    assert "WALK AWAY" in m.summary()


# --- allocation: the bankroll is the real constraint ------------------------

def _plan_inputs():
    return [
        _scored("fast cheap", buy=20, sold=60, days=7),
        _scored("fast mid", buy=60, sold=150, days=10),
        _scored("slow fat", buy=200, sold=380, days=200),
    ]


def test_allocate_buys_best_velocity_first_and_respects_the_bankroll():
    plan = allocate(_plan_inputs(), bankroll=70.0, hours=8.0)
    titles = [a.title for a in plan.bought]
    assert titles == ["fast cheap"]              # "fast mid" needs $60, $50 left
    assert plan.capital_used == pytest.approx(20.0)
    assert plan.binding == "capital"
    assert any("only $50.00 left" in why for _, why in plan.skipped)


def test_allocate_stops_on_hours_when_money_is_plentiful():
    plan = allocate(_plan_inputs(), bankroll=10_000.0, hours=0.5)  # 30 min = 1 flip
    assert len(plan.bought) == 1
    assert plan.binding == "labor"
    assert plan.hours_used == pytest.approx(25 / 60, abs=1e-2)


def test_running_out_of_deals_is_named_as_the_constraint():
    plan = allocate(_plan_inputs(), bankroll=10_000.0, hours=40.0, min_tier=Tier.SLOW)
    assert plan.binding == "deal flow"
    assert plan.capital_free > 0
    assert "FINDING deals" in plan.summary()


def test_min_tier_gate_keeps_slow_money_out_by_default():
    plan = allocate(_plan_inputs(), bankroll=10_000.0, hours=40.0)
    assert "slow fat" not in [a.title for a in plan.bought]
    assert any("below the GOOD bar" in why for _, why in plan.skipped)


def test_needs_comp_is_skipped_with_a_useful_reason_not_a_tier_insult():
    a = score_candidate(Candidate("mystery lamp", 25.0))
    plan = allocate([a], bankroll=100.0)
    assert plan.bought == []
    assert "sold comp" in plan.skipped[0][1]


def test_blended_velocity_weights_by_capital_days_not_by_item():
    """A mean of per-item ratios lets one tiny fast flip hide a big slow one.
    Profit over total capital-days cannot."""
    plan = allocate(_plan_inputs(), bankroll=10_000.0, hours=40.0, min_tier=Tier.SLOW)
    capital_days = sum(a.cost * a.hold_days for a in plan.bought)
    assert plan.blended_per_100_per_day == pytest.approx(
        plan.profit_total / capital_days * 100.0)
    naive = sum(a.per_100_per_day for a in plan.bought) / len(plan.bought)
    assert plan.blended_per_100_per_day < naive


def test_weekly_run_rate_is_the_sum_of_daily_earn_rates():
    plan = allocate(_plan_inputs(), bankroll=10_000.0, hours=40.0, min_tier=Tier.SLOW)
    assert plan.profit_per_week == pytest.approx(
        sum(a.profit_per_day for a in plan.bought) * 7, abs=0.01)


def test_empty_plan_is_a_plan():
    plan = allocate([], bankroll=500.0)
    assert plan.bought == [] and plan.binding == "deal flow"
    assert plan.blended_per_100_per_day is None
    assert "0 buy(s)" in plan.summary()


# --- realized velocity off the ledger ---------------------------------------

LEDGER = [
    {"id": 1, "date": "2026-06-01", "title": "Sansui receiver", "paid": 40.0,
     "status": "sold", "sold_date": "2026-06-20", "profit": 110.0},
    {"id": 2, "date": "2026-05-01", "title": "Gunne Sax dress", "paid": 20.0,
     "status": "sold", "sold_date": "2026-08-01", "profit": 76.5},
    {"id": 3, "date": "2026-08-20", "title": "Dewalt DCD771", "paid": 35.0,
     "status": "open"},
    {"id": 4, "date": "2026-03-02", "title": "Lego bulk lot", "paid": 90.0,
     "status": "open"},
]


def test_realized_velocity_divides_profit_by_capital_days():
    r = realized_velocity(ledger_entries=LEDGER, today="2026-08-26")
    assert [f.hold_days for f in r.flips] == [19, 92]
    assert r.capital_days == pytest.approx(40 * 19 + 20 * 92)
    assert r.profit == pytest.approx(186.5)
    assert r.per_100_per_day == pytest.approx(186.5 / (40 * 19 + 20 * 92) * 100)
    assert r.avg_hold_days == pytest.approx((19 + 92) / 2)


def test_realized_velocity_flags_capital_that_has_stopped_working():
    r = realized_velocity(ledger_entries=LEDGER, today="2026-08-26", stale_days=60)
    assert r.parked_capital == pytest.approx(125.0)
    assert [p.id for p in r.stale] == [4]            # 177 days old
    assert "Lego bulk lot" in r.report()
    assert "$0.00/day" in r.report()


def test_same_day_flip_is_charged_one_day_not_zero():
    rows = [{"id": 1, "date": "2026-06-01", "title": "quick", "paid": 10.0,
             "status": "sold", "sold_date": "2026-06-01", "profit": 5.0}]
    r = realized_velocity(ledger_entries=rows, today="2026-06-02")
    assert r.flips[0].hold_days == 1
    assert r.per_100_per_day == pytest.approx(50.0)


def test_empty_ledger_tells_you_how_to_start():
    r = realized_velocity(ledger_entries=[], today="2026-08-26")
    assert r.per_100_per_day is None
    assert "flipscout bought" in r.report()


def test_unsold_ledger_only_still_reports_parked_capital():
    r = realized_velocity(ledger_entries=LEDGER[2:], today="2026-08-26")
    assert r.flips == []
    assert "nothing closed yet" in r.report()
    assert r.parked_capital == pytest.approx(125.0)


def test_garbage_dates_are_ignored_rather_than_crashing():
    rows = [{"id": 1, "date": "not-a-date", "title": "x", "paid": 5.0, "status": "open"}]
    r = realized_velocity(ledger_entries=rows, today="2026-08-26")
    assert r.open_positions == []


# --- the plumbing back into the analyzer ------------------------------------

def test_candidate_days_to_sell_beats_the_supply_demand_estimate():
    # counts alone say ~45 days; you say 7 because the last three sold in a week.
    c = Candidate("t", 40, observed_price=95, sold_count=800, active_count=400,
                  days_to_sell=7)
    assert analyze(c).days_to_sell == 7
    assert analyze(Candidate("t", 40, observed_price=95, sold_count=800,
                             active_count=400)).days_to_sell == pytest.approx(45.0)


def test_csv_reads_the_optional_days_to_sell_column(tmp_path):
    p = tmp_path / "c.csv"
    p.write_text("title,source_price,observed_price,days_to_sell\n"
                 "widget,10,40,6\nnodays,10,40,\n", encoding="utf-8")
    a, b = candidates_from_csv(str(p))
    assert a.days_to_sell == 6
    assert b.days_to_sell is None


def test_score_uses_the_deals_own_estimate_when_none_is_passed():
    deal = analyze(Candidate("t", 40, observed_price=95, sold_count=900,
                             active_count=90))
    a = score(deal)
    assert a.days_assumed is False
    assert a.days_to_sell == pytest.approx(deal.days_to_sell)


def test_fee_model_flows_through_to_velocity():
    cheap = score_candidate(Candidate("t", 40, observed_price=95),
                            fees=FeeModel(final_value_pct=0.05), days_to_sell=9)
    dear = score_candidate(Candidate("t", 40, observed_price=95),
                           fees=FeeModel(final_value_pct=0.25), days_to_sell=9)
    assert cheap.velocity > dear.velocity


# --- CLI --------------------------------------------------------------------

def test_cli_velocity_prints_the_readout_and_exits_zero_on_a_good_slot(capsys):
    from flipscout.cli import main
    rc = main(["velocity", "Switch OLED", "--buy", "120", "--sold", "250",
               "--days-to-sell", "9"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "per $100 per day" in out and "HOT" in out
    assert "PAY <=" in out


def test_cli_velocity_exit_codes_separate_slow_money_from_no_comp(capsys):
    from flipscout.cli import main
    assert main(["velocity", "brick", "--buy", "200", "--sold", "230",
                 "--days-to-sell", "300"]) == 1
    assert main(["velocity", "mystery", "--buy", "20"]) == 2


def test_cli_portfolio_spends_a_bankroll(tmp_path, capsys):
    from flipscout.cli import main
    p = tmp_path / "c.csv"
    p.write_text("title,source_price,observed_price,days_to_sell\n"
                 "fast,20,60,7\nslow,200,380,200\n", encoding="utf-8")
    rc = main(["portfolio", str(p), "--bankroll", "100", "--hours", "8"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "fast" in out and "BINDING CONSTRAINT" in out


def test_cli_maxpay_velocity_flag_adds_the_time_aware_ceiling(capsys):
    from flipscout.cli import main
    rc = main(["maxpay", "--sold", "95", "--velocity", "1.0", "--days-to-sell", "9"])
    out = capsys.readouterr().out
    assert rc == 0
    assert out.count("PAY <=") == 2       # the ROI ceiling and the velocity one
    assert "/$100/day" in out


def test_cli_turns_reads_the_ledger(tmp_path, monkeypatch, capsys):
    import json
    from flipscout.cli import main
    p = tmp_path / "ledger.json"
    p.write_text(json.dumps(LEDGER), encoding="utf-8")
    monkeypatch.setenv("FLIPSCOUT_LEDGER_FILE", str(p))
    assert main(["turns"]) == 0
    assert "REALIZED VELOCITY" in capsys.readouterr().out


def test_a_free_item_is_bounded_by_your_time_not_your_money():
    a = _scored(buy=0.0, sold=95.0, days=9)
    assert a.velocity == float("inf")
    assert a.tier is Tier.HOT
    assert any("$0 at risk" in n for n in a.notes)
    a.summary(); a.detail()          # infinities must not blow up the renderers


def test_a_free_item_that_still_loses_money_is_dead():
    a = _scored(buy=0.0, sold=1.0, days=9, ship=20.0)
    assert a.deal.net_profit < 0
    assert a.tier is Tier.DEAD
