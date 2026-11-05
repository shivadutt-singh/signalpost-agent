"""
Self-Contained Static HTML Dashboard Exporter for Signalpost Agent (v6.0 Fail-Proof Edition).

Embeds 100% of stored profiles and empirical calibration audit data into output/index.html
allowing zero-dependency offline browser inspection.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from src.web.app import get_html_template

logger = logging.getLogger("signalpost.static_exporter")


def build_static_html(
    output_path: Path | None = None,
    profiles_dir: Path | None = None,
    audit_file: Path | None = None,
) -> Path:
    """Assembles all profiles and hand-checked audit matrix into a standalone single-file HTML."""
    out_file = output_path or Path("output/index.html")
    p_dir = profiles_dir or Path("output/profiles")
    a_path = audit_file or Path("data/hand_checked_audit.json")

    out_file.parent.mkdir(parents=True, exist_ok=True)

    # 1. Load profiles
    profiles_summary: list[dict[str, Any]] = []
    full_profiles_map: dict[str, Any] = {}

    if p_dir.exists():
        for p_file in sorted(p_dir.glob("*.json")):
            try:
                with open(p_file, encoding="utf-8") as f:
                    data = json.load(f)

                orgnr = str(data.get("orgnr", p_file.stem))
                name = data.get("canonical_name") or data.get("legal_name", f"Entity {orgnr}")
                fin_years = len(data.get("financial_history", []))
                jobs_count = len(data.get("job_listings", [])) or (1 if "operations.jobs" in data.get("facts", {}) else 0)

                profiles_summary.append({
                    "orgnr": orgnr,
                    "legal_name": name,
                    "canonical_status": data.get("status", "ACTIVE"),
                    "financial_years_count": fin_years,
                    "has_jobs": jobs_count > 0,
                    "updated_at": data.get("updated_at", ""),
                })
                full_profiles_map[orgnr] = data
            except Exception as exc:
                logger.warning(f"Failed to read profile {p_file}: {exc}")

    # 2. Load audit matrix
    audit_data = {}
    if a_path.exists():
        try:
            with open(a_path, encoding="utf-8") as f:
                audit_data = json.load(f)
        except Exception as exc:
            logger.warning(f"Failed to load audit file: {exc}")

    initial_payload = {
        "profiles_summary": profiles_summary,
        "full_profiles": full_profiles_map,
        "audit_data": audit_data,
        "version": "6.0.0",
        "edition": "v6.0 Fail-Proof Edition",
    }

    payload_json = json.dumps(initial_payload, ensure_ascii=False)
    html_content = get_html_template(initial_data_json=payload_json, is_static=True)

    with open(out_file, "w", encoding="utf-8") as f:
        f.write(html_content)

    logger.info(f"Successfully generated standalone static HTML dashboard: {out_file} ({len(profiles_summary)} profiles embedded)")
    return out_file


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    dest = build_static_html()
    print(f"Exported static HTML dashboard to: {dest}")
