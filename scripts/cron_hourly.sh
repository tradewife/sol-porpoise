#!/bin/bash
# cron_hourly.sh — Single cron entry point for the Imperial live-paper trading agent.
#
# Runs BOTH accounts each hour:
#   1. Deterministic scan (live-paper, account: deterministic)
#   2. AI scan (ai-paper, account: ai)
#
# Each account has isolated ledgers, reports, and mission state under accounts/<id>/.
#
# Usage:
#   ./scripts/cron_hourly.sh
#
# Cron line (for human installation — never auto-installed):
#   0 * * * * /home/kt/imperial-agent/scripts/cron_hourly.sh >> /home/kt/imperial-agent/data/cron_hourly.log 2>&1
#
# Exits 0 on success.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"

cd "$PROJECT_ROOT"

echo "=== Imperial hourly scan started at $(date -Iseconds) ==="

# Run the deterministic scan on its own account
EXIT_CODE=0
./scripts/run_scan.sh --mode live-paper --account deterministic || EXIT_CODE=$?

echo "=== Deterministic scan finished at $(date -Iseconds) exit_code=${EXIT_CODE} ==="

# Run the AI scan on its own account. If no Droid/Hermes bridge response is
# available, ai-paper fails closed to no_trade after writing ai_prompt.txt.
AI_EXIT=0
./scripts/run_scan.sh --mode ai-paper --account ai || AI_EXIT=$?
echo "=== AI scan finished at $(date -Iseconds) exit_code=${AI_EXIT} ==="

echo "=== Imperial hourly cycle complete at $(date -Iseconds) ==="

if [ "${EXIT_CODE}" -ne 0 ]; then
    exit "${EXIT_CODE}"
fi
exit "${AI_EXIT}"
