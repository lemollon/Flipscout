"""The purchase ledger: realized P&L must be checkable against the book."""

import json

from flipscout import ledger


def test_buy_pins_the_comp_at_buy_time(tmp_path):
    p = str(tmp_path / "ledger.json")
    e = ledger.record_buy("Gunne Sax by Jessica McClintock Prairie Dress",
                          paid=21.50, source="goodwill", path=p)
    assert e["id"] == 1 and e["model"] == "gunne_sax"
    assert e["comp_at_buy"] == 122.00          # pinned, not re-read later
    assert e["status"] == "open"


def test_unbooked_purchases_are_recorded_but_flagged(tmp_path):
    p = str(tmp_path / "ledger.json")
    e = ledger.record_buy("Random Yard Sale Vase", paid=5.0, path=p)
    assert e["model"] is None and e["comp_at_buy"] is None
    assert "not in book" in e["model_label"].lower()


def test_sale_uses_the_same_fee_model_as_the_alerts(tmp_path):
    from flipscout.fees import FeeModel, net_proceeds
    p = str(tmp_path / "ledger.json")
    e = ledger.record_buy("TI-84 Plus CE Graphing Calculator", paid=15.0, path=p)
    s = ledger.record_sale(e["id"], gross=56.37, shipping=5.0, path=p)
    expect = net_proceeds(56.37, fees=FeeModel(), shipping_cost=5.0).net
    assert s["net"] == round(expect, 2)
    assert s["profit"] == round(expect - 15.0, 2)
    assert s["status"] == "sold"


def test_sale_of_unknown_id_fails_softly(tmp_path):
    assert ledger.record_sale(99, gross=10.0,
                              path=str(tmp_path / "ledger.json")) is None


def test_pnl_reports_drift_when_realizing_under_comp(tmp_path):
    p = str(tmp_path / "ledger.json")
    e = ledger.record_buy("Singer Featherweight 221 Sewing Machine w/ Case",
                          paid=60.0, path=p)
    # sold at 60% of the $200 comp -> the drift line must fire
    ledger.record_sale(e["id"], gross=120.0, shipping=14.0, path=p)
    out = ledger.pnl(path=p)
    assert "re-measure this model" in out
    assert "Singer Featherweight" in out


def test_pnl_empty_ledger_explains_itself(tmp_path):
    assert "flipscout bought" in ledger.pnl(path=str(tmp_path / "nope.json"))


def test_cli_wires_the_three_commands(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("FLIPSCOUT_LEDGER_FILE", str(tmp_path / "ledger.json"))
    from flipscout.cli import main
    assert main(["bought", "TI-84 Plus CE Calculator", "--paid", "12", "--source", "goodwill"]) == 0
    out = capsys.readouterr().out
    assert "ledger #1" in out and "TI-84 Plus CE" in out
    assert main(["sold", "1", "--gross", "56.37", "--shipping", "5"]) == 0
    assert "profit" in capsys.readouterr().out
    assert main(["pnl"]) == 0
    assert "1 sold" in capsys.readouterr().out
