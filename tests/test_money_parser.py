"""Mongo-independent unit tests for the centralized amount parser.

Covers the plain decimal forms, the human-readable suffixes (k/m/b/t,
lakh/crore), the absolute parser ceiling and the ``parse_amount_or_error``
wrapper every economy command uses.  Requires no database.

Run with:  pytest tests/test_money_parser.py -v
"""

import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

os.environ.setdefault("API_ID", "12345")
os.environ.setdefault("API_HASH", "testhash")
os.environ.setdefault("BOT_TOKEN", "123:testtoken")
os.environ.setdefault("MONGO_URI", "mongodb://127.0.0.1:27017")
os.environ.setdefault("MONGO_DB_NAME", "unoitachi_tests_money_parser")
os.environ.setdefault("OWNER_ID", "1")

from utils.money import MoneyError, parse_amount  # noqa: E402
from utils.validators import parse_amount_or_error  # noqa: E402


def test_plain_forms_unchanged():
    assert parse_amount("500") == 50_000
    assert parse_amount("10.50") == 1050
    assert parse_amount("1,000.25") == 100_025
    assert parse_amount("0.01") == 1
    assert parse_amount("100000") == 10_000_000


def test_human_k():
    assert parse_amount("10k") == 1_000_000
    assert parse_amount("10K") == 1_000_000
    assert parse_amount("1.5k") == 150_000


def test_human_m_b():
    assert parse_amount("1m") == 100_000_000
    assert parse_amount("2.5m") == 250_000_000
    assert parse_amount("1b") == 100_000_000_000
    assert parse_amount("1.25b") == 125_000_000_000


def test_human_lakh_crore():
    assert parse_amount("1 lakh") == 10_000_000
    assert parse_amount("1lakh") == 10_000_000
    assert parse_amount("1.5 lakh") == 15_000_000
    assert parse_amount("1 crore") == 1_000_000_000
    assert parse_amount("2 crore") == 2_000_000_000


def test_leading_trailing_whitespace_and_commas():
    assert parse_amount("  10k ") == 1_000_000
    assert parse_amount("1,000k") == 100_000_000


@pytest.mark.parametrize(
    "bad",
    ["", "abc", "10kk", "1.2.3m", "--10k", "10xyz", "-5", "NaN", "1e3", "0.000001k"],
)
def test_rejects_malformed(bad):
    with pytest.raises(MoneyError):
        parse_amount(bad)


def test_ceiling_boundary_allowed():
    # ₹10,000,000,000 (1000 crore) is exactly the absolute parser ceiling.
    assert parse_amount("10000000000") == 10**12
    assert parse_amount("1b") == 100_000_000_000


@pytest.mark.parametrize("huge", ["1.5t", "1t", "999999999999", "99999999999"])
def test_ceiling_rejects_oversized(huge):
    with pytest.raises(MoneyError):
        parse_amount(huge)


def test_getcoin_accepts_reasonable_amounts():
    # The /getcoin flow uses parse_amount_or_error.
    assert parse_amount_or_error("100000") == (10_000_000, None)
    assert parse_amount_or_error("10k") == (1_000_000, None)
    assert parse_amount_or_error("1m") == (100_000_000, None)


def test_getcoin_rejects_huge_amount():
    value, err = parse_amount_or_error("999999999999")
    assert value is None
    assert err is not None


def test_getcoin_rejects_zero():
    value, err = parse_amount_or_error("0")
    assert value is None
    assert err is not None