"""Unit tests for the HTML message builders.

Every user-facing message must be valid Telegram HTML with escaped dynamic
content.  These tests need no database.
"""

from html.parser import HTMLParser

from utils.messages import balance, format_duration, game_cooldown, leaderboard, profile, stock_list


class _HtmlValidator(HTMLParser):
    """Fail when the markup is malformed or raw < / > leak into text."""

    def __init__(self):
        super().__init__(convert_charrefs=False)
        self.valid = True

    def error(self, message):
        self.valid = False

    def handle_data(self, data):
        if "<" in data or ">" in data:
            self.valid = False


def _assert_safe_html(text: str) -> None:
    parser = _HtmlValidator()
    try:
        parser.feed(text)
        parser.close()
    except Exception:
        parser.valid = False
    assert parser.valid, f"invalid HTML produced: {text!r}"


def test_profile_escapes_username():
    user = {
        "user_id": 1,
        "username": "<script>x",
        "first_name": "A & B",
        "wallet": 1000,
        "bank": 2000,
        "stocks_value": 0,
        "total_earned": 0,
        "total_spent": 0,
    }
    text = profile(user)
    assert "<script>" not in text
    _assert_safe_html(text)


def test_balance_safe_html():
    user = {"user_id": 1, "username": "user", "wallet": 100, "bank": 200}
    text = balance(user, user)
    assert "<b>💰 BALANCE</b>" in text
    _assert_safe_html(text)


def test_leaderboard_safe_html():
    entries = [(1, "user<&>", 5000), (2, "second", 3000)]
    text = leaderboard(entries)
    _assert_safe_html(text)


def test_stock_list_safe_html():
    assets = [{"symbol": "BTC", "price": 1000, "change_percent": 3.21}]
    text = stock_list(assets)
    _assert_safe_html(text)
    assert "₹" in text


def test_format_duration():
    assert format_duration(0) == "0s"
    assert format_duration(45) == "45s"
    assert format_duration(90) == "1m 30s"
    assert format_duration(3725) == "1h 2m 5s"
    assert format_duration(90061) == "1d 1h 1m"


def test_game_cooldown_readable_timer():
    text = game_cooldown("fly", 3725)
    assert "1h 2m 5s" in text
    assert "3725s" not in text
    _assert_safe_html(text)
