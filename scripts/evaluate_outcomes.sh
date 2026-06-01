#!/bin/bash
# evaluate_outcomes.sh — Evaluate open paper orders against post-order market data.
#
# Usage:
#   ./scripts/evaluate_outcomes.sh
#
# Exits 0 on success.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"

cd "$PROJECT_ROOT"

exec .venv/bin/python -m engine.run_scan --mode evaluate-outcomes "$@"
