"""The deals board - the qualifying items the web app shows."""

import json

from flipscout import board
from flipscout.bidding import advise
from flipscout.pricebook import match


def _cand(title="Fluke 87V True RMS Multimeter", price=5.0, source="hibid",
          nearby=False, pickup=False, listing_type="auction", **row_extra):
    m = match(title)
    assert m is not None, f"fixture title no longer matches the book: {title}"
    row = {"source": source, "id": "1", "title": title, "url": "http://x/1",
           "image": "http://img/1.jpg", "price": price, "min_bid": price + 1,
           "increment": 1.0, "bids": 0, "handling": 0.0, "ends": "2026-07-28",
           "listing_type": listing_type, "nearby": nearby, "local": nearby,
           "pickup_risk": pickup, "house": "King's Auction", "city": "Katy",
           "state": "TX", **row_extra}
    adv = advise(m.model.comp, units=m.units, handling=0.0,
                 inbound_shipping=0.0 if nearby else 9.0,
                 outbound_shipping=m.model.outbound_shipping,
                 target_profit=20.0, current_price=price, min_bid=price + 1,
                 increment=1.0, bid_count=0)
    return {"row": row, "model": m.model, "match": m, "advice": adv}


def test_board_item_carries_what_you_need_to_act():
    it = board.item(_cand())
    # what it is + the evidence
    assert it["model"] and it["comp"] > 0 and it["comps_url"].startswith("http")
    # what to do
    assert it["max_bid"] > 0 and it["open_bid"] is not None
    # where to do it
    assert it["url"] == "http://x/1"
    assert it["house"] == "King's Auction" and it["where"] == "Katy, TX"


def test_board_reports_nearby_and_pickup_flags():
    near = board.item(_cand(nearby=True, pickup=True))
    assert near["nearby"] is True and near["pickup_only"] is True
    far = board.item(_cand())
    assert far["nearby"] is False and far["pickup_only"] is False


def test_build_summarises_the_set():
    b = board.build([_cand(nearby=True), _cand(source="goodwill")])
    assert b["count"] == 2
    assert b["nearby_count"] == 1
    assert b["sources"] == ["goodwill", "hibid"]
    assert b["generated"]


def test_write_then_load_roundtrips(tmp_path):
    p = tmp_path / "sub" / "deals.json"        # directory does not exist yet
    assert board.write([_cand()], str(p)) == str(p)
    loaded = board.load(str(p))
    assert loaded["count"] == 1
    assert loaded["items"][0]["max_bid"] > 0
    assert json.loads(p.read_text(encoding="utf-8"))["count"] == 1


def test_load_of_a_missing_board_is_empty_not_an_error():
    b = board.load("does/not/exist.json")
    assert b["count"] == 0 and b["items"] == []


def test_fixed_price_rows_keep_their_type():
    """The page words these differently - "Asking / Don't pay over" rather than
    "Open at / MAX bid" - so the type has to survive into the board."""
    it = board.item(_cand(source="craigslist", listing_type="fixed", nearby=True))
    assert it["listing_type"] == "fixed"


def test_a_block_does_not_blank_the_board(tmp_path, monkeypatch):
    """If every source fails, run() returns before publishing, so the last good
    board stays on disk. Blanking it would look identical to "no deals"."""
    from flipscout import hunt
    p = tmp_path / "deals.json"
    board.write([_cand()], str(p))
    cfg = dict(hunt.load_config({}), board_file=str(p),
               state_file=str(tmp_path / "seen.json"),
               heartbeat_file=str(tmp_path / "hb.json"))

    class Dead:
        name = "dead"

        def search(self, q, limit=40):
            return []

    out = hunt.run(cfg, hunters=[Dead()], notifier=lambda *a, **k: [])
    assert out["blocked"] is True
    assert board.load(str(p))["count"] == 1        # untouched


# --- the daily Discord recap ------------------------------------------------

def test_digest_says_what_is_on_the_board():
    b = board.build([_cand(), _cand(nearby=True, source="goodwill")])
    body = board.digest(b)
    assert "2 item(s) buyable right now" in body
    assert "1 drivable" in body
    assert "max bid" in body
    assert "http://x/1" in body          # clickable straight to the listing


def test_digest_is_empty_when_the_board_is_empty():
    assert board.digest(board.build([])) == ""


def test_digest_fits_in_a_discord_message():
    body = board.digest(board.build([_cand() for _ in range(200)]))
    assert len(body) <= 1900


def test_digest_words_fixed_price_rows_differently():
    b = board.build([_cand(source="craigslist", listing_type="fixed", nearby=True)])
    assert "Asking" in board.digest(b) and "don't pay over" in board.digest(b)


def test_digest_title_is_also_copyable_inline_code():
    """The title in the digest lives inside a [name](url) link - Discord gives
    no way to copy text out of a link, so it must also appear as plain,
    selectable inline code."""
    b = board.build([_cand(title="Fluke 87V True RMS Multimeter")])
    body = board.digest(b)
    assert "`Fluke 87V True RMS Multimeter`" in body
    assert "[Fluke 87V True RMS Multimeter]" in body   # the link stays too


def test_digest_copy_paste_title_is_truncated():
    long_title = "Fluke 87V True RMS Multimeter " + "Professional " * 20
    b = board.build([_cand(title=long_title)])
    body = board.digest(b)
    assert f"`{long_title[:150]}`" in body


def test_checkin_posts_the_board_instead_of_nothing_new(tmp_path):
    """The old check-in said "you've already been sent all of them", which is
    misleading when the board is full. It should list what's buyable."""
    from flipscout import hunt
    posted = []
    cfg = dict(hunt.load_config({}),
               board_file=str(tmp_path / "b.json"),
               state_file=str(tmp_path / "seen.json"),
               heartbeat_file=str(tmp_path / "hb.json"),
               estate_area="")

    class One:
        name = "goodwill"

        def search(self, q, limit=40):
            return [{"source": "goodwill", "id": "1",
                     "title": "Fluke 87V True RMS Multimeter", "url": "http://x/1",
                     "price": 5.0, "min_bid": 6.0, "increment": 1.0, "bids": 0,
                     "handling": 0.0, "image": "", "ends": ""}]

    # First run alerts it; second finds nothing new -> the check-in fires.
    hunt.run(cfg, hunters=[One()], notifier=lambda a, content="": posted.append(content) or ["webhook"])
    posted.clear()
    hunt.run(cfg, hunters=[One()], notifier=lambda a, content="": posted.append(content) or ["webhook"])
    assert posted, "the daily check-in should still post"
    assert "buyable right now" in posted[0]
    assert "already been sent" not in posted[0]


# --- the full-list page + embed digest (2026-07-28) --------------------------
# "why am i not seeing a list for all 398" - Discord physically can't carry it
# (2000 chars, 10 embeds), so the digest must POINT at a page that can.

def test_write_also_produces_the_browsable_markdown_board(tmp_path):
    p = tmp_path / "docs" / "deals.json"
    assert board.write([_cand()], str(p))
    md = (tmp_path / "docs" / "BOARD.md").read_text(encoding="utf-8")
    assert "Flipscout board - 1 buyable now" in md
    assert "Fluke 87V" in md and "http://x/1" in md
    assert "| Open | Max bid |" in md


def test_markdown_board_lists_every_item_not_a_digest(tmp_path):
    cands = [_cand(row_extra={}) for _ in range(12)]
    for n, c in enumerate(cands):
        c["row"] = {**c["row"], "id": str(n), "title": f"Fluke 87V Multimeter #{n}"}
    md = board.render_markdown(board.build(cands))
    for n in range(12):
        assert f"#{n}" in md, f"item {n} missing from the full list"


def test_digest_links_the_full_board_and_suppresses_unfurls(monkeypatch):
    monkeypatch.setenv("GITHUB_REPOSITORY", "lemollon/Flipscout")
    d = board.digest(board.build([_cand() for _ in range(7)]), top=5)
    # every inline link is wrapped in <> so Discord doesn't stack blank
    # preview cards under the digest
    assert "](<http" in d and "](http://x/1)" not in d
    assert "Full list (all 7)" in d
    assert "<https://github.com/lemollon/Flipscout/blob/main/docs/BOARD.md>" in d


def test_board_url_is_absent_outside_actions(monkeypatch):
    monkeypatch.delenv("GITHUB_REPOSITORY", raising=False)
    assert board.board_page_url() == ""
    assert "Full list" not in board.digest(board.build([_cand()]))


def test_top_items_are_embed_ready_with_our_own_images():
    tops = board.top_items(board.build([_cand() for _ in range(8)]), top=5)
    assert len(tops) == 5
    for t in tops:
        assert t["verdict"] == "buy"
        assert t["buy_url"] == t["url"]
        assert t["image"]          # the whole point: our photo, not an unfurl
        assert t["open_bid"] is not None and t["max_bid"] is not None


def test_checkin_sends_top_rows_as_embeds(tmp_path, monkeypatch):
    from flipscout import hunt
    posts = []
    monkeypatch.setattr(hunt, "_load_seen", lambda p, **k: {"hibid:1"})
    monkeypatch.setattr(hunt, "_save_seen", lambda *a: None)

    class H:
        name = "hibid"
        def search(self, q, limit=40):
            return [{"source": "hibid", "id": "1",
                     "title": "Fluke 87V True RMS Multimeter", "url": "u",
                     "price": 5.0, "min_bid": 6.0, "increment": 1.0, "bids": 0,
                     "handling": 0.0, "image": "http://img/1.jpg", "ends": ""}]
        def enrich(self, row):
            return row

    def notifier(alerts, content="", **k):
        posts.append((alerts, content))
        return ["webhook"]

    cfg = {"sources": ["hibid"], "target_profit": 20.0, "inbound_shipping": 9.0,
           "top": 10, "state_file": "nonexistent.json",
           "heartbeat_file": str(tmp_path / "hb.json")}
    hunt.run(cfg, hunters=[H()], notifier=notifier)
    alerts, content = posts[0]
    assert "buyable right now" in content
    assert alerts and alerts[0]["image"] == "http://img/1.jpg"
    assert alerts[0]["verdict"] == "buy"
