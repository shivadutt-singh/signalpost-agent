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
from src.collectors.web_discovery import WebDiscoveryCollector, WebPageContent

__all__ = [
    "HttpBudgetTracker",
    "BudgetedAsyncClient",
    "CompanyHttpBudgetExceededError",
    "GlobalHttpBudgetExceededError",
    "BrregClient",
    "WebDiscoveryCollector",
    "WebPageContent",
]
