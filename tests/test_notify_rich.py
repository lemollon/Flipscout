"""Tests for image+link (embed) alerts."""

from flipscout.notify import VERDICT_COLORS, build_embed, notify_rich


class FakeResp:
    def raise_for_status(self):
        pass


class FakeSession:
    def __init__(self):
        self.calls = []

    def post(self, url, json=None, timeout=None):
        self.calls.append(json)
        return FakeResp()


CAND = {
    "title": "Milwaukee M18 FUEL 2-Tool Brushless Kit",
    "url": "https://www.ebay.com/itm/358816746510",
    "image": "https://i.ebayimg.com/images/g/CW0AAeSwhPFqD7AO/s-l1600.jpg",
    "verdict": "pass",
    "reason": "Bare tools, no batteries/charger.",
    "all_in": 103.26, "comp": 265.74, "max_bid": 170.13, "bids": 0, "ends": "23h",
}


def test_embed_carries_link_and_image():
    e = build_embed(CAND)
    assert e["url"] == CAND["url"]
    assert e["thumbnail"]["url"] == CAND["image"]
    assert e["color"] == VERDICT_COLORS["pass"]


def test_embed_fields_are_labelled_money():
    vals = {f["name"]: f["value"] for f in build_embed(CAND)["fields"]}
    assert vals["Costs now"] == "$103.26"
    assert vals["Sells for"] == "$265.74"
    assert vals["MAX bid (never exceed)"] == "$170.13"
    assert "batteries" in vals["Verdict"]


def test_no_room_is_stated_not_a_negative_number():
    e = build_embed({**CAND, "max_bid": -5.0})
    assert {f["name"]: f["value"] for f in e["fields"]}["MAX bid (never exceed)"] == "no room"


def test_missing_fields_are_omitted_not_crashed():
    e = build_embed({"title": "x", "verdict": "buy"})
    assert e["fields"] == []
    assert "url" not in e and "thumbnail" not in e


def test_notify_rich_posts_embeds():
    s = FakeSession()
    sent = notify_rich([CAND], content="hi", env={"FLIPSCOUT_ALERT_WEBHOOK": "http://x"}, session=s)
    assert sent == ["webhook"]
    assert s.calls[0]["content"] == "hi"
    assert len(s.calls[0]["embeds"]) == 1


def test_discord_ten_embed_cap_is_chunked():
    s = FakeSession()
    notify_rich([CAND] * 23, env={"FLIPSCOUT_ALERT_WEBHOOK": "http://x"}, session=s)
    assert [len(c["embeds"]) for c in s.calls] == [10, 10, 3]


def test_no_webhook_falls_back_to_printing(capsys):
    sent = notify_rich([CAND], content="digest", env={})
    assert sent == []
    assert "ebay.com/itm" in capsys.readouterr().out


def test_dead_webhook_is_fail_soft():
    class Boom(FakeSession):
        def post(self, *a, **k):
            raise RuntimeError("503")
    assert notify_rich([CAND], env={"FLIPSCOUT_ALERT_WEBHOOK": "http://x"}, session=Boom()) == []


def test_embed_carries_both_the_buy_link_and_the_comps_link():
    """Every alert must let you verify the 'sells for more' claim yourself."""
    from flipscout.notify import build_embed
    e = build_embed({**CAND,
                     "buy_url": "https://shopgoodwill.com/item/9",
                     "comps_url": "https://www.ebay.com/sch/i.html?_nkw=ipod&LH_Sold=1"})
    links = {f["name"]: f["value"] for f in e["fields"]}["Links"]
    assert "shopgoodwill.com/item/9" in links
    assert "LH_Sold=1" in links
    assert "Buy it here" in links and "sold for on eBay" in links
