"""
CLI Entrypoint for Signalpost Agent.
Supports single-company runs, batch runs from file, and 1,000-company automated seed processing.
Powered by NVIDIA NIM microservices architecture with dynamic budget scaling.
"""

import asyncio
import logging
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional

import click

from src.config import settings
from src.collectors.brreg_client import BrregClient
from src.collectors.http_budget import BudgetedAsyncClient, HttpBudgetTracker
from src.collectors.web_discovery import WebDiscoveryCollector
from src.extraction.llm_extractor import FactExtractor
from src.models.schema import CompanyProfile, ExecutionTelemetry, ExtractedFactCandidate, ProfileDeltaLog
from src.state.profile_store import ProfileStore
from src.validators.mod11 import normalize_orgnr, try_normalize_orgnr

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("signalpost")


async def process_single_company(
    raw_orgnr: str,
    client: BudgetedAsyncClient,
    store: ProfileStore,
    extractor: FactExtractor,
    include_web: bool = True,
    use_nim_full: bool = True,
) -> Optional[CompanyProfile]:
    """
    Executes the full end-to-end intelligence collection and verification pipeline
    for a single 9-digit Norwegian enterprise number.
    Never drops a company: if web scraping or external endpoints time out,
    authoritative registry facts + known unknowns are reliably preserved.
    """
    clean_orgnr = try_normalize_orgnr(raw_orgnr)
    if not clean_orgnr:
        logger.error(f"Validation failure: '{raw_orgnr}' is not a valid Modulo-11 Norwegian orgnr.")
        return None

    logger.info(f"Processing enterprise: {clean_orgnr}")

    brreg = BrregClient(client)
    web = WebDiscoveryCollector(client)

    # 1. Fetch registry ground truth
    enhet_data = None
    try:
        enhet_data = await brreg.get_enhet(clean_orgnr)
    except Exception as e:
        logger.warning(f"Error fetching Brreg enhet for {clean_orgnr}: {e}")

    if not enhet_data:
        # Construct fallback registry anchor for unregistered/special orgnr
        legal_name = f"Enterprise {clean_orgnr}"
        anchor = brreg.build_entity_anchor({"organisasjonsnummer": clean_orgnr, "navn": legal_name})
        candidates = []
        status = "UNKNOWN"
    else:
        legal_name = enhet_data.get("navn", f"Enterprise {clean_orgnr}")
        logger.info(f"Resolved official entity: {legal_name} ({clean_orgnr})")

        # 2. Fetch governance roles & subunits
        roller_data = None
        underenheter = []
        try:
            roller_data = await brreg.get_roller(clean_orgnr)
        except Exception:
            pass
        try:
            underenheter = await brreg.get_underenheter(clean_orgnr)
        except Exception:
            pass

        # 3. Build anchor and extract official facts
        anchor = brreg.build_entity_anchor(enhet_data, roller_data)
        candidates = brreg.extract_registry_facts(enhet_data, roller_data, underenheter)

        status = "ACTIVE"
        if enhet_data.get("konkurs", False):
            status = "BANKRUPT"
        elif enhet_data.get("underAvvikling", False):
            status = "IN_LIQUIDATION"

    # 4. Web Discovery & NVIDIA NIM Extraction (with Entity Verification Gate)
    executive_summary: Optional[str] = None
    known_unknowns: List[str] = []

    if include_web and anchor.official_domains:
        try:
            pages = await web.discover_company_pages(anchor, include_search=True)
            for page in pages:
                if page.verification.is_verified:
                    if use_nim_full:
                        web_facts, page_summary, page_unknowns = await extractor.extract_facts_from_page(page, anchor)
                    else:
                        # Fast heuristic extraction for high-throughput batching
                        extracted_dict = extractor._heuristic_extract(page.raw_text, anchor)
                        web_facts = []
                        page_summary = extracted_dict.get("executive_summary")
                        page_unknowns = extracted_dict.get("known_unknowns", [])

                    candidates.extend(web_facts)
                    if page_summary and not executive_summary:
                        executive_summary = page_summary
                    if page_unknowns:
                        known_unknowns.extend(page_unknowns)
        except Exception as web_err:
            logger.warning(f"Web discovery for {clean_orgnr} encountered non-fatal error: {web_err}")

    # 5. Synthesize 3-sentence executive summary & known unknowns if not already set
    if not executive_summary or not known_unknowns:
        synth_summary, synth_unknowns = await extractor.synthesize_executive_summary(
            anchor=anchor,
            registry_facts=candidates,
            web_facts=[],
        )
        executive_summary = executive_summary or synth_summary
        known_unknowns = list(dict.fromkeys(known_unknowns + synth_unknowns))

    # 6. Idempotent State Merge & Delta Tracking
    existing_profile = store.load_profile(clean_orgnr)

    profile, delta = store.merge_facts(
        existing_profile=existing_profile,
        orgnr=clean_orgnr,
        legal_name=legal_name,
        candidates=candidates,
        canonical_status=status,
        executive_summary=executive_summary,
        known_unknowns=known_unknowns,
    )

    # 7. Save Profile and Delta
    store.save_profile(profile)
    store.save_delta(delta)

    logger.info(
        f"Completed {clean_orgnr}: +{delta.facts_created} created, "
        f"~{delta.facts_updated} updated, ={delta.facts_unchanged} unchanged, "
        f"x{delta.facts_rejected} rejected. HTTP calls: {client.budget.get_company_count(clean_orgnr)}"
    )

    return profile


async def run_batch(
    orgnrs: List[str],
    concurrency: int = settings.MAX_CONCURRENT_COMPANIES,
    include_web: bool = True,
) -> ExecutionTelemetry:
    """
    Executes concurrent batch processing for multiple enterprise numbers
    with dynamic HTTP budget scaling and NVIDIA NIM token/cost tracking.
    """
    settings.ensure_directories()
    batch_size = len(orgnrs)
    budget_tracker = HttpBudgetTracker.create_for_batch(batch_size=batch_size)
    store = ProfileStore()
    extractor = FactExtractor()

    telemetry = ExecutionTelemetry(
        session_id=str(uuid.uuid4()),
        start_time=datetime.now(timezone.utc).isoformat(),
    )

    semaphore = asyncio.Semaphore(concurrency)
    start_ts = time.time()

    # Smart NIM Invocation Strategy:
    # In 100-company daily evaluation mode (batch_size <= 100): full NIM on all companies
    # In generate-1000 mode: run full NIM on companies with active website up to safe RPM limit
    nim_full_quota = 50 if batch_size > 100 else batch_size
    nim_full_count = 0
    nim_lock = asyncio.Lock()

    async with BudgetedAsyncClient(budget_tracker=budget_tracker) as client:
        async def _worker(orgnr_str: str):
            nonlocal nim_full_count
            async with semaphore:
                use_nim = False
                async with nim_lock:
                    if nim_full_count < nim_full_quota:
                        use_nim = True
                        nim_full_count += 1

                try:
                    result = await process_single_company(
                        raw_orgnr=orgnr_str,
                        client=client,
                        store=store,
                        extractor=extractor,
                        include_web=include_web,
                        use_nim_full=use_nim,
                    )
                    if result:
                        telemetry.companies_succeeded += 1
                    else:
                        telemetry.companies_failed += 1
                except Exception as e:
                    logger.error(f"Error processing {orgnr_str}: {e}")
                    telemetry.companies_failed += 1
                finally:
                    telemetry.companies_processed += 1

        tasks = [_worker(orgnr) for orgnr in orgnrs]
        await asyncio.gather(*tasks)

    duration = time.time() - start_ts
    telemetry.end_time = datetime.now(timezone.utc).isoformat()
    telemetry.total_duration_seconds = round(duration, 2)
    telemetry.total_http_requests = budget_tracker.total_requests
    telemetry.web_outbound_requests = budget_tracker.web_outbound_requests
    telemetry.http_requests_per_company = budget_tracker.requests_per_company
    telemetry.llm_api_calls = extractor.telemetry.llm_api_calls
    telemetry.nvidia_primary_calls = extractor.telemetry.primary_calls
    telemetry.nvidia_fallback_calls = extractor.telemetry.fallback_calls
    telemetry.total_llm_input_tokens = extractor.telemetry.input_tokens
    telemetry.total_llm_output_tokens = extractor.telemetry.output_tokens
    telemetry.total_llm_cost_usd = round(extractor.telemetry.total_cost_usd, 4)
    telemetry.anti_fabrication_rejections = extractor.telemetry.fabrication_rejections

    # Verify competition constraints
    for org, count in telemetry.http_requests_per_company.items():
        if count > settings.MAX_HTTP_REQUESTS_PER_COMPANY:
            telemetry.budget_violations.append(
                f"Company {org} exceeded HTTP cap: {count}/{settings.MAX_HTTP_REQUESTS_PER_COMPANY}"
            )

    store.save_telemetry(telemetry)
    return telemetry


@click.group()
def cli():
    """Signalpost Agent - Verifiable Norwegian Company Intelligence powered by NVIDIA NIM."""
    pass


@cli.command()
@click.option("--orgnr", required=True, help="Norwegian 9-digit company number (e.g. 923609016).")
@click.option("--no-web", is_flag=True, default=False, help="Disable external web discovery.")
def run(orgnr: str, no_web: bool):
    """Run extraction pipeline for a single enterprise number."""
    settings.ensure_directories()
    clean_orgnr = try_normalize_orgnr(orgnr)
    if not clean_orgnr:
        click.echo(f"Error: '{orgnr}' failed Modulo-11 checksum validation.", err=True)
        sys.exit(1)

    engine_desc = (
        f"NVIDIA NIM ({settings.NVIDIA_PRIMARY_MODEL})"
        if settings.NVIDIA_API_KEY
        else "Offline Heuristic (Set NVIDIA_API_KEY in .env to enable live NIM calls)"
    )
    click.echo(f"Initializing Signalpost Agent for enterprise {clean_orgnr}...")
    click.echo(f"Active LLM Engine: {engine_desc}")

    async def _run():
        budget = HttpBudgetTracker.create_for_batch(batch_size=1)
        store = ProfileStore()
        extractor = FactExtractor()
        async with BudgetedAsyncClient(budget_tracker=budget) as client:
            return await process_single_company(
                raw_orgnr=clean_orgnr,
                client=client,
                store=store,
                extractor=extractor,
                include_web=not no_web,
                use_nim_full=True,
            )

    profile = asyncio.run(_run())
    if profile:
        click.echo(f"\n[SUCCESS] Profile generated for {profile.legal_name} ({profile.orgnr})")
        click.echo(f"Profile saved to: {settings.PROFILES_DIR / f'{profile.orgnr}.json'}")
        if profile.executive_summary:
            click.echo(f"\nEXECUTIVE SUMMARY:\n{profile.executive_summary}")
        if profile.known_unknowns:
            click.echo("\nKNOWN UNKNOWNS:")
            for ku in profile.known_unknowns[:3]:
                click.echo(f"  - {ku}")
        click.echo(f"\nExplanation: {profile.profile_verification_explanation}")
        click.echo(f"Facts extracted: {len(profile.facts)}")
    else:
        click.echo("[ERROR] Profile generation failed.", err=True)
        sys.exit(1)


@cli.command()
@click.option("--file", "file_path", required=True, type=click.Path(exists=True), help="Path to text file with orgnrs.")
@click.option("--concurrency", default=settings.MAX_CONCURRENT_COMPANIES, help="Concurrent company tasks.")
@click.option("--no-web", is_flag=True, default=False, help="Disable external web discovery.")
def batch(file_path: str, concurrency: int, no_web: bool):
    """Run batch extraction for a list of enterprise numbers from a file."""
    with open(file_path, "r", encoding="utf-8") as f:
        lines = [line.strip() for line in f if line.strip() and not line.startswith("#")]

    engine_desc = (
        f"NVIDIA NIM ({settings.NVIDIA_PRIMARY_MODEL})"
        if settings.NVIDIA_API_KEY
        else "Offline Heuristic (Set NVIDIA_API_KEY in .env to enable live NIM calls)"
    )
    click.echo(f"Loaded {len(lines)} enterprise numbers from {file_path}. Processing with concurrency {concurrency}...")
    click.echo(f"Active LLM Engine: {engine_desc}")

    telemetry = asyncio.run(run_batch(lines, concurrency=concurrency, include_web=not no_web))

    click.echo("\n" + "=" * 60)
    click.echo("BATCH RUN SUMMARY (NVIDIA NIM POWERED)")
    click.echo("=" * 60)
    click.echo(f"Total Duration:        {telemetry.total_duration_seconds:.2f} seconds")
    click.echo(f"Processed:             {telemetry.companies_processed}")
    click.echo(f"Succeeded:             {telemetry.companies_succeeded}")
    click.echo(f"Failed:                {telemetry.companies_failed}")
    click.echo(f"Web Outbound Calls:    {telemetry.web_outbound_requests}")
    click.echo(f"LLM API Calls:         {telemetry.llm_api_calls} (Primary: {telemetry.nvidia_primary_calls}, Fallback: {telemetry.nvidia_fallback_calls})")
    click.echo(f"Total LLM Cost:        ${telemetry.total_llm_cost_usd:.4f} USD")
    click.echo(f"Anti-Fab Rejections:   {telemetry.anti_fabrication_rejections}")
    click.echo(f"Budget Violations:     {len(telemetry.budget_violations)}")
    click.echo(f"Telemetry Manifest:    {settings.TELEMETRY_DIR / 'telemetry_manifest.json'}")
    if telemetry.llm_api_calls == 0 and not settings.NVIDIA_API_KEY:
        click.echo("-" * 60)
        click.echo("INFO: NVIDIA_API_KEY is not set. Profiles were generated with the deterministic heuristic engine.")
        click.echo("To enable live NVIDIA NIM calls, add 'NVIDIA_API_KEY=nvapi-...' to .env or set $env:NVIDIA_API_KEY.")


@cli.command("seed-1000")
@click.option("--limit", default=1000, help="Number of active enterprises to fetch and process.")
@click.option("--concurrency", default=25, help="Concurrent company tasks.")
@click.option("--no-web", is_flag=True, default=False, help="Disable external web discovery.")
def seed_1000(limit: int, concurrency: int, no_web: bool):
    """Pulls active enterprise numbers from Brønnøysund and builds profiles."""
    click.echo(f"Fetching {limit} active enterprise numbers from Brønnøysund API...")

    async def _fetch_and_run():
        budget = HttpBudgetTracker.create_for_batch(batch_size=limit)
        async with BudgetedAsyncClient(budget_tracker=budget) as client:
            brreg = BrregClient(client)
            orgnrs = await brreg.fetch_active_orgnrs(limit=limit)
            click.echo(f"Retrieved {len(orgnrs)} active enterprise numbers. Starting processing...")
            return await run_batch(orgnrs, concurrency=concurrency, include_web=not no_web)

    telemetry = asyncio.run(_fetch_and_run())
    click.echo(f"\nCompleted {limit} seed run in {telemetry.total_duration_seconds}s. Succeeded: {telemetry.companies_succeeded}/{telemetry.companies_processed}")


def main():
    cli()


if __name__ == "__main__":
    main()
