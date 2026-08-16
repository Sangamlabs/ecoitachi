"""Centralized money utilities.

All currency is stored as an integer in the smallest unit (rupees x 100,
so ₹10.50 = 1050).  Floating-point values are NEVER used for internal money.
Only pure functions live here so they can be unit tested without a database.
"""

from __future__ import annotations

import re
from typing import Union

UNIT = 100  # 1 RS = 100 sub-units
SYMBOL = "₹"

Number = Union[int, str, float]


class MoneyError(ValueError):
    """Raised for invalid monetary amounts."""


def _to_int(value: Number) -> int:
    if isinstance(value, bool):
        raise MoneyError("boolean is not a valid amount")
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        if not value.is_integer():
            raise MoneyError("float amounts must be whole sub-units")
        return int(value)
    raise MoneyError(f"cannot parse {value!r} as money")


def parse_amount(raw: str) -> int:
    """Parse a user-supplied amount string into integer sub-units.

    Accepts forms like ``500``, ``10.50``, ``1,000.25``, ``0.01``.
    Rejects zero, negatives, NaN, and anything non-numeric.
    """
    if not isinstance(raw, str):
        raw = str(raw)
    cleaned = raw.strip().replace(",", "").replace(SYMBOL, "")
    if not cleaned:
        raise MoneyError("amount is empty")
    if not re.fullmatch(r"\d+(\.\d{1,2})?", cleaned):
        raise MoneyError(f"invalid amount: {raw!r}")
    if "." in cleaned:
        whole, _, frac = cleaned.partition(".")
        frac = (frac + "00")[:2]
        return int(whole) * UNIT + int(frac)
    return int(cleaned) * UNIT


def is_valid_amount(raw: str) -> bool:
    try:
        parse_amount(raw)
        return True
    except MoneyError:
        return False


def format_money(units: Number) -> str:
    """Format integer sub-units as a human-readable ₹ string."""
    units = _to_int(units)
    sign = "-" if units < 0 else ""
    units = abs(units)
    rupees, paise = divmod(units, UNIT)
    body = f"{rupees:,}"
    if paise:
        body += f".{paise:02d}".rstrip("0")
    return f"{sign}{SYMBOL}{body}"


def is_positive(units: Number) -> bool:
    return _to_int(units) > 0


def check_balance(balance: Number, amount: Number) -> bool:
    """True when balance - amount >= 0."""
    return _to_int(balance) >= _to_int(amount)


def add(a: Number, b: Number) -> int:
    return _to_int(a) + _to_int(b)


def subtract(a: Number, b: Number) -> int:
    return _to_int(a) - _to_int(b)


def percentage(amount: Number, rate: float) -> int:
    """Compute ``amount * rate %`` as an integer (rounded down)."""
    if rate < 0:
        raise MoneyError("rate cannot be negative")
    return int(_to_int(amount) * rate) // 100


def multiply(amount: Number, multiplier: float) -> int:
    """Integer payout for a decimal multiplier (e.g. 1.5x)."""
    if multiplier < 0:
        raise MoneyError("multiplier cannot be negative")
    return int(_to_int(amount) * multiplier)
