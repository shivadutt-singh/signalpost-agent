"""
Collectors and network clients for registry and web discovery.
"""

from src.collectors.brreg_client import BrregClient
from src.collectors.financials_client import FinancialsClient
from src.collectors.http_budget import (
    BudgetedAsyncClient,
    CompanyHttpBudgetExceededError,
    GlobalHttpBudgetExceededError,
    HttpBudgetTracker,
)
from src.collectors.kunngjoringer_client import KunngjoringerClient, KunngjoringItem
from src.collectors.web_discovery import WebDiscoveryCollector, WebPageContent

__all__ = [
    "HttpBudgetTracker",
    "BudgetedAsyncClient",
    "CompanyHttpBudgetExceededError",
    "GlobalHttpBudgetExceededError",
    "BrregClient",
    "FinancialsClient",
    "KunngjoringItem",
    "KunngjoringerClient",
    "WebDiscoveryCollector",
    "WebPageContent",
]
