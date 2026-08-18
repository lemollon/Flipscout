"""Tests for image+link (embed) alerts."""

from flipscout.notify import VERDICT_COLORS, build_embed, notify_rich


class FakeResp:
    def __init__(self, mid="1"):
        self._mid = mid

    def raise_for_status(self):
        pass

    def json(self):
        return {"id": self._mid}


class FakeSession:
    def __init__(self):
        self.calls = []

    def post(self, url, json=None, timeout=None, params=None):
        self.calls.append(json)
        return FakeResp()

    def put(self, url, headers=None, timeout=None):
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
    assert e["image"]["url"] == CAND["image"]
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
    # Only the copy-paste name field survives - everything else stays omitted.
    assert [f["name"] for f in e["fields"]] == ["\U0001F4CB Copy-paste name"]
    assert "url" not in e and "thumbnail" not in e


def test_notify_rich_posts_embeds():
    s = FakeSession()
    sent = notify_rich([CAND], content="hi", env={"FLIPSCOUT_ALERT_WEBHOOK": "http://x"}, session=s)
    assert sent == ["webhook", "webhook"]      # header, then the card
    assert s.calls[0]["content"] == "hi"
    assert len(s.calls[1]["embeds"]) == 1


def test_every_deal_gets_its_own_message():
    """🚨 A reaction belongs to a MESSAGE, so a digest carrying ten embeds
    cannot say which deal you meant - arming just took the first link it found.

    Measured against the live channel on 2026-08-18: all 25 recent alerts were
    multi-deal, which means arming had never once worked.
    """
    s = FakeSession()
    notify_rich([CAND] * 23, env={"FLIPSCOUT_ALERT_WEBHOOK": "http://x"}, session=s)
    assert len(s.calls) == 23
    assert all(len(c["embeds"]) == 1 for c in s.calls)


def test_the_tap_targets_are_seeded_on_each_card():
    """The bot puts 🎯 and ❌ under every card so arming is ONE TAP - that is
    as close to a button as a webhook can get."""
    seen = []

    class S(FakeSession):
        def put(self, url, headers=None, timeout=None):
            seen.append(url)
            return FakeResp()

    notify_rich([CAND, CAND], env={"FLIPSCOUT_ALERT_WEBHOOK": "http://x",
                                   "FLIPSCOUT_DISCORD_BOT_TOKEN": "t",
                                   "FLIPSCOUT_DISCORD_CHANNEL_ID": "9"}, session=S())
    assert len(seen) == 4                       # two emoji on each of two cards
    assert all(u.endswith("/@me") for u in seen)


def test_a_missing_bot_token_never_blocks_the_alert():
    """Seeding is a convenience; the card itself is the product."""
    s = FakeSession()
    sent = notify_rich([CAND], env={"FLIPSCOUT_ALERT_WEBHOOK": "http://x"}, session=s)
    assert sent == ["webhook"]


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


def test_failure_message_includes_the_discord_response_body(capsys):
    """Discord explains itself (unknown webhook / rate limit); that text IS the
    diagnosis, so it must reach the logs."""
    import requests

    class Resp:
        status_code = 404
        text = '{"message": "Unknown Webhook", "code": 10015}'

    class Boom:
        def post(self, *a, **k):
            err = requests.HTTPError("404 Client Error")
            err.response = Resp()
            raise err

    assert notify_rich([CAND], env={"FLIPSCOUT_ALERT_WEBHOOK": "http://x"}, session=Boom()) == []
    out = capsys.readouterr().out
    assert "Unknown Webhook" in out and "HTTP 404" in out


def test_photo_is_full_width_by_default(monkeypatch):
    """Condition is most of the buy decision and can't be judged from an 80px
    corner crop."""
    from flipscout.notify import build_embed
    monkeypatch.delenv("FLIPSCOUT_SMALL_IMAGES", raising=False)
    e = build_embed(CAND)
    assert e["image"]["url"] == CAND["image"]
    assert "thumbnail" not in e


def test_small_images_can_be_opted_into(monkeypatch):
    from flipscout.notify import build_embed
    monkeypatch.setenv("FLIPSCOUT_SMALL_IMAGES", "1")
    e = build_embed(CAND)
    assert e["thumbnail"]["url"] == CAND["image"]
    assert "image" not in e


# --- copy-paste name (2026-08-13) --------------------------------------------
# The embed title is Discord's ONLY clickable link - and Discord gives no way
# to copy text out of a link, so the raw name has to exist somewhere as plain
# text too.

def test_embed_carries_the_title_as_copyable_inline_code():
    e = build_embed(CAND)
    vals = {f["name"]: f["value"] for f in e["fields"]}
    assert vals["\U0001F4CB Copy-paste name"] == f"`{CAND['title']}`"
    # It rides on its own row, not squeezed into the money grid.
    assert next(f for f in e["fields"]
                if f["name"] == "\U0001F4CB Copy-paste name")["inline"] is False


def test_copy_paste_name_is_truncated_not_field_limit_busted():
    long_title = "Craftsman " + "Professional " * 100 + "Socket Set"
    assert len(long_title) > 1024
    e = build_embed({**CAND, "title": long_title})
    value = {f["name"]: f["value"] for f in e["fields"]}["\U0001F4CB Copy-paste name"]
    assert len(value) < 1024
    assert value == f"`{long_title[:150]}`"


def test_copy_paste_name_field_is_absent_when_there_is_no_title():
    e = build_embed({"verdict": "buy"})
    assert e["fields"] == []


def test_overlong_content_is_truncated_not_rejected():
    """Discord 400s the ENTIRE message when content > 2000 chars - a caller
    composing header + digest hit this live on 2026-07-28 and the whole
    delivery silently died. Truncate instead."""
    import flipscout.notify as notify
    posted = {}

    class Sess:
        def post(self, url, json=None, timeout=None):
            posted.update(json)
            class R:
                def raise_for_status(self):
                    assert len(json["content"]) <= 2000
            return R()

    sent = notify.notify_rich([], content="x" * 5000,
                              env={"FLIPSCOUT_ALERT_WEBHOOK": "https://h"},
                              session=Sess())
    assert sent == ["webhook"]
    assert len(posted["content"]) <= 2000
