"""Background jobs: interest, monthly tax distribution, market ticks.

All jobs are idempotent so bot restarts cannot double-pay interest or
double-distribute the tax pool.
"""

from __future__ import annotations

import logging

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

from config import config
from services import (
    asset_market as asset_market_service,
    interest as interest_service,
    stocks as stocks_service,
    tax as tax_service,
)

logger = logging.getLogger(__name__)


async def _run_interest() -> None:
    try:
        await interest_service.process_due_interest()
    except Exception:
        logger.exception("interest job failed")


async def _run_stock_tick() -> None:
    try:
        await stocks_service.update_market_prices()
        await stocks_service.refresh_all_stock_values()
    except Exception:
        logger.exception("stock market job failed")


async def _run_asset_tick() -> None:
    try:
        await asset_market_service.tick()
    except Exception:
        logger.exception("asset market job failed")


async def _run_game_cleanup() -> None:
    from services import game_engine

    try:
        await game_engine.expire_stale_games("mines")
    except Exception:
        logger.exception("game cleanup job failed")


async def _run_monthly_tax() -> None:
    try:
        await tax_service.distribute_monthly()
    except Exception:
        logger.exception("monthly tax distribution job failed")


def build_scheduler() -> AsyncIOScheduler:
    scheduler = AsyncIOScheduler(timezone="UTC")

    scheduler.add_job(
        _run_interest,
        IntervalTrigger(minutes=max(1, config.INTEREST_CHECK_INTERVAL_MINUTES)),
        id="interest",
        name="24h bank interest",
        max_instances=1,
        coalesce=True,
    )
    scheduler.add_job(
        _run_stock_tick,
        IntervalTrigger(minutes=max(1, config.STOCK_UPDATE_INTERVAL_MINUTES)),
        id="stock_tick",
        name="stock market price updates",
        max_instances=1,
        coalesce=True,
    )
    scheduler.add_job(
        _run_asset_tick,
        IntervalTrigger(minutes=1),
        id="asset_tick",
        name="asset market price updates",
        max_instances=1,
        coalesce=True,
    )
    scheduler.add_job(
        _run_game_cleanup,
        IntervalTrigger(minutes=1),
        id="game_cleanup",
        name="expire stale game sessions",
        max_instances=1,
        coalesce=True,
    )
    # Run daily; distribute_monthly() is idempotent per month key.
    scheduler.add_job(
        _run_monthly_tax,
        CronTrigger(hour=0, minute=5, timezone="UTC"),
        id="monthly_tax",
        name="monthly tax pool distribution",
        max_instances=1,
        coalesce=True,
    )
    return scheduler


def job_summary(scheduler: AsyncIOScheduler) -> str:
    jobs = scheduler.get_jobs()
    return f"Scheduled jobs: {', '.join(j.id for j in jobs) or 'none'}"
