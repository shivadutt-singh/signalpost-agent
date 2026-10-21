import pytest
from src.collectors.http_budget import AsyncHttpBudgetClient, HttpBudgetTracker
from src.collectors.brreg_client import BrregClient

@pytest.mark.asyncio
async def test_underenhet_fallback():
    class MockResponse:
        def __init__(self, code, json_data=None):
            self.status_code = code
            self.content = b"x" * 1024
            self.headers = {}
            self._json = json_data
        def json(self): return self._json

    class MockClient:
        async def get(self, url, **kwargs):
            if "underenheter" in url:
                return MockResponse(200, json_data={"navn": "Test Underenhet", "organisasjonsnummer": "923609016"})
            return MockResponse(404)
        def stream(self, method, url, **kwargs):
            class StreamContext:
                async def __aenter__(self): return self
                async def __aexit__(self, *args): pass
                @property
                def headers(self): return {}
                async def aiter_bytes(self, chunk_size):
                    yield b"x" * 1024
            return StreamContext()

    tracker = HttpBudgetTracker()
    client = AsyncHttpBudgetClient(budget_tracker=tracker)
    client._client = MockClient()

    brreg = BrregClient(client)

    enhet = await brreg.get_enhet("923609016")
    assert enhet is None

    underenhet = await brreg.get_underenhet("923609016")
    assert underenhet is not None
    assert underenhet["navn"] == "Test Underenhet"
