#!/usr/bin/env bash
# ==============================================================================
# Signalpost Agent - Container & Sandbox Runner Script (JBOX & CLI Compatible)
# ==============================================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "${SCRIPT_DIR}"

PYTHON="${PYTHON:-python3}"
if ! command -v "${PYTHON}" &> /dev/null; then
    PYTHON="python"
fi

# Ensure output and data directories exist
mkdir -p output/profiles output/deltas output/telemetry data

# Case 1: No arguments provided (Automated Sandboxed / Non-interactive Evaluation)
if [ $# -eq 0 ]; then
    if [ -f "sample_orgnrs.txt" ]; then
        echo "[Signalpost JBOX Runner] No arguments provided. Running default evaluation batch on sample_orgnrs.txt..."
        exec "${PYTHON}" -m src.cli batch --file sample_orgnrs.txt --concurrency 5
    elif [ -f "test_10_diverse.txt" ]; then
        echo "[Signalpost JBOX Runner] Running default evaluation batch on test_10_diverse.txt..."
        exec "${PYTHON}" -m src.cli batch --file test_10_diverse.txt --concurrency 5
    else
        echo "[Signalpost JBOX Runner] Running default single evaluation on Equinor (923609016)..."
        exec "${PYTHON}" -m src.cli run --orgnr 923609016
    fi
fi

# Case 2: User or Judge passed a 9-digit orgnr directly
if [[ "$1" =~ ^[0-9]{9}$ ]]; then
    ORGNR="$1"
    shift
    exec "${PYTHON}" -m src.cli run --orgnr "${ORGNR}" "$@"
fi

# Case 3: User or Judge passed a batch text file directly
if [[ "$1" == *.txt ]] || [ -f "$1" ]; then
    BATCH_FILE="$1"
    shift
    exec "${PYTHON}" -m src.cli batch --file "${BATCH_FILE}" "$@"
fi

# Case 4: Shortcut subcommands
case "$1" in
    test|tests)
        shift
        exec "${PYTHON}" -m pytest tests -v --tb=short "$@"
        ;;
    audit|calibration)
        shift
        exec "${PYTHON}" -m src.scripts.build_calibration_audit "$@"
        ;;
    html|export-html|static)
        shift
        exec "${PYTHON}" -m src.web.static_builder "$@"
        ;;
    serve|web)
        shift
        exec "${PYTHON}" -m src.cli serve "$@"
        ;;
    *)
        exec "${PYTHON}" -m src.cli "$@"
        ;;
esac
