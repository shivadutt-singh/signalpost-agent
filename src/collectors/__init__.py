"""
Collectors and network clients for registry and web discovery.
"""

from src.collectors.http_budget import (
    HttpBudgetTracker,
    AsyncHttpBudgetClient,
    CompanyHttpBudgetExceededError,
    GlobalHttpBudgetExceededError,
)
from src.collectors.brreg_client import BrregClient
from src.collectors.web_discovery import WebDiscoveryCollector, WebPageContent

__all__ = [
    "HttpBudgetTracker",
    "AsyncHttpBudgetClient",
    "CompanyHttpBudgetExceededError",
    "GlobalHttpBudgetExceededError",
    "BrregClient",
    "WebDiscoveryCollector",
    "WebPageContent",
]
