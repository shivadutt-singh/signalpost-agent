"""Presentation and Auditor Web UI Layer for Signalpost Agent (v6.0 Fail-Proof Edition)."""

from src.web.app import app
from src.web.static_exporter import build_static_html

__all__ = [
    "app",
    "build_static_html",
]
