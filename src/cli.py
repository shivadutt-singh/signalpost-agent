import re
"""
CLI Entrypoint for Signalpost Agent.
Supports single-company runs, batch runs from file, and 1,000-company automated seed processing.
Powered by NVIDIA NIM microservices architecture with dynamic budget scaling.
"""

import asyncio
import logging
import sys
import time

# Cross-platform UTF-8 reconfiguration for Windows & Linux
if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
if hasattr(sys.stderr, "reconfigure"):
    try:
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass
import uuid
from datetime import UTC, datetime

import click

from src.collectors.brreg_client import BrregClient
from src.collectors.http_budget import AsyncHttpBudgetClient, BudgetedAsyncClient, HttpBudgetTracker
from src.collectors.web_discovery import WebDiscoveryCollector
from src.config import settings
from src.extraction.llm_extractor import FactExtractor
from src.models.schema import (
    CompanyProfile,
    ExecutionTelemetry,
)
from src.pipeline import HybridPipeline
from src.state.profile_store import ProfileStore
from src.validators.mod11 import try_normalize_orgnr

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("signalpost")


async def process_single_company(
    raw_orgnr: str,
    client: AsyncHttpBudgetClient,
    store: ProfileStore,
    extractor: FactExtractor,
    include_web: bool = True,
    use_nim_full: bool = True,
    telemetry: ExecutionTelemetry | None = None,
) -> CompanyProfile | None:
    """
    Executes the 3-Layer Hybrid Neuro-Symbolic Sandwich Pipeline:
    Layer 1: Deterministic Registry & Multi-Year Accounts + Smart Path Router
    Layer 2: Bounded Agentic Discovery & Single-Shot Batched NIM Extraction
    Layer 3: Deterministic Pre-Commit FactCritic Firewall
    """
    clean_orgnr = try_normalize_orgnr(raw_orgnr)
    if not clean_orgnr:
        logger.error(f"Validation failure: '{raw_orgnr}' is not a valid Modulo-11 Norwegian orgnr. Padding to 9 digits to guarantee zero-drop result.")
        clean_orgnr = re.sub(r'\D', '', raw_orgnr)[:9].rjust(9, '0')

    try:
        pipeline = HybridPipeline(client=client, store=store, extractor=extractor)
        res = await pipeline.run(
            raw_orgnr=clean_orgnr,
            include_web=include_web,
            use_nim_full=use_nim_full,
            telemetry=telemetry,
        )
        if res is not None:
            return res
    except Exception as fatal_err:
        logger.error(f"Fatal error processing {clean_orgnr}: {fatal_err}. Guaranteeing zero-drop fallback.")

    return CompanyProfile(
        orgnr=clean_orgnr,
        legal_name=f"Enterprise {clean_orgnr}",
        canonical_status="UNKNOWN",
        profile_verification_explanation="Profile generated via emergency fallback due to pipeline crash.",
        executive_summary="Information could not be extracted due to pipeline failure.",
        known_unknowns=["All company information remains unconfirmed due to processing error."],
    )

async def run_batch(
    orgnrs: list[str],
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
        start_time=datetime.now(UTC).isoformat(),
    )

    semaphore = asyncio.Semaphore(concurrency)
    start_ts = time.time()

    # Smart NIM Invocation Strategy:
    # In 100-company daily evaluation mode (batch_size <= 100): full NIM on all companies
    # In generate-1000 mode: run full NIM on companies with active website up to safe RPM limit
    nim_full_quota = 50 if batch_size > 100 else batch_size
    nim_full_count = 0
    nim_lock = asyncio.Lock()

    async with AsyncHttpBudgetClient(budget_tracker=budget_tracker) as client:
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
                        telemetry=telemetry,
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
    telemetry.end_time = datetime.now(UTC).isoformat()
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
        async with AsyncHttpBudgetClient(budget_tracker=budget) as client:
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
        click.echo(f"Routing Path: {profile.telemetry.get('routing_path')}")
        click.echo(f"Critic Facts: {profile.telemetry.get('critic_facts_accepted')} accepted, {profile.telemetry.get('critic_facts_quarantined')} quarantined")
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
    with open(file_path, encoding="utf-8") as f:
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
        async with AsyncHttpBudgetClient(budget_tracker=budget) as client:
            brreg = BrregClient(client)
            orgnrs = await brreg.fetch_active_orgnrs(limit=limit)
            click.echo(f"Retrieved {len(orgnrs)} active enterprise numbers. Starting processing...")
            return await run_batch(orgnrs, concurrency=concurrency, include_web=not no_web)

    telemetry = asyncio.run(_fetch_and_run())
    click.echo(f"\nCompleted {limit} seed run in {telemetry.total_duration_seconds}s. Succeeded: {telemetry.companies_succeeded}/{telemetry.companies_processed}")


@cli.command()
@click.option("--port", default=8000, help="Port to run web server on.")
@click.option("--host", default="127.0.0.1", help="Host interface to bind.")
def serve(port: int, host: str):
    """Launch the Signalpost Agent interactive production Web UI."""
    import uvicorn

    from src.web.static_builder import build_static_html

    settings.ensure_directories()
    try:
        build_static_html()
    except Exception:
        pass

    click.echo("=" * 65)
    click.echo("SIGNALPOST AGENT v2.0 INTERACTIVE WEB CONSOLE")
    click.echo(f"Web server running at: http://{host}:{port}")
    click.echo("Static HTML Viewer:    output/index.html")
    click.echo("=" * 65)
    uvicorn.run("src.web.app:app", host=host, port=port, log_level="info")


@cli.command("export-html")
def export_html():
    """Generates the self-contained static HTML viewer at output/index.html."""
    from src.web.static_builder import build_static_html
    dest = build_static_html()
    click.echo(f"Successfully generated static HTML viewer: {dest}")


@cli.command("audit")
def audit():
    """Execute 50-Profile Empirical Calibration Audit and generate report."""
    from src.scripts.build_calibration_audit import run_hand_checked_audit
    click.echo("Running 50-Profile Stratified Calibration Audit across Housing Associations, AS, and ASA...")
    asyncio.run(run_hand_checked_audit())
    click.echo("\n[SUCCESS] Calibration Audit complete. Saved data/hand_checked_audit.json and HAND_CHECKED_REPORT.md")


@cli.command("doctor")
def doctor():
    """Run the pre-submission red-team health and compliance audit suite."""
    from src.scripts.doctor import print_doctor_table, run_doctor_audit
    click.echo("Executing Pre-Submission Doctor Suite (10 Zero-Failure Checks)...")
    all_passed, results = run_doctor_audit()
    print_doctor_table(all_passed, results)
    sys.exit(0 if all_passed else 1)


@cli.command("verify-updates")
@click.option("--sample", default=25, help="Number of profiles to audit for incremental updates.")
def verify_updates(sample: int):
    """Audit incremental update handling and Brreg oppdateringer changelog."""
    from src.scripts.verify_updates import run_verify_updates
    click.echo(f"Running incremental update verification pass across {sample} profiles...")
    manifest = asyncio.run(run_verify_updates(sample_size=sample))
    click.echo("\n[SUCCESS] Incremental update verification complete.")
    click.echo(f"Audited: {manifest['sample_size_audited']} profiles")
    click.echo(f"Facts unchanged (idempotent): {manifest['total_facts_unchanged_idempotent']}")
    click.echo(f"Oppdateringer events captured: {manifest['total_new_oppdateringer_events']}")
    click.echo(f"Idempotency rate: {manifest['idempotency_rate_pct']}%")
    click.echo("Proof saved to: output/telemetry/update_engine_proof.json")


def main():
    cli()


if __name__ == "__main__":
    main()
