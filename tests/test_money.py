"""Unit tests for the centralized money utilities.

Money is always an integer count of sub-units (₹1 = 100 units) - floats are
never used internally.  These tests need no database.
"""

import pytest

from utils.money import (
    MoneyError,
    add,
    check_balance,
    format_money,
    is_positive,
    is_valid_amount,
    multiply,
    parse_amount,
    percentage,
    subtract,
)


class TestParseAmount:
    @pytest.mark.parametrize(
        "raw,expected",
        [
            ("500", 50_000),
            ("10.50", 1_050),
            ("1,000.25", 100_025),
            ("0.01", 1),
            ("₹500", 50_000),
            ("1,000", 100_000),
            ("0", 0),
        ],
    )
    def test_valid(self, raw, expected):
        assert parse_amount(raw) == expected

    @pytest.mark.parametrize("raw", ["", "abc", "-5", "1.234", "NaN", "inf", "5$", None])
    def test_invalid(self, raw):
        with pytest.raises(MoneyError):
            parse_amount(raw)

    def test_is_valid_amount(self):
        assert is_valid_amount("10.5")
        assert not is_valid_amount("ten")


class TestFormatMoney:
    @pytest.mark.parametrize(
        "units,expected",
        [
            (50_000, "₹500"),
            (1_050, "₹10.5"),
            (100_025, "₹1,000.25"),
            (1, "₹0.01"),
            (0, "₹0"),
            (123_456_789, "₹1,234,567.89"),
        ],
    )
    def test_format(self, units, expected):
        assert format_money(units) == expected


class TestArithmetic:
    def test_add_subtract(self):
        assert add(1000, 2500) == 3500
        assert subtract(1000, 2500) == -1500

    def test_positive(self):
        assert is_positive(1)
        assert not is_positive(0)
        assert not is_positive(-5)

    def test_check_balance(self):
        assert check_balance(10_000, 5_000)
        assert check_balance(10_000, 10_000)
        assert not check_balance(10_000, 10_001)

    def test_percentage_rounds_down(self):
        assert percentage(100_000, 2.0) == 2_000
        assert percentage(1050, 5.0) == 52  # 52.5 -> 52
        assert percentage(1000, 0.0) == 0

    def test_percentage_negative_rejected(self):
        with pytest.raises(MoneyError):
            percentage(1000, -1)

    def test_multiply(self):
        assert multiply(1000, 1.5) == 1500
        assert multiply(1000, 0) == 0

    def test_negative_multiplier_rejected(self):
        with pytest.raises(MoneyError):
            multiply(1000, -1.5)

