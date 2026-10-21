import pytest
import asyncio
from src.collectors.http_budget import HttpBudgetTracker, AsyncHttpBudgetClient

@pytest.mark.asyncio
async def test_concurrent_requests_use_same_future():
    tracker = HttpBudgetTracker()

    class MockResponse:
        def __init__(self):
            self.status_code = 200
            self.content = b"OK"
            self.headers = {}

    class MockClient:
        async def get(self, url, **kwargs):
            await asyncio.sleep(0.1)
            return MockResponse()

        def stream(self, method, url, **kwargs):
            class StreamContext:
                async def __aenter__(self): return self
                async def __aexit__(self, *args): pass
                @property
                def headers(self): return {}
                async def aiter_bytes(self, chunk_size):
                    yield b"OK"
            return StreamContext()

    client = AsyncHttpBudgetClient(budget_tracker=tracker)
    client._client = MockClient()

    url = "https://example.com"
    tasks = [client.get(url, orgnr="123456789") for _ in range(5)]

    results = await asyncio.gather(*tasks)

    assert tracker.get_company_count("123456789") == 1
    assert tracker.total_requests == 1

    for r in results:
        assert r.status_code == 200
