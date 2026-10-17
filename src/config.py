"""
Global Configuration & Budget Thresholds for Signalpost Agent.
Strictly enforces resource caps, concurrency parameters, and cost accounting rates.
Equipped with NVIDIA NIM Microservices architecture and dynamic budget scaling.
"""

import os
from pathlib import Path
from typing import Literal
from dotenv import load_dotenv

# Load environment variables with override at the very top
load_dotenv(override=True)

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore"
    )

    # --------------------------------------------------------------------------
    # Critical Competition Resource Limits & Dynamic Scaling
    # --------------------------------------------------------------------------
    # Hard cap: Max 18 outbound HTTP requests per company
    MAX_HTTP_REQUESTS_PER_COMPANY: int = Field(default=18, description="Max outbound HTTP requests per company")
    # Base web request cap for 100-company evaluation mode (safety margin below 2000)
    MAX_GLOBAL_WEB_REQUESTS_100: int = Field(default=1950, description="Web request budget cap for <= 100 companies")
    # Default global HTTP request ceiling (dynamically adjusted by batch size in HttpBudgetTracker)
    GLOBAL_MAX_HTTP_REQUESTS: int = Field(default=2000, description="Default global outbound HTTP request budget")
    # Max cost cap per 100 companies: $9.50 USD (safety buffer strictly below competition $10.00 cap)
    MAX_EXTERNAL_COST_USD_PER_100: float = Field(default=9.50, description="Max external LLM budget per 100 companies")
    TARGET_EXTERNAL_COST_USD_PER_COMPANY: float = Field(default=0.015, description="Target LLM cost per company")

    # --------------------------------------------------------------------------
    # NVIDIA NIM Microservices Configuration
    # --------------------------------------------------------------------------
    NVIDIA_API_KEY: str = Field(
        default_factory=lambda: os.getenv("NVIDIA_API_KEY", ""),
        description="NVIDIA NIM API key"
    )
    NVIDIA_BASE_URL: str = Field(
        default_factory=lambda: os.getenv("NVIDIA_BASE_URL", "https://integrate.api.nvidia.com/v1"),
        description="NVIDIA NIM API Base URL"
    )
    NVIDIA_PRIMARY_MODEL: str = Field(
        default_factory=lambda: os.getenv("NVIDIA_MODEL", "nvidia/llama-3.1-nemotron-70b-instruct"),
        description="NVIDIA NIM Primary Model"
    )
    NVIDIA_FALLBACK_MODEL: str = Field(
        default_factory=lambda: os.getenv("NVIDIA_FALLBACK_MODEL", "meta/llama-3.2-11b-vision-instruct"),
        description="NVIDIA NIM Fallback Model for 429/5xx/410 errors"
    )
    NVIDIA_RPM_LIMIT: int = Field(
        default_factory=lambda: int(os.getenv("NVIDIA_RPM_LIMIT", "38")),
        description="Sliding-window RPM limit for NVIDIA NIM (protects against 40 RPM cap)"
    )

    # --------------------------------------------------------------------------
    # Realistic Cost Accounting Rates (USD per 1M Tokens)
    # NVIDIA NIM: $0.0006 per 1K tokens = $0.60 per 1M tokens
    # --------------------------------------------------------------------------
    NVIDIA_INPUT_COST_PER_M: float = 0.60
    NVIDIA_OUTPUT_COST_PER_M: float = 0.60

    # Fallback rates for Gemini / OpenAI if enabled
    GEMINI_INPUT_COST_PER_M: float = 0.10
    GEMINI_OUTPUT_COST_PER_M: float = 0.40
    OPENAI_INPUT_COST_PER_M: float = 0.15
    OPENAI_OUTPUT_COST_PER_M: float = 0.60

    # --------------------------------------------------------------------------
    # Active LLM Provider Configuration
    # --------------------------------------------------------------------------
    LLM_PROVIDER: Literal["nvidia", "gemini", "openai", "heuristic"] = Field(
        default_factory=lambda: (
            "nvidia" if os.getenv("NVIDIA_API_KEY")
            else "gemini" if os.getenv("GEMINI_API_KEY")
            else "openai" if os.getenv("OPENAI_API_KEY")
            else "heuristic"
        ),
        description="Active LLM provider. Defaults to 'nvidia' when NVIDIA_API_KEY is present."
    )
    GEMINI_API_KEY: str = Field(default_factory=lambda: os.getenv("GEMINI_API_KEY", ""))
    GEMINI_MODEL: str = Field(default="gemini-2.5-flash")
    OPENAI_API_KEY: str = Field(default_factory=lambda: os.getenv("OPENAI_API_KEY", ""))
    OPENAI_MODEL: str = Field(default="gpt-4o-mini")

    # --------------------------------------------------------------------------
    # Concurrency & Network Timeouts
    # --------------------------------------------------------------------------
    MAX_CONCURRENT_COMPANIES: int = Field(default=10, description="Max concurrent companies in asyncio pool")
    HTTP_TIMEOUT_SECONDS: float = Field(default=10.0, description="HTTP request timeout in seconds")
    HTTP_MAX_RETRIES: int = Field(default=3, description="Max retries for transient HTTP errors")
    USER_AGENT: str = Field(
        default="SignalpostAgent/1.0 (Norwegian Business Intelligence; +https://github.com/signalpost/signalpost-agent)",
        description="Outbound HTTP User-Agent header"
    )

    # --------------------------------------------------------------------------
    # Official Brønnøysund API Endpoints
    # --------------------------------------------------------------------------
    BRREG_BASE_URL: str = "https://data.brreg.no/enhetsregisteret/api"

    # --------------------------------------------------------------------------
    # Directory Paths
    # --------------------------------------------------------------------------
    OUTPUT_DIR: Path = Field(default=Path("./output"))
    PROFILES_DIR: Path = Field(default=Path("./output/profiles"))
    DELTAS_DIR: Path = Field(default=Path("./output/deltas"))
    TELEMETRY_DIR: Path = Field(default=Path("./output/telemetry"))

    def resolve_provider(self) -> str:
        """Determines effective LLM provider based on configured API keys."""
        if self.NVIDIA_API_KEY:
            return "nvidia"
        if self.GEMINI_API_KEY:
            return "gemini"
        if self.OPENAI_API_KEY:
            return "openai"
        return "heuristic"

    def ensure_directories(self) -> None:
        """Ensures all necessary output directories exist."""
        self.OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        self.PROFILES_DIR.mkdir(parents=True, exist_ok=True)
        self.DELTAS_DIR.mkdir(parents=True, exist_ok=True)
        self.TELEMETRY_DIR.mkdir(parents=True, exist_ok=True)


# Singleton settings instance
settings = Settings()
