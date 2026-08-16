"""User asset listing (resale) market service.

A LISTING is a user's offer to sell a quantity of an ASSET they own, priced in
their own words.  Every listing carries a unique Listing ID (``LST-...``);
the asset's Asset ID is never used as a listing identifier.  Sales are atomic:
claim active -> pending -> transfer -> move holdings -> sold, reverting to
active if any step fails.
"""

from __future__ import annotations

import time
from typing import Any

from database import asset_holdings as holdings_db
from database import asset_listings as listings_db
from database import users as users_db
from services import assets as asset_service
from services import economy
from services import transaction as tx_service
from utils.money import format_money, multiply, percentage


async def create_listing(
    user_id: int,
    symbol: str,
    quantity_raw: str,
    price_raw: str,
) -> dict[str, Any]:
    cfg = await asset_service.market_config()
    if not cfg.get("enabled", True):
        raise asset_service.AssetMarketDisabled("The Assets Market is currently disabled.")

    asset = await asset_service.get_active_asset(symbol)
    qty = asset_service.parse_quantity(quantity_raw, asset)
    price = int(float(str(price_raw)))
    if price <= 0:
        raise asset_service.AssetError("Price must be greater than 0.")

    holding = await holdings_db.get_holding(user_id, asset["asset_id"])
    owned = float(holding.get("quantity", 0)) if holding else 0.0
    if qty > owned + 1e-9:
        raise asset_service.InsufficientHoldings(
            f"You only own <b>{owned:g}</b> of <code>{asset['symbol']}</code>."
        )

    total_price = multiply(price, qty)
    listing_fee = percentage(total_price, float(cfg.get("listing_fee_percent", 0.0)))

    listing_id = listings_db.new_listing_id()
    doc = {
        "listing_id": listing_id,
        "seller_user_id": user_id,
        "asset_id": asset["asset_id"],
        "symbol": asset["symbol"],
        "name": asset.get("name", ""),
        "emoji": asset.get("emoji", ""),
        "quantity": qty,
        "unit_price": price,
        "total_price": total_price,
        "listing_fee": listing_fee,
        "status": listings_db.STATUS_ACTIVE,
        "buyer_user_id": None,
        "created_at": int(time.time()),
        "updated_at": int(time.time()),
    }
    await listings_db.insert_listing(doc)

    if listing_fee:
        try:
            await economy.remove_wallet(user_id, listing_fee, spend=True)
        except economy.InsufficientBalance:
            await listings_db.admin_cancel_listing(listing_id)
            raise asset_service.AssetError(
                f"Insufficient wallet balance to pay the listing fee ({format_money(listing_fee)})."
            )
        await tx_service.record(
            user_id=user_id,
            ttype=tx_service.ASSET_LISTING_BUY,
            amount=listing_fee,
            balance_before=0,
            balance_after=0,
            metadata={"listing_id": listing_id, "symbol": asset["symbol"], "fee": listing_fee},
        )
    return doc


async def cancel_listing(user_id: int, listing_id: str) -> dict[str, Any]:
    listing = await listings_db.get_listing(listing_id)
    if listing is None:
        raise asset_service.AssetError(f"Unknown listing: <code>{listing_id}</code>.")
    if listing["seller_user_id"] != user_id:
        raise asset_service.AssetError("You can only cancel your own listings.")
    if listing["status"] != listings_db.STATUS_ACTIVE:
        raise asset_service.AssetError("Only <b>active</b> listings can be cancelled.")
    await listings_db.cancel_listing(listing_id, user_id)
    return listing


async def buy_listing(user_id: int, listing_id: str) -> dict[str, Any]:
    """Buy an entire listing.  Atomic; reverts the listing on failure."""
    cfg = await asset_service.market_config()
    if not cfg.get("enabled", True):
        raise asset_service.AssetMarketDisabled("The Assets Market is currently disabled.")

    listing = await listings_db.get_listing(listing_id)
    if listing is None:
        raise asset_service.ListingUnavailable(f"Unknown listing: <code>{listing_id}</code>.")
    if listing["seller_user_id"] == user_id:
        raise asset_service.AssetError("You cannot buy your own listing.")

    claimed = await listings_db.claim_listing(listing_id, user_id)
    if claimed is None:
        raise asset_service.ListingUnavailable("This listing was just bought by someone else.")

    asset_id = claimed["asset_id"]
    symbol = claimed["symbol"]
    qty = float(claimed["quantity"])
    total_price = int(claimed["total_price"])
    unit_price = int(claimed["unit_price"])

    try:
        before_buyer = await economy.get_balance(user_id)
        try:
            await economy.remove_wallet(user_id, total_price, spend=True)
        except economy.InsufficientBalance:
            await listings_db.release_listing(listing_id)
            raise asset_service.AssetError(
                f"Insufficient wallet balance. Needed <b>{format_money(total_price)}</b>."
            )
        before_seller = await economy.get_balance(claimed["seller_user_id"])
        await economy.add_wallet(claimed["seller_user_id"], total_price, earn=False)
        await holdings_db.add_holding(
            user_id, asset_id, symbol, qty, total_price, unit_price
        )
        await holdings_db.remove_holding(claimed["seller_user_id"], asset_id, qty, unit_price)
    except Exception:
        await listings_db.release_listing(listing_id)
        raise

    await listings_db.mark_sold(listing_id)
    sold_listing = await listings_db.get_listing(listing_id)
    await tx_service.record(
        user_id=user_id,
        ttype=tx_service.ASSET_LISTING_BUY,
        amount=total_price,
        balance_before=before_buyer["wallet"],
        balance_after=before_buyer["wallet"] - total_price,
        metadata={
            "listing_id": listing_id,
            "seller_user_id": claimed["seller_user_id"],
            "asset_id": asset_id,
            "symbol": symbol,
            "quantity": qty,
            "unit_price": unit_price,
        },
    )
    await tx_service.record(
        user_id=claimed["seller_user_id"],
        ttype=tx_service.ASSET_LISTING_SALE,
        amount=total_price,
        balance_before=before_seller["wallet"],
        balance_after=before_seller["wallet"] + total_price,
        metadata={
            "listing_id": listing_id,
            "buyer_user_id": user_id,
            "asset_id": asset_id,
            "symbol": symbol,
            "quantity": qty,
            "unit_price": unit_price,
        },
    )
    await asset_service.refresh_user_asset_value(user_id)
    await asset_service.refresh_user_asset_value(claimed["seller_user_id"])
    return sold_listing


async def my_listings(user_id: int) -> list[dict[str, Any]]:
    return await listings_db.user_listings(user_id)


async def browse(
    symbol: str | None = None, page: int = 1, per_page: int = 10
) -> dict[str, Any]:
    page = max(1, page)
    offset = (page - 1) * per_page
    listings = await listings_db.list_active(symbol=symbol, limit=per_page, offset=offset)
    total = await listings_db.count_active(symbol=symbol)
    return {
        "listings": listings,
        "page": page,
        "per_page": per_page,
        "total": total,
        "pages": max(1, (total + per_page - 1) // per_page) if total else 1,
    }


async def admin_cancel_listing(admin_id: int, listing_id: str) -> dict[str, Any]:
    listing = await listings_db.get_listing(listing_id)
    if listing is None:
        raise asset_service.AssetError(f"Unknown listing: <code>{listing_id}</code>.")
    await listings_db.admin_cancel_listing(listing_id)
    await asset_service.audit(
        admin_id, "RM_LISTING", listing["symbol"], listing["status"], listings_db.STATUS_CANCELLED
    )
    return listing


async def listing_info(listing_id: str) -> dict[str, Any] | None:
    return await listings_db.get_listing(listing_id)


def listing_text(listing: dict[str, Any]) -> str:
    symbol = listing["symbol"]
    emoji = listing.get("emoji", "📦")
    name = listing.get("name", symbol)
    status = listing["status"]
    state = {
        listings_db.STATUS_ACTIVE: "🟢 Active",
        listings_db.STATUS_PENDING: "🕐 Pending",
        listings_db.STATUS_SOLD: "✅ Sold",
        listings_db.STATUS_CANCELLED: "❌ Cancelled",
    }.get(status, status)
    return (
        f"{emoji} <code>{listing['listing_id']}</code> — <b>{name}</b> ({symbol})\n"
        f"💹 Qty: <b>{listing['quantity']:g}</b> × ₹{format_money(listing['unit_price'])}"
        f" = <b>₹{format_money(listing['total_price'])}</b>\n"
        f"👤 Seller: <b>{username_of(listing['seller_user_id'])}</b>\n"
        f"📋 Status: {state}"
    )


def username_of(user_id: int) -> str:
    return f"<code>{user_id}</code>"


async def username_lookup(user_id: int) -> str:
    user = await users_db.get_user(user_id)
    if not user:
        return f"<code>{user_id}</code>"
    uname = user.get("username")
    if uname:
        return f"@{uname}"
    first = user.get("first_name") or ""
    return first or f"<code>{user_id}</code>"
