#!/usr/bin/env bash
# ==============================================================================
# Signalpost Agent - CLI Runner Script
# ==============================================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "${SCRIPT_DIR}"

PYTHON="${PYTHON:-python3}"
if ! command -v "${PYTHON}" &> /dev/null; then
    PYTHON="python"
fi

# Ensure output directories exist
mkdir -p output/profiles output/deltas output/telemetry

if [ $# -eq 0 ]; then
    echo "Usage: ./run_agent.sh [run --orgnr <NUMBER> | batch --file <PATH> | seed-1000]"
    echo ""
    echo "Examples:"
    echo "  ./run_agent.sh run --orgnr 923609016"
    echo "  ./run_agent.sh batch --file sample_orgnrs.txt --concurrency 10"
    echo "  ./run_agent.sh seed-1000 --limit 1000 --concurrency 25"
    exit 0
fi

exec "${PYTHON}" -m src.cli "$@"
