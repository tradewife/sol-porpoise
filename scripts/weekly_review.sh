#!/bin/bash
# weekly_review.sh — Cron-ready weekly performance review entry point.
#
# Usage:
#   ./scripts/weekly_review.sh
#
# Reads ledgers/outcomes.csv, ledgers/signal_outcomes.csv, ledgers/paper_orders.csv
# and outputs a weekly performance summary.
#
# Exits 0 on success.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"

cd "$PROJECT_ROOT"

exec .venv/bin/python -m engine.weekly_review "$@"
