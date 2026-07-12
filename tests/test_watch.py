"""Tests for the always-on watcher: digest formatting, channel dispatch, config
parsing, and alert-on-new-only dedup — all offline."""

import pytest

from flipscout.scanner import ScanHit
from flipscout.notify import format_digest, notify
from flipscout.watch import load_config, run_watch


def _hit(profit, per_hour, url="u1", source="ebay", title="DeWalt drill"):
    return ScanHit(query="q", title=title, buy_price=60.0, sold_price=200.0,
                   profit=profit, roi=1.0, per_hour=per_hour, url=url, source=source)


# --- digest + notify --------------------------------------------------------

def test_format_digest():
    text = format_digest([_hit(113, 339)])
    assert "1 new deal" in text
    assert "$339/hr" in text and "DeWalt drill" in text and "u1" in text


def test_notify_webhook(monkeypatch):
    calls = {}
    class _Sess:
        def post(self, url, json=None, timeout=None):
            calls["url"] = url; calls["json"] = json
            class R:
                def raise_for_status(self): pass
            return R()
    env = {"FLIPSCOUT_ALERT_WEBHOOK": "https://hook"}
    sent = notify("hello", env=env, session=_Sess())
    assert sent == ["webhook"]
    assert calls["json"]["content"] == "hello" and calls["json"]["text"] == "hello"


def test_notify_none_configured_prints(capsys):
    sent = notify("hi", env={})
    assert sent == []
    assert "hi" in capsys.readouterr().out


# --- config -----------------------------------------------------------------

def test_load_config_parses_watchlist():
    cfg = load_config({"FLIPSCOUT_WATCHLIST": "dewalt drill\nsansui receiver, canon powershot",
                       "FLIPSCOUT_MIN_PROFIT": "25", "FLIPSCOUT_SOURCES": "ebay,goodwill"})
    assert cfg["queries"] == ["dewalt drill", "sansui receiver", "canon powershot"]
    assert cfg["min_profit"] == 25.0
    assert cfg["sources"] == ["ebay", "goodwill"]


# --- run_watch: alert on NEW deals only -------------------------------------

class _FakeSource:
    name = "ebay"
    def __init__(self, sold, listings): self._sold = sold; self._l = listings
    class _Comp:
        def __init__(s, sold): s.sold_price = sold; s.source = "ebay"
    def lookup(self, q, observed_price=None): return self._Comp(self._sold)
    def active_listings(self, q, limit=50, **kw): return self._l


def test_run_watch_alerts_then_dedupes(tmp_path):
    state = str(tmp_path / "seen.json")
    src = _FakeSource(sold=200.0, listings=[
        {"title": "cheap DeWalt", "price": 60.0, "url": "u1"}])
    cfg = load_config({"FLIPSCOUT_WATCHLIST": "dewalt", "FLIPSCOUT_MIN_PROFIT": "15",
                       "FLIPSCOUT_MIN_ROI": "0.5", "FLIPSCOUT_STATE_FILE": state})
    sent = []
    def notifier(text): sent.append(text); return ["webhook"]

    r1 = run_watch(cfg, ebay=src, notifier=notifier)
    assert r1["new"] == 1 and r1["sent"] == ["webhook"]          # first run alerts

    r2 = run_watch(cfg, ebay=src, notifier=notifier)
    assert r2["new"] == 0                                        # same item -> no re-alert
    assert len(sent) == 1


def test_run_watch_empty_watchlist_noop():
    r = run_watch(load_config({"FLIPSCOUT_WATCHLIST": ""}), ebay=object())
    assert r == {"new": 0, "sent": [], "scanned": 0}
