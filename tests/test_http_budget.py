import pytest
import asyncio
from src.collectors.http_budget import HttpBudgetTracker, AsyncHttpBudgetClient
import httpx

@pytest.mark.asyncio
async def test_concurrent_requests_use_same_future():
    tracker = HttpBudgetTracker()

    class MockClient:
        async def get(self, url, **kwargs):
            await asyncio.sleep(0.1)
            return httpx.Response(200, text="OK")

    client = AsyncHttpBudgetClient(budget_tracker=tracker)
    client._client = MockClient()

    # Send 5 concurrent requests to the same URL
    url = "https://example.com"
    tasks = [client.get(url, orgnr="123456789") for _ in range(5)]

    results = await asyncio.gather(*tasks)

    # Budget should only increment by 1
    assert tracker.get_company_count("123456789") == 1
    assert tracker.total_requests == 1

    for r in results:
        assert r.status_code == 200
