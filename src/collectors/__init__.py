"""
Collectors and network clients for registry and web discovery.
"""

from src.collectors.http_budget import (
    HttpBudgetTracker,
    BudgetedAsyncClient,
    CompanyHttpBudgetExceededError,
    GlobalHttpBudgetExceededError,
)
from src.collectors.brreg_client import BrregClient
from src.collectors.financials_client import FinancialsClient
from src.collectors.kunngjoringer_client import KunngjoringItem, KunngjoringerClient
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
