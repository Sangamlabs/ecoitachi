"""Unit tests for validators and mines multiplier math (pure logic)."""

from games.mines import _auto_table, multiplier_after, payout_for
from utils.validators import (
    is_safe_multiplier,
    is_safe_percent,
    is_safe_probability,
    parse_user_id,
    validate_min_max,
)


class TestValidators:
    def test_probability(self):
        assert is_safe_probability(0.5)
        assert is_safe_probability(1.0)
        assert not is_safe_probability(1.1)
        assert not is_safe_probability(-0.1)

    def test_percent(self):
        assert is_safe_percent(100.0)
        assert not is_safe_percent(101)

    def test_multiplier(self):
        assert is_safe_multiplier(5.0)
        assert not is_safe_multiplier(-1)
        assert not is_safe_multiplier(float("nan"))

    def test_min_max(self):
        assert validate_min_max(1, 5)
        assert not validate_min_max(5, 1)

    def test_parse_user_id(self):
        assert parse_user_id("123456") == 123456
        assert parse_user_id("@username") == -1
        assert parse_user_id("abc") is None


class TestMinesMath:
    def test_table_increases_with_reveals(self):
        table = _auto_table(5)
        for a, b in zip(table, table[1:]):
            assert a < b, f"multiplier must grow with reveals, got {a} >= {b}"

    def test_table_starts_above_one(self):
        assert _auto_table(5)[0] > 1.0

    def test_table_multiplier_after(self):
        table = _auto_table(5)
        assert multiplier_after(0, table) == 1.0
        assert multiplier_after(1, table) == table[0]
        assert multiplier_after(10, table) == table[9]

    def test_payout_is_integer(self):
        assert payout_for(1000, 3, _auto_table(5)) >= 1000

    def test_more_bombs_pays_more(self):
        table_5 = _auto_table(5)
        table_10 = _auto_table(10)
        assert table_10[0] > table_5[0]
