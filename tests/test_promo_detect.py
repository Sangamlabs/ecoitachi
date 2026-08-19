"""Mongo-independent unit tests for the promo text detector.

The auto-detection handler only ever redeems a token that already exists as a
known active promo code; candidate extraction itself is a pure regex.  These
tests exercise exactly that extraction (the gate that ordinary messages must
pass before any economy code is touched) and require no database.

Run with:  pytest tests/test_promo_detect.py -v
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
os.environ.setdefault("MONGO_DB_NAME", "unoitachi_tests_promo_detect")
os.environ.setdefault("OWNER_ID", "1")

from handlers.promo_detect import MAX_SCAN_LENGTH, TOKEN_RE  # noqa: E402


def extract(text: str) -> list[str]:
    return TOKEN_RE.findall(text.upper())


def test_standalone_valid_code():
    assert extract("ABC123XYZ") == ["ABC123XYZ"]


def test_code_inside_sentence():
    tokens = extract("Congratulations everyone! Use ABC123XYZ")
    assert "ABC123XYZ" in tokens


def test_code_is_uppercased():
    assert extract("abc123xyz") == ["ABC123XYZ"]


def test_case_variation():
    assert extract("AbC123XyZ") == ["ABC123XYZ"]


def test_short_tokens_ignored():
    assert extract("hi ok by") == []


def test_too_long_code_is_split():
    tokens = extract("A" * 21)
    assert all(len(t) <= 20 for t in tokens)


def test_special_characters_split_tokens():
    assert extract("ABC-123") == ["ABC", "123"]


def test_numbers_and_letters_mix():
    assert extract("ITACHI500") == ["ITACHI500"]


def test_max_scan_length_constant():
    assert MAX_SCAN_LENGTH == 1000


@pytest.mark.parametrize(
    "text,expected",
    [
        ("hello bro", ["HELLO", "BRO"]),
        ("Use promo ABC123XYZ now", ["USE", "PROMO", "ABC123XYZ", "NOW"]),
        ("ABC123XYZ ABC456XYZ", ["ABC123XYZ", "ABC456XYZ"]),
    ],
)
def test_extraction_tokens(text, expected):
    # Ordinary words still extract as tokens — the cache pre-filter is what
    # rejects them; extraction alone must match the documented 3-20 alnum format.
    assert extract(text) == expected