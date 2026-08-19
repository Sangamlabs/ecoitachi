"""Unit tests for the /loan, /loaninfo and /loanpay handlers.

Covers the /100 regression fix: the amount is parsed with the canonical
``parse_amount_or_error`` (rupee input -> sub-units, no division by 100) and
the argument order is ``/loan AMOUNT DAYS``.
"""

import asyncio
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

os.environ.setdefault("API_ID", "12345")
os.environ.setdefault("API_HASH", "testhash")
os.environ.setdefault("BOT_TOKEN", "123:testtoken")
os.environ.setdefault("MONGO_URI", "mongodb://127.0.0.1:27017")
os.environ.setdefault("MONGO_DB_NAME", "unoitachi_tests_loan_handler")
os.environ.setdefault("OWNER_ID", "1")

from handlers import loan  # noqa: E402
from utils.money import parse_amount  # noqa: E402


class FakeApp:
    def __init__(self):
        self.message_handlers = []

    def on_message(self, flt):
        def deco(cb):
            self.message_handlers.append((flt, cb))
            return cb

        return deco


@pytest.fixture
def app():
    a = FakeApp()
    loan.register(a)
    return a


@pytest.fixture
def handlers_map(app):
    return {cb.__name__: (flt, cb) for flt, cb in app.message_handlers}


@pytest.fixture
def gate(monkeypatch):
    async def fake_gate(message, feature=None):
        return True, None

    async def fake_ensure(*args, **kwargs):
        return None

    monkeypatch.setattr("handlers.common.check_gate", fake_gate)
    monkeypatch.setattr("handlers.common.identity_service.ensure_user", fake_ensure)


@pytest.fixture
def replies(monkeypatch):
    calls = []

    async def reply(client, message, text, **kwargs):
        calls.append(text)

    monkeypatch.setattr("handlers.loan.reply_html", reply)
    return calls


def _msg(command, user_id=1):
    return SimpleNamespace(
        id=1,
        command=command,
        from_user=SimpleNamespace(id=user_id, username="user", first_name="User"),
        chat=SimpleNamespace(id=999),
    )


def _call(cb, message, client=None):
    async def go():
        await cb(client, message)

    asyncio.run(go())


# ─── /loan ────────────────────────────────────────────────────────────────


def test_loan_5000_3_credits_500000_subunits(app, handlers_map, gate, replies, monkeypatch):
    issued = []

    async def fake_issue(user_id, amount, duration_days, actor_id=None):
        issued.append((user_id, amount, duration_days, actor_id))
        return True, "Loan issued successfully."

    monkeypatch.setattr("handlers.loan.loans_service.issue_loan", fake_issue)

    _call(handlers_map["cmd_loan"][1], _msg(["loan", "5000", "3"]))

    assert issued == [(1, parse_amount("5000"), 3, 1)]
    assert issued[0][1] == 500_000
    assert "Loan issued successfully." in replies[0]


def test_loan_50000_5(app, handlers_map, gate, replies, monkeypatch):
    issued = []

    async def fake_issue(user_id, amount, duration_days, actor_id=None):
        issued.append((user_id, amount, duration_days, actor_id))
        return True, "ok"

    monkeypatch.setattr("handlers.loan.loans_service.issue_loan", fake_issue)

    _call(handlers_map["cmd_loan"][1], _msg(["loan", "50000", "5"]))

    assert issued == [(1, parse_amount("50000"), 5, 1)]
    assert issued[0][1] == 5_000_000


def test_loan_500000_7(app, handlers_map, gate, replies, monkeypatch):
    issued = []

    async def fake_issue(user_id, amount, duration_days, actor_id=None):
        issued.append((user_id, amount, duration_days, actor_id))
        return True, "ok"

    monkeypatch.setattr("handlers.loan.loans_service.issue_loan", fake_issue)

    _call(handlers_map["cmd_loan"][1], _msg(["loan", "500000", "7"]))

    assert issued == [(1, parse_amount("500000"), 7, 1)]
    assert issued[0][1] == 50_000_000


def test_loan_argument_order_is_amount_days(app, handlers_map, gate, replies, monkeypatch):
    issued = []

    async def fake_issue(user_id, amount, duration_days, actor_id=None):
        issued.append((user_id, amount, duration_days, actor_id))
        return True, "ok"

    monkeypatch.setattr("handlers.loan.loans_service.issue_loan", fake_issue)

    _call(handlers_map["cmd_loan"][1], _msg(["loan", "3", "5000"]))

    assert issued == [(1, parse_amount("3"), 5000, 1)]


def test_loan_invalid_amount_not_issued(app, handlers_map, gate, replies, monkeypatch):
    issued = []

    async def fake_issue(**kwargs):
        issued.append(kwargs)
        return True, "ok"

    monkeypatch.setattr("handlers.loan.loans_service.issue_loan", fake_issue)

    _call(handlers_map["cmd_loan"][1], _msg(["loan", "abc", "3"]))

    assert issued == []
    assert "Usage" in replies[0]


def test_loan_wrong_arg_count_not_issued(app, handlers_map, gate, replies, monkeypatch):
    issued = []

    async def fake_issue(**kwargs):
        issued.append(kwargs)
        return True, "ok"

    monkeypatch.setattr("handlers.loan.loans_service.issue_loan", fake_issue)

    _call(handlers_map["cmd_loan"][1], _msg(["loan", "5000"]))

    assert issued == []
    assert "Usage" in replies[0]


def test_loan_non_int_days_not_issued(app, handlers_map, gate, replies, monkeypatch):
    issued = []

    async def fake_issue(**kwargs):
        issued.append(kwargs)
        return True, "ok"

    monkeypatch.setattr("handlers.loan.loans_service.issue_loan", fake_issue)

    _call(handlers_map["cmd_loan"][1], _msg(["loan", "5000", "x"]))

    assert issued == []
    assert "Days must be an integer." in replies[0]


def test_loan_rejection_message_passed_through(app, handlers_map, gate, replies, monkeypatch):
    async def fake_issue(user_id, amount, duration_days, actor_id=None):
        return False, "Maximum duration is 7 days."

    monkeypatch.setattr("handlers.loan.loans_service.issue_loan", fake_issue)

    _call(handlers_map["cmd_loan"][1], _msg(["loan", "5000", "99"]))

    assert "Maximum duration is 7 days." in replies[0]


# ─── /loaninfo ──────────────────────────────────────────────────────────────


def _loan_dict(principal=500_000):
    now = datetime.now(timezone.utc)
    return {
        "loan_id": "LOAN-TEST",
        "user_id": 1,
        "principal": principal,
        "outstanding_principal": principal,
        "interest_rate": 1.0,
        "total_repaid": 0,
        "issued_at": now,
        "due_at": now,
        "status": "ACTIVE",
    }


def test_loaninfo_shows_subunit_amounts(app, handlers_map, gate, replies, monkeypatch):
    async def fake_info(user_id):
        return _loan_dict(500_000)

    monkeypatch.setattr("handlers.loan.loans_service.get_loan_info", fake_info)

    _call(handlers_map["cmd_loaninfo"][1], _msg(["loaninfo"]))

    assert "₹5,000" in replies[0]
    assert "LOAN-TEST" in replies[0]


def test_loaninfo_no_loan(app, handlers_map, gate, replies, monkeypatch):
    async def fake_info(user_id):
        return None

    monkeypatch.setattr("handlers.loan.loans_service.get_loan_info", fake_info)

    _call(handlers_map["cmd_loaninfo"][1], _msg(["loaninfo"]))

    assert "You have no active loan." in replies[0]


# ─── /loanpay ───────────────────────────────────────────────────────────────


def test_loanpay_amount_parsed_canonically(app, handlers_map, gate, replies, monkeypatch):
    paid = []

    async def fake_pay(user_id, amount):
        paid.append((user_id, amount))
        return True, "Payment processed."

    monkeypatch.setattr("handlers.loan.loans_service.process_loan_payment", fake_pay)

    _call(handlers_map["cmd_loanpay"][1], _msg(["loanpay", "5000"]))

    assert paid == [(1, parse_amount("5000"))]
    assert paid[0][1] == 500_000


def test_loanpay_all_uses_outstanding(app, handlers_map, gate, replies, monkeypatch):
    paid = []

    async def fake_info(user_id):
        return _loan_dict(1_000_000)

    async def fake_pay(user_id, amount):
        paid.append((user_id, amount))
        return True, "Payment processed."

    monkeypatch.setattr("handlers.loan.loans_service.get_loan_info", fake_info)
    monkeypatch.setattr("handlers.loan.loans_service.process_loan_payment", fake_pay)

    _call(handlers_map["cmd_loanpay"][1], _msg(["loanpay", "all"]))

    assert paid == [(1, 1_000_000)]