import pytest
import asyncio
from src.cli import process_single_company
from src.collectors.http_budget import AsyncHttpBudgetClient
from src.state.profile_store import ProfileStore
from src.extraction.llm_extractor import FactExtractor
from src.models.schema import CompanyProfile
import tempfile
from pathlib import Path

@pytest.mark.asyncio
async def test_zero_drop_fatal_crash_fallback():
    # Provide a completely broken orgnr to trigger padding
    invalid_orgnr = "12345"

    with tempfile.TemporaryDirectory() as tmpdir:
        store = ProfileStore(profiles_dir=Path(tmpdir), deltas_dir=Path(tmpdir), telemetry_dir=Path(tmpdir))
        extractor = FactExtractor(provider="heuristic")

        # We also want to simulate an exception to verify the fatal crash fallback
        # Let's break the client or brreg somehow? The invalid orgnr itself is enough to test the pad logic.
        # But to test the exception block, we can pass a None client which will crash `BrregClient`.

        profile = await process_single_company(
            raw_orgnr=invalid_orgnr,
            client=None, # Will raise AttributeError when trying to call client.get
            store=store,
            extractor=extractor,
            include_web=False
        )

        assert profile is not None
        assert profile.orgnr == "000012345"
        assert profile.canonical_status == "UNKNOWN"
        assert "Profile generated via emergency fallback" in profile.profile_verification_explanation
        assert "All company information remains unconfirmed" in profile.known_unknowns[0]
