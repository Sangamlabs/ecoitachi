"""Loan (debt) service — business logic for the loan system.

All money movement goes through the existing Economy Engine; every financial
event produces a transaction through the Transaction Engine.  Loan state lives
in the dedicated ``loans`` MongoDB collection owned by ``database/loans.py``.

A loan is a LIABILITY: the borrowed amount is credited to the wallet (so the
user can spend it) but subtracted from net worth, and never counted as earned
income.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Tuple

from database import loans as loans_db
from services import economy as econ
from services import settings as settings_service
from services import transaction as tx_service
from utils.money import format_money

logger = logging.getLogger(__name__)

# ─── Defaults (configurable via /setloan) ───────────────────────────────────

DEFAULT_MAX_PRINCIPAL = 100_000
DEFAULT_MIN_DURATION_DAYS = 1  # 24 hours minimum
DEFAULT_MAX_DURATION_DAYS = 7
DEFAULT_OVERDUE_INTEREST_RATE = 1.0  # 1% per overdue day
DEFAULT_RECOVERY_PERCENT = 100  # % of new wallet income applied to debt

# Transaction types (recorded through the central Transaction Engine)
LOAN_ISSUED = "LOAN_ISSUED"
LOAN_PAYMENT = "LOAN_PAYMENT"
LOAN_INTEREST = "LOAN_INTEREST"
LOAN_RECOVERY = "LOAN_RECOVERY"


def _now() -> datetime:
    return datetime.now(timezone.utc)


async def get_loan_config() -> dict[str, Any]:
    """Return the admin-configurable loan settings (merged over defaults)."""
    return await settings_service.get_loan_config()


async def is_eligible_for_loan(user_id: int) -> Tuple[bool, str | None]:
    """Return ``(eligible, reason)`` for taking a new loan."""
    from services import security as sec_svc

    banned, reason = await sec_svc.global_ban_check(user_id)
    if banned:
        return False, f"You are globally banned: {reason or 'no reason'}"

    if await loans_db.get_active_loan(user_id):
        return False, "You already have an active loan. Repay it first."

    return True, None


async def issue_loan(
    user_id: int,
    amount: int,
    duration_days: int,
    actor_id: int | None = None,
) -> Tuple[bool, str]:
    """Issue a loan: validate, create, credit wallet, audit transaction."""
    if not amount or amount <= 0:
        return False, "Loan amount must be positive."
    if duration_days < 1:
        return False, "Duration must be at least 1 day (24 hours)."

    eligible, reason = await is_eligible_for_loan(user_id)
    if not eligible:
        return False, reason or "You are not eligible for a loan."

    cfg = await get_loan_config()
    if not cfg["enabled"]:
        return False, "Loans are currently disabled by an admin."

    if amount > cfg["max_principal"]:
        return False, f"Amount exceeds the maximum principal of {format_money(cfg['max_principal'])}."
    if duration_days < cfg["min_duration_days"]:
        return False, f"Minimum duration is {cfg['min_duration_days']} day(s) (24 hours)."
    if duration_days > cfg["max_duration_days"]:
        return False, f"Maximum duration is {cfg['max_duration_days']} days."

    try:
        loan = await loans_db.create_loan(
            user_id=user_id,
            principal=amount,
            duration_days=duration_days,
            interest_rate=cfg["interest_rate"],
            issued_by=actor_id,
        )
    except ValueError as exc:
        return False, str(exc)

    # Credit wallet through the Economy Engine. Borrowed money is NOT earned
    # income, so it never inflates total_earned / monthly_earnings.
    before = await econ.get_balance(user_id)
    try:
        await econ.add_wallet(user_id, amount, earn=False, from_transaction="loan_issued")
    except Exception:
        logger.exception("loan credit failed for user %s; voiding loan %s", user_id, loan["loan_id"])
        await loans_db.update_loan(loan["loan_id"], status=loans_db.STATUS_CANCELLED)
        return False, "Loan could not be issued. Please try again."

    after = await econ.get_balance(user_id)
    await tx_service.record(
        user_id=user_id,
        ttype=LOAN_ISSUED,
        amount=amount,
        balance_before=before["wallet"],
        balance_after=after["wallet"],
        metadata={"loan_id": loan["loan_id"], "duration_days": duration_days, "actor_id": actor_id},
    )

    msg = (
        f"Loan issued successfully.\n"
        f"<b>Loan ID:</b> <code>{loan['loan_id']}</code>\n"
        f"<b>Principal:</b> {format_money(amount)}\n"
        f"<b>Duration:</b> {duration_days} day(s)\n"
        f"<b>Overdue interest:</b> {loan['interest_rate']}% per day\n"
        f"<b>Due date:</b> {loan['due_at'].strftime('%Y-%m-%d')}\n"
        f"<b>Outstanding:</b> {format_money(amount)}"
    )
    return True, msg


async def process_loan_payment(user_id: int, amount: int) -> Tuple[bool, str]:
    """Pay ``amount`` toward the user's active loan from their wallet."""
    loan = await loans_db.get_active_loan(user_id)
    if loan is None:
        return False, "You have no active loan."

    outstanding = int(loan.get("outstanding_principal", 0))
    if outstanding <= 0:
        return False, "Your loan has no outstanding balance."

    pay = max(0, min(int(amount), outstanding))
    if pay <= 0:
        return False, "Payment amount must be positive."

    balance = await econ.get_balance(user_id)
    if balance["wallet"] < pay:
        return False, (
            f"Insufficient wallet balance. You have {format_money(balance['wallet'])}, "
            f"need {format_money(pay)}."
        )

    before = balance["wallet"]
    await econ.remove_wallet(user_id, pay, spend=True, from_transaction="loan_payment")
    updated, applied = await loans_db.record_repayment(loan["loan_id"], pay)
    if applied < pay:
        # Interest accrued concurrently: refund the overpayment.
        refund = pay - applied
        await econ.add_wallet(user_id, refund, earn=False, from_transaction="loan_payment_refund")

    after = (await econ.get_balance(user_id))["wallet"]
    await tx_service.record(
        user_id=user_id,
        ttype=LOAN_PAYMENT,
        amount=applied,
        balance_before=before,
        balance_after=after,
        metadata={"loan_id": loan["loan_id"], "requested": pay},
    )

    if updated.get("status") == loans_db.STATUS_PAID or updated.get("outstanding_principal", 0) <= 0:
        return True, (
            f"Payment of {format_money(applied)} processed.\n"
            f"Loan <b>PAID OFF</b>! 🎉\n"
            f"<b>Loan ID:</b> <code>{updated['loan_id']}</code>\n"
            f"<b>Total repaid:</b> {format_money(updated.get('total_repaid', 0))}\n"
            f"<b>Outstanding:</b> 0"
        )

    return True, (
        f"Payment of {format_money(applied)} processed.\n"
        f"<b>Loan ID:</b> <code>{updated['loan_id']}</code>\n"
        f"<b>Outstanding:</b> {format_money(updated.get('outstanding_principal', 0))}\n"
        f"<b>Total repaid:</b> {format_money(updated.get('total_repaid', 0))}"
    )


async def get_loan_info(user_id: int) -> dict[str, Any] | None:
    """Return the user's active loan, or ``None``."""
    return await loans_db.get_active_loan(user_id)


async def run_loan_maintenance() -> dict[str, Any]:
    """APScheduler job: mark due loans OVERDUE and accrue interest once.

    Idempotent: ``mark_overdue`` and ``apply_interest`` are guarded so a
    restart or concurrent run never double-charges interest.
    """
    now = _now()
    summary = {"marked_overdue": 0, "interest_charged": 0, "scanned": 0}

    from database.mongo import mongo as _mongo

    cursor = _mongo.db[loans_db.COLLECTION].find(
        {"status": {"$in": loans_db.ACTIVE_STATES}, "due_at": {"$lt": now}}
    )
    async for doc in cursor:
        summary["scanned"] += 1
        if doc.get("status") == loans_db.STATUS_ACTIVE:
            if await loans_db.mark_overdue(doc["loan_id"]):
                summary["marked_overdue"] += 1
        applied, interest, _new_outstanding = await loans_db.apply_interest(doc["loan_id"])
        if applied:
            summary["interest_charged"] += 1
            try:
                await tx_service.record(
                    user_id=doc["user_id"],
                    ttype=LOAN_INTEREST,
                    amount=interest,
                    balance_before=int(doc.get("outstanding_principal", 0)),
                    balance_after=int(doc.get("outstanding_principal", 0)) + interest,
                    metadata={"loan_id": doc["loan_id"], "type": "debt"},
                )
            except Exception:
                logger.exception("failed to record loan interest transaction for %s", doc["loan_id"])
    return summary


async def apply_loan_recovery(user_id: int, income_amount: int) -> dict[str, Any]:
    """Apply the configured recovery percent of new wallet income to debt.

    Intended to be called exactly once per income credit (no per-command
    duplication).  Returns a summary dict for logging/response building.
    """
    cfg = await get_loan_config()
    recovery_percent = cfg["recovery_percent"]
    if recovery_percent <= 0 or income_amount <= 0:
        return {"recovered": 0, "remaining_debt": await loans_db.get_outstanding(user_id), "new_wallet": income_amount}

    loan = await loans_db.get_active_loan(user_id)
    if loan is None:
        return {"recovered": 0, "remaining_debt": 0, "new_wallet": income_amount}

    recovery_amount = int(income_amount * recovery_percent / 100)
    if recovery_amount <= 0:
        return {
            "recovered": 0,
            "remaining_debt": int(loan.get("outstanding_principal", 0)),
            "new_wallet": income_amount,
        }

    ok, _msg = await process_loan_payment(user_id, recovery_amount)
    remaining = await loans_db.get_outstanding(user_id)
    if ok:
        recovered = recovery_amount
        if recovery_amount > int(loan.get("outstanding_principal", 0)):
            recovered = int(loan.get("outstanding_principal", 0))
        return {
            "recovered": recovered,
            "remaining_debt": remaining,
            "new_wallet": max(0, income_amount - recovered),
            "type": LOAN_RECOVERY,
        }
    return {"recovered": 0, "remaining_debt": remaining, "new_wallet": income_amount}
