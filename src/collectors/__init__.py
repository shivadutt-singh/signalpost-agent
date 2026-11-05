"""
Collectors and network clients for registry and web discovery.
"""

from src.collectors.brreg_client import BrregClient
from src.collectors.financials_client import FinancialsClient
from src.collectors.http_budget import (
    AsyncHttpBudgetClient,
    BudgetedAsyncClient,
    CompanyHttpBudgetExceededError,
    GlobalHttpBudgetExceededError,
    HttpBudgetTracker,
)
from src.collectors.kunngjoringer_client import KunngjoringItem, KunngjoringerClient
from src.collectors.web_discovery import WebDiscoveryCollector, WebPageContent

# Backwards compatibility alias
KunngjoringClient = KunngjoringerClient

__all__ = [
    "HttpBudgetTracker",
    "AsyncHttpBudgetClient",
    "BudgetedAsyncClient",
    "CompanyHttpBudgetExceededError",
    "GlobalHttpBudgetExceededError",
    "BrregClient",
    "FinancialsClient",
    "KunngjoringItem",
    "KunngjoringerClient",
    "KunngjoringClient",
    "WebDiscoveryCollector",
    "WebPageContent",
]
