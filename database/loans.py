"""Loan (debt) data access layer.

All money movement happens through the Economy Service; this module owns the
``loans`` collection and the loan lifecycle state only.

Invariants enforced here:

- a user can have at most one ACTIVE/OVERDUE loan (DB-level partial unique
  index makes creation race-safe),
- repayments never drive ``outstanding_principal`` below zero,
- interest accrual is idempotent via ``last_interest_at``.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from pymongo.errors import DuplicateKeyError

from database.mongo import mongo

COLLECTION = "loans"

STATUS_ACTIVE = "ACTIVE"
STATUS_OVERDUE = "OVERDUE"
STATUS_PAID = "PAID"
STATUS_DEFAULTED = "DEFAULTED"
STATUS_CANCELLED = "CANCELLED"

ACTIVE_STATES = [STATUS_ACTIVE, STATUS_OVERDUE]


def _now() -> datetime:
    return datetime.now(timezone.utc)


async def ensure_indexes() -> None:
    """Create the loans indexes (including the active-loan race guard)."""
    loans = mongo.db[COLLECTION]
    await loans.create_index("loan_id", unique=True)
    await loans.create_index("user_id")
    await loans.create_index("status")
    await loans.create_index("due_at")
    # At most one ACTIVE/OVERDUE loan per user — enforced by MongoDB.
    await loans.create_index(
        [("user_id", 1)],
        unique=True,
        name="user_id_active_loan",
        partialFilterExpression={"status": {"$in": ACTIVE_STATES}},
    )
    await loans.create_index([("user_id", 1), ("status", 1)])


async def create_loan(
    user_id: int,
    principal: int,
    duration_days: int,
    interest_rate: float,
    issued_by: int | None = None,
) -> dict[str, Any]:
    """Atomically create one loan for a user.

    Raises ``ValueError`` if the user already has an ACTIVE/OVERDUE loan
    (concurrent double-issue is prevented by the partial unique index).
    """
    now = _now()
    due_at = now + timedelta(days=duration_days)
    loan_id = f"LOAN-{uuid.uuid4().hex[:10].upper()}"
    doc = {
        "loan_id": loan_id,
        "user_id": user_id,
        "principal": principal,
        "outstanding_principal": principal,
        "interest_rate": interest_rate,
        "issued_at": now,
        "due_at": due_at,
        "status": STATUS_ACTIVE,
        "total_interest": 0,
        "total_repaid": 0,
        "last_interest_at": now,
        "last_repayment_at": now,
        "issued_by": issued_by,
        "created_at": now,
        "updated_at": now,
    }
    try:
        await mongo.db[COLLECTION].insert_one(doc)
    except DuplicateKeyError:
        raise ValueError("You already have an active loan. Repay it first.")
    return dict(doc)


async def get_active_loan(user_id: int) -> Optional[dict[str, Any]]:
    """Return the user's ACTIVE or OVERDUE loan, or ``None``."""
    return await mongo.db[COLLECTION].find_one(
        {"user_id": user_id, "status": {"$in": ACTIVE_STATES}}
    )


async def get_outstanding(user_id: int) -> int:
    """Return the total outstanding debt of a user's active loan (0 if none)."""
    loan = await get_active_loan(user_id)
    if loan is None:
        return 0
    return int(loan.get("outstanding_principal", 0))


async def get_loan_by_id(loan_id: str) -> Optional[dict[str, Any]]:
    """Get a loan document by its loan_id."""
    return await mongo.db[COLLECTION].find_one({"loan_id": loan_id})


async def update_loan(loan_id: str, **updates: Any) -> Optional[dict[str, Any]]:
    """Update a loan document and return the updated document."""
    updates["updated_at"] = _now()
    return await mongo.db[COLLECTION].find_one_and_update(
        {"loan_id": loan_id},
        {"$set": updates},
        return_document=True,
    )


async def mark_overdue(loan_id: str) -> bool:
    """Move an ACTIVE loan to OVERDUE (idempotent)."""
    result = await mongo.db[COLLECTION].update_one(
        {"loan_id": loan_id, "status": STATUS_ACTIVE},
        {"$set": {"status": STATUS_OVERDUE, "updated_at": _now()}},
    )
    return result.modified_count > 0


async def record_repayment(
    loan_id: str,
    amount_repaid: int,
    transaction_type: str = "LOAN_PAYMENT",
) -> tuple[dict[str, Any], int]:
    """Apply a repayment, clamping to the current outstanding.

    Race-safe: the ``$inc`` only matches documents whose outstanding is still
    ``>= amount``, and the clamp loop retries on a lost race.  Returns
    ``(updated_loan, applied_amount)``; ``applied_amount`` can be smaller than
    ``amount_repaid`` when the debt was already partly paid off.
    """
    if amount_repaid <= 0:
        loan = await mongo.db[COLLECTION].find_one({"loan_id": loan_id})
        if loan is None:
            raise ValueError("Loan not found.")
        return loan, 0

    for _ in range(5):
        loan = await mongo.db[COLLECTION].find_one({"loan_id": loan_id})
        if loan is None:
            raise ValueError("Loan not found.")
        amount = min(int(amount_repaid), int(loan.get("outstanding_principal", 0)))
        if amount <= 0:
            return loan, 0
        result = await mongo.db[COLLECTION].find_one_and_update(
            {"loan_id": loan_id, "outstanding_principal": {"$gte": amount}},
            {
                "$inc": {"outstanding_principal": -amount, "total_repaid": amount},
                "$set": {"last_repayment_at": _now(), "updated_at": _now()},
            },
            return_document=True,
        )
        if result is not None:
            if result.get("outstanding_principal", 0) <= 0:
                result = await mongo.db[COLLECTION].find_one_and_update(
                    {"loan_id": loan_id, "status": {"$ne": STATUS_PAID}},
                    {"$set": {"status": STATUS_PAID, "updated_at": _now()}},
                    return_document=True,
                ) or result
                result["status"] = STATUS_PAID
            return result, amount
    raise ValueError("Repayment conflict. Please try again.")


async def calculate_interest(loan: dict[str, Any]) -> tuple[int, int]:
    """Compute (idempotent) overdue interest since the last charge.

    Returns ``(interest_amount, new_outstanding)``.  Uses
    ``last_interest_at`` so repeated runs never double-charge.
    """
    now = _now()
    due_at = loan.get("due_at")
    if due_at is None or now <= due_at:
        return 0, int(loan.get("outstanding_principal", 0))
    outstanding = int(loan.get("outstanding_principal", 0))
    rate = float(loan.get("interest_rate", 0))
    if outstanding <= 0 or rate <= 0:
        return 0, outstanding
    last = loan.get("last_interest_at") or due_at
    days = max(0, (now - last).days)
    if days <= 0:
        return 0, outstanding
    interest = int(outstanding * rate / 100) * days
    return interest, outstanding + interest


async def apply_interest(loan_id: str) -> tuple[bool, int, int]:
    """Atomically accrue overdue interest for a loan once per period.

    Guarded by ``status == OVERDUE`` and ``last_interest_at`` matching the
    snapshot the interest was computed from, so two maintenance runs can never
    double-charge.  Returns ``(applied, interest, new_outstanding)``.
    """
    now = _now()
    loan = await mongo.db[COLLECTION].find_one(
        {"loan_id": loan_id, "status": STATUS_OVERDUE}
    )
    if loan is None:
        return False, 0, 0
    interest, _ = await calculate_interest(loan)
    if interest <= 0:
        return False, 0, int(loan.get("outstanding_principal", 0))
    updated = await mongo.db[COLLECTION].find_one_and_update(
        {
            "loan_id": loan_id,
            "status": STATUS_OVERDUE,
            "last_interest_at": loan.get("last_interest_at"),
        },
        {
            "$inc": {"outstanding_principal": interest, "total_interest": interest},
            "$set": {"last_interest_at": now, "updated_at": now},
        },
        return_document=True,
    )
    if updated is None:
        return False, 0, int(loan.get("outstanding_principal", 0))
    return True, interest, int(updated.get("outstanding_principal", 0))


async def get_loans_by_status(status: str, limit: int = 100) -> list[dict[str, Any]]:
    cursor = mongo.db[COLLECTION].find({"status": status}).sort("due_at", 1).limit(limit)
    return [doc async for doc in cursor]


async def get_loans_by_user(user_id: int) -> list[dict[str, Any]]:
    cursor = mongo.db[COLLECTION].find({"user_id": user_id}).sort("issued_at", -1)
    return [doc async for doc in cursor]


async def get_loan_stats() -> dict[str, Any]:
    """Overall loan statistics (active / overdue / paid + monetary totals)."""
    loans = mongo.db[COLLECTION]
    cursor = loans.find({})
    docs = [doc async for doc in cursor]

    active = sum(1 for d in docs if d.get("status") == STATUS_ACTIVE)
    overdue = sum(1 for d in docs if d.get("status") == STATUS_OVERDUE)
    paid = sum(1 for d in docs if d.get("status") == STATUS_PAID)
    total_principal = sum(int(d.get("principal", 0)) for d in docs)
    total_outstanding = sum(int(d.get("outstanding_principal", 0)) for d in docs)
    total_interest = sum(int(d.get("total_interest", 0)) for d in docs)
    total_repaid = sum(int(d.get("total_repaid", 0)) for d in docs)
    unique_borrowers = len({d.get("user_id") for d in docs})

    return {
        "active_loans": active,
        "overdue_loans": overdue,
        "paid_loans": paid,
        "total_principal_issued": total_principal,
        "total_outstanding": total_outstanding,
        "total_interest_accrued": total_interest,
        "total_repaid": total_repaid,
        "unique_borrowers": unique_borrowers,
    }
