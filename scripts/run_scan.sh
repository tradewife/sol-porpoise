#!/bin/bash
# run_scan.sh — Cron entry point for the Imperial live-paper trading agent.
#
# Usage:
#   ./scripts/run_scan.sh --mode plumbing-dry-run
#   ./scripts/run_scan.sh --mode live-paper
#
# Exits 0 on success.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"

cd "$PROJECT_ROOT"

exec .venv/bin/python -m engine.run_scan "$@"
