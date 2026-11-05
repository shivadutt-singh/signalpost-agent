"""
Self-Contained Static HTML Export Builder.
Generates output/index.html containing all company profiles and the hand-checked audit report
embedded directly into the HTML so evaluators can inspect profiles directly in any browser
with zero server or network setup required.
"""

import json
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional

from src.config import settings
from src.web.app import get_html_template

logger = logging.getLogger("signalpost.static_builder")


def build_static_html(
    output_path: Optional[Path] = None,
    profiles_dir: Optional[Path] = None,
    audit_file: Optional[Path] = None,
) -> Path:
    """
    Assembles all available profiles and calibration audit data into a single self-contained HTML file.
    """
    settings.ensure_directories()
    dest_path = output_path or (settings.OUTPUT_DIR / "index.html")
    p_dir = profiles_dir or settings.PROFILES_DIR
    a_path = audit_file or Path("./data/hand_checked_audit.json")

    # Load all profiles
    profiles: List[Dict[str, Any]] = []
    if p_dir.exists():
        for f in sorted(p_dir.glob("*.json")):
            try:
                with open(f, "r", encoding="utf-8") as fp:
                    data = json.load(fp)
                fin_history = data.get("financial_history", [])
                facts = data.get("facts", {})
                has_jobs = "operations.jobs" in facts

                profiles.append({
                    "orgnr": data.get("orgnr"),
                    "legal_name": data.get("legal_name"),
                    "canonical_status": data.get("canonical_status", "ACTIVE"),
                    "financial_years_count": len(fin_history),
                    "has_jobs": has_jobs,
                    "created_at": data.get("created_at"),
                    "updated_at": data.get("updated_at"),
                    "executive_summary": data.get("executive_summary"),
                    "known_unknowns": data.get("known_unknowns", []),
                    "financial_history": fin_history,
                    "facts": facts,
                    "metadata": data.get("metadata", {}),
                    "profile_verification_explanation": data.get("profile_verification_explanation"),
                    "schema_version": data.get("schema_version"),
                })
            except Exception as e:
                logger.debug(f"Error reading profile {f}: {e}")

    # Load hand-checked audit report if exists
    audit_data = None
    if a_path.exists():
        try:
            with open(a_path, "r", encoding="utf-8") as fp:
                audit_data = json.load(fp)
        except Exception as e:
            logger.debug(f"Error reading audit file {a_path}: {e}")

    embedded_payload = {
        "profiles": profiles,
        "audit": audit_data,
    }

    raw_json_str = json.dumps(embedded_payload, ensure_ascii=False)
    html_content = get_html_template(initial_data_json=raw_json_str, is_static=True)

    dest_path.parent.mkdir(parents=True, exist_ok=True)
    with open(dest_path, "w", encoding="utf-8") as fp:
        fp.write(html_content)

    logger.info(f"Generated static HTML viewer at: {dest_path} ({len(profiles)} profiles embedded)")
    return dest_path


if __name__ == "__main__":
    build_static_html()
