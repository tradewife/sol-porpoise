#!/bin/bash
# cron_hourly.sh — Single cron entry point for the Imperial live-paper trading agent.
#
# Runs one hourly cycle: live-paper scan (which includes auto-evaluate of
# prior open orders before fetching new data).
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

# Run the live-paper scan (includes auto-evaluate of open orders from
# previous cycle inline at start of _run_live_paper).
EXIT_CODE=0
./scripts/run_scan.sh --mode live-paper || EXIT_CODE=$?

echo "=== Imperial hourly scan finished at $(date -Iseconds) exit_code=${EXIT_CODE} ==="

exit ${EXIT_CODE}
