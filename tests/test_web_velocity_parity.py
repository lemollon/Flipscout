"""The web app claims, in the README, to run "the exact same math as the CLI".

For fees and ROI that claim is old and eyeballed. For capital velocity it is
new, and velocity is exactly the kind of arithmetic that drifts silently: one
side forgets the payout hold, both keep producing plausible numbers, and the
phone tells you to buy something the terminal would have refused.

So this pins it. The JS is lifted straight out of web/index.html — the real
shipped file, not a copy — and run under node against the same cases as
flipscout.velocity. Skipped (not failed) where node isn't installed, since the
Python side stands on its own.
"""

import json
import os
import re
import shutil
import subprocess

import pytest

from flipscout.analyzer import Candidate
from flipscout.fees import FeeModel
from flipscout.velocity import CycleModel, VelocityThresholds, score_candidate

WEB = os.path.join(os.path.dirname(__file__), os.pardir, "web", "index.html")
pytestmark = pytest.mark.skipif(shutil.which("node") is None,
                                reason="node isn't installed; the Python side is tested directly")

# The settings object the JS math runs on, mirroring the CLI defaults.
JS_SETTINGS = {
    "fvf": 0.1325, "perOrder": 0.40, "perOrderSmall": 0.30, "smallThreshold": 10.0,
    "intl": 0.0, "promoted": 0.0, "taxPct": 0.0,
    "minProfit": 10.0, "minRoi": 0.50,
    "prepDays": 2, "shipDays": 3, "payoutDays": 2, "assumeDays": 45, "handleMin": 25,
    "velHot": 2.00, "velGood": 0.75, "velSlow": 0.25, "minHourly": 20,
}

CASES = [
    # buy, sold, shipCost, extra, daysToSell, soldCount, activeCount
    {"buy": 120, "sold": 250, "shipCost": 12, "extra": 0, "daysToSell": 9},
    {"buy": 40, "sold": 95, "shipCost": 0, "extra": 0, "daysToSell": 21},
    {"buy": 400, "sold": 680, "shipCost": 25, "extra": 5, "daysToSell": 240},
    {"buy": 8, "sold": 32, "shipCost": 6, "extra": 0, "daysToSell": 14},
    {"buy": 2, "sold": 8, "shipCost": 0, "extra": 0, "daysToSell": 1},
    {"buy": 90, "sold": 95, "shipCost": 18, "extra": 0, "daysToSell": 3},
    # no days-to-sell at all -> both sides must reach for the same assumption
    {"buy": 40, "sold": 95, "shipCost": 0, "extra": 0, "daysToSell": None},
    # days derived from the sold/active counts rather than given
    {"buy": 40, "sold": 95, "shipCost": 0, "extra": 0, "daysToSell": None,
     "soldCount": 800, "activeCount": 400},
]


def _js_function(src: str, name: str) -> str:
    """Lift one top-level function out of the page, verbatim."""
    m = re.search(r"\nfunction " + re.escape(name) + r"\(.*?\n\}\n", src, re.S)
    assert m, f"web/index.html no longer defines {name}() — did the math move?"
    return m.group(0)


def _harness() -> str:
    with open(WEB, encoding="utf-8") as f:
        page = f.read()
    money = re.search(r"\nconst \$money = .*", page)
    assert money, "web/index.html no longer defines $money"
    parts = [money.group(0)]
    for name in ("netProceeds", "estDaysToSell", "holdDays", "velocityOf",
                 "maxPayVelocity"):
        parts.append(_js_function(page, name))
    return "\n".join(parts)


def _run_js(cases, settings):
    script = _harness() + """
const SET = %s, CASES = %s;
const out = CASES.map(o => {
  const np = netProceeds(o.sold, SET, o.shipCost||0, 0);
  const cost = (o.buy||0)+(o.shipCost||0)+(o.extra||0);
  const profit = np.net - (o.buy||0) - (o.extra||0);
  const a = { verdict:"BUY", profit, roi: cost>0?profit/cost:Infinity };
  const v = velocityOf(a, o, SET);
  const mv = maxPayVelocity({sold:o.sold, shipCost:o.shipCost, extra:o.extra},
                            SET, o.daysToSell, SET.velGood);
  return { profit, hold:v.hold, per100:v.per100, turns:v.turns,
           hourly:v.hourly, tier:v.tier, assumed:v.assumed,
           maxPrice:mv.maxPrice, requiredRoi:mv.requiredRoi };
});
console.log(JSON.stringify(out));
""" % (json.dumps(settings), json.dumps(cases))
    r = subprocess.run(["node", "-e", script], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout)


def _python_side(case):
    cycle = CycleModel(prep_days=2, ship_days=3, payout_days=2,
                       default_days_to_sell=45, handle_minutes=25)
    vt = VelocityThresholds(hot=2.00, good=0.75, slow=0.25,
                            min_profit=10.0, min_hourly=20.0)
    cand = Candidate(
        title="t", source_price=case["buy"], observed_price=case["sold"],
        shipping_cost=case.get("shipCost", 0), extra_cost=case.get("extra", 0),
        sold_count=case.get("soldCount"), active_count=case.get("activeCount"),
        days_to_sell=case.get("daysToSell"),
    )
    return score_candidate(cand, fees=FeeModel(), cycle=cycle,
                           velocity_thresholds=vt)


def test_the_web_app_and_the_cli_agree_on_every_velocity_number():
    js_all = _run_js(CASES, JS_SETTINGS)
    for case, js in zip(CASES, js_all):
        py = _python_side(case)
        where = f"case {case}"
        assert js["profit"] == pytest.approx(py.deal.net_profit, abs=1e-6), where
        assert js["hold"] == pytest.approx(py.hold_days, abs=1e-6), where
        assert js["per100"] == pytest.approx(py.per_100_per_day, abs=1e-6), where
        assert js["turns"] == pytest.approx(py.turns_per_year, abs=1e-6), where
        assert js["hourly"] == pytest.approx(py.hourly, abs=1e-6), where
        assert js["assumed"] is py.days_assumed, where
        assert js["tier"] == py.tier.value, where


def test_the_two_sides_agree_on_the_velocity_ceiling():
    from flipscout.velocity import max_pay_for_velocity

    js_all = _run_js(CASES, JS_SETTINGS)
    cycle = CycleModel(prep_days=2, ship_days=3, payout_days=2,
                       default_days_to_sell=45, handle_minutes=25)
    for case, js in zip(CASES, js_all):
        m = max_pay_for_velocity(
            sale_price=case["sold"], days_to_sell=case.get("daysToSell"),
            target_per_100_per_day=0.75, cycle=cycle,
            thresholds=VelocityThresholds(min_profit=10.0),
            shipping_cost=case.get("shipCost", 0), extra_cost=case.get("extra", 0),
        )
        assert js["maxPrice"] == pytest.approx(m.max_price, abs=1e-6), case
        assert js["requiredRoi"] == pytest.approx(m.required_roi, abs=1e-6), case


def test_the_page_still_ships_the_velocity_defaults_the_python_uses():
    """A knob that exists on only one side is the other way this drifts."""
    with open(WEB, encoding="utf-8") as f:
        page = f.read()
    c, t = CycleModel(), VelocityThresholds()
    for js_key, value in [("prepDays", c.prep_days), ("shipDays", c.ship_days),
                          ("payoutDays", c.payout_days),
                          ("assumeDays", c.default_days_to_sell),
                          ("handleMin", c.handle_minutes),
                          ("velHot", t.hot), ("velGood", t.good),
                          ("velSlow", t.slow), ("minHourly", t.min_hourly)]:
        m = re.search(js_key + r":\s*([0-9.]+)", page)
        assert m, f"web/index.html has no {js_key} default"
        assert float(m.group(1)) == pytest.approx(value), js_key
