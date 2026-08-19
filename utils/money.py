"""Centralized money utilities.

All currency is stored as an integer in the smallest unit (rupees x 100,
so ₹10.50 = 1050).  Floating-point values are NEVER used for internal money.
Only pure functions live here so they can be unit tested without a database.
"""

from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation
from typing import Union

from config import config

UNIT = 100  # 1 UN = 100 sub-units
SYMBOL = "₹"

Number = Union[int, str, float]

# Human-readable rupee multipliers (case-insensitive): 10k = 10,000 rupees,
# 1.5m = 1,500,000, 1b = 1,000,000,000, 1t = 1,000,000,000,000, 1 crore, 1 lakh.
_HUMAN_SUFFIXES: dict[str, Decimal] = {
    "k": Decimal(10**3),
    "m": Decimal(10**6),
    "b": Decimal(10**9),
    "t": Decimal(10**12),
    "lakh": Decimal(10**5),
    "crore": Decimal(10**7),
}
_PLAIN_RE = re.compile(r"\d+(\.\d{1,2})?")
_HUMAN_RE = re.compile(r"(\d+(?:\.\d+)?)\s*([a-z]+)", re.IGNORECASE)


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


def _parse_human(cleaned: str) -> int | None:
    """Parse a human-readable form (``10k``, ``1.5m``, ``1 lakh``, ``1 crore``).

    Returns integer sub-units, ``None`` when the input is not human-readable,
    or raises :class:`MoneyError` when the suffix is unknown or the value is
    too precise to fit whole sub-units.
    """
    match = _HUMAN_RE.fullmatch(cleaned)
    if not match:
        return None
    suffix = match.group(2).lower()
    multiplier = _HUMAN_SUFFIXES.get(suffix)
    if multiplier is None:
        raise MoneyError("invalid amount")
    try:
        sub_units = Decimal(match.group(1)) * multiplier * UNIT
    except InvalidOperation:
        raise MoneyError("invalid amount")
    if sub_units != sub_units.to_integral_value():
        raise MoneyError("amount is too precise (minimum is 0.01)")
    return int(sub_units)


def _parse_plain(cleaned: str) -> int | None:
    """Parse a plain decimal form (``500``, ``10.50``, ``1,000.25``)."""
    if not _PLAIN_RE.fullmatch(cleaned):
        return None
    if "." in cleaned:
        whole, _, frac = cleaned.partition(".")
        frac = (frac + "00")[:2]
        return int(whole) * UNIT + int(frac)
    return int(cleaned) * UNIT


def parse_amount(raw: str) -> int:
    """Parse a user-supplied amount string into integer sub-units.

    Accepts plain forms (``500``, ``10.50``, ``1,000.25``, ``0.01``) and
    human-readable forms (``10k``, ``1.5m``, ``1b``, ``1 lakh``, ``1 crore``).
    Values above the configured absolute ceiling are REJECTED (never clamped).
    Rejects zero, negatives, NaN, and anything non-numeric.
    """
    if not isinstance(raw, str):
        raw = str(raw)
    cleaned = raw.strip().replace(",", "").replace(SYMBOL, "")
    if not cleaned:
        raise MoneyError("amount is empty")
    value = _parse_human(cleaned)
    if value is None:
        value = _parse_plain(cleaned)
    if value is None:
        raise MoneyError(f"invalid amount: {raw!r}")
    if value > config.MAX_AMOUNT_SUBUNITS:
        raise MoneyError(
            f"amount exceeds the maximum of {format_money(config.MAX_AMOUNT_SUBUNITS)}"
        )
    return value


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
