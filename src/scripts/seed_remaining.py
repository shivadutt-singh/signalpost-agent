"""
Seed remaining active Norwegian enterprises to exceed 1,000 compliant profiles.
"""

from __future__ import annotations

import asyncio
import logging
import sys
import time
from pathlib import Path

# Add project root to sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from src.cli import run_batch
from src.collectors.brreg_client import BrregClient
from src.collectors.http_budget import BudgetedAsyncClient, HttpBudgetTracker
from src.scripts.backfill_standards import synchronize_profiles

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("signalpost.seed")


async def main():
    profiles_dir = Path("output/profiles")
    existing = {p.stem for p in profiles_dir.glob("*.json")}
    logger.info(f"Existing profiles in {profiles_dir}: {len(existing)}")

    needed = max(0, 1020 - len(existing))
    logger.info(f"Targeting at least {needed} new profiles to reach >= 1,020...")

    # Fetch 1,100 orgnrs
    budget = HttpBudgetTracker.create_for_batch(batch_size=100)
    async with BudgetedAsyncClient(budget_tracker=budget) as client:
        brreg = BrregClient(client)
        orgnrs = await brreg.fetch_active_orgnrs(limit=1100)

    new_orgnrs = [o for o in orgnrs if o not in existing]
    to_process = new_orgnrs[:max(needed, 520)]
    logger.info(f"Identified {len(new_orgnrs)} new active orgnrs. Selected {len(to_process)} for processing.")

    t0 = time.time()
    telemetry = await run_batch(to_process, concurrency=25, include_web=False)
    elapsed = time.time() - t0
    logger.info(f"Batch completed in {elapsed:.2f}s ({elapsed/60:.2f} min). Succeeded: {telemetry.companies_succeeded}/{telemetry.companies_processed}")

    # Synchronize standards across all profiles
    sync_count = synchronize_profiles()
    logger.info(f"Synchronized standards across {sync_count} total profiles.")

    total_profiles = len(list(profiles_dir.glob("*.json")))
    logger.info(f"Final total profiles: {total_profiles}")


if __name__ == "__main__":
    asyncio.run(main())
