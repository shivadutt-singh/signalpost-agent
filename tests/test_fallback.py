import pytest
import asyncio
from src.collectors.http_budget import AsyncHttpBudgetClient, HttpBudgetTracker
from src.collectors.brreg_client import BrregClient
import httpx

@pytest.mark.asyncio
async def test_underenhet_fallback():
    class MockClient:
        async def get(self, url, **kwargs):
            if "underenheter" in url:
                return httpx.Response(200, json={"navn": "Test Underenhet", "organisasjonsnummer": "923609016"})
            return httpx.Response(404)

    tracker = HttpBudgetTracker()
    client = AsyncHttpBudgetClient(budget_tracker=tracker)
    client._client = MockClient()

    brreg = BrregClient(client)

    # 404 on enhet
    enhet = await brreg.get_enhet("923609016")
    assert enhet is None

    # Fallback underenhet
    underenhet = await brreg.get_underenhet("923609016")
    assert underenhet is not None
    assert underenhet["navn"] == "Test Underenhet"
