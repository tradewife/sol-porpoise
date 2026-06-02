#!/bin/bash
# trial_stop.sh — Stop the 24-hour live-paper trial and produce final summary.
#
# 1. Removes cron entry for imperial-agent
# 2. Runs final evaluate-outcomes for any remaining open orders
# 3. Runs weekly review on trial data
# 4. Restores original config from data/trial_config_backup/
# 5. Prints trial summary
#
# Usage:
#   ./scripts/trial_stop.sh
#
# Exits 0 on success.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"

cd "$PROJECT_ROOT"

BACKUP_DIR="data/trial_config_backup"

echo "=== Imperial Trial Stop ==="
echo "Stopping trial at $(date -Iseconds)"
echo ""

# Step 1: Remove cron entry
echo "Step 1: Removing cron entry"
if crontab -l 2>/dev/null | grep -q "imperial-agent"; then
    crontab -l 2>/dev/null | grep -v "imperial-agent" | crontab -
    echo "  Cron entry removed"
else
    echo "  No imperial-agent cron entry found (already removed or never installed)"
fi
echo ""

# Step 2: Run final evaluate-outcomes for remaining open orders
echo "Step 2: Running final evaluate-outcomes"
if [ -f "memory/mission_state.json" ]; then
    HAS_OPEN=$(.venv/bin/python -c "
import json
state = json.load(open('memory/mission_state.json'))
orders = state.get('open_paper_orders', [])
print(len(orders))
" 2>/dev/null || echo "0")
    if [ "${HAS_OPEN}" != "0" ]; then
        echo "  Found ${HAS_OPEN} open order(s), evaluating..."
        ./scripts/evaluate_outcomes.sh || echo "  Warning: evaluate-outcomes exited with error (continuing)"
    else
        echo "  No open orders to evaluate"
    fi
else
    echo "  No mission_state.json found, skipping"
fi
echo ""

# Step 3: Run weekly review on trial data
echo "Step 3: Running weekly review on trial data"
if [ -f "ledgers/outcomes.csv" ] && [ -s "ledgers/outcomes.csv" ]; then
    ./scripts/weekly_review.sh || echo "  Warning: weekly-review exited with error (continuing)"
else
    echo "  No outcomes data yet, skipping weekly review"
fi
echo ""

# Step 4: Restore original config from backup
echo "Step 4: Restoring original config from backup"
if [ -f "${BACKUP_DIR}/run.yaml" ] && [ -f "${BACKUP_DIR}/risk.yaml" ]; then
    cp "${BACKUP_DIR}/run.yaml" config/run.yaml
    cp "${BACKUP_DIR}/risk.yaml" config/risk.yaml
    echo "  Restored config/run.yaml from backup"
    echo "  Restored config/risk.yaml from backup"
else
    echo "  Warning: No backup found in ${BACKUP_DIR}/, config not restored"
fi
echo ""

# Step 5: Print trial summary
echo "=== Trial Summary ==="

# Count scans from reports directory
SCAN_COUNT=0
if [ -d "reports" ]; then
    SCAN_COUNT=$(find reports/ -name "*_report.md" 2>/dev/null | wc -l)
fi

# Count orders from paper_orders.csv
ORDER_COUNT=0
if [ -f "ledgers/paper_orders.csv" ] && [ -s "ledgers/paper_orders.csv" ]; then
    ORDER_COUNT=$(.venv/bin/python -c "
import csv
with open('ledgers/paper_orders.csv') as f:
    reader = csv.DictReader(f)
    rows = list(reader)
print(len(rows))
" 2>/dev/null || echo "0")
fi

# Count outcomes from outcomes.csv
OUTCOME_COUNT=0
WINS=0
LOSSES=0
AVG_R="N/A"
if [ -f "ledgers/outcomes.csv" ] && [ -s "ledgers/outcomes.csv" ]; then
    STATS=$(.venv/bin/python -c "
import csv
with open('ledgers/outcomes.csv') as f:
    reader = csv.DictReader(f)
    rows = list(reader)
total = len(rows)
wins = sum(1 for r in rows if float(r.get('result_R', 0)) > 0)
losses = sum(1 for r in rows if float(r.get('result_R', 0)) < 0)
if total > 0:
    avg_r = sum(float(r.get('result_R', 0)) for r in rows) / total
else:
    avg_r = 0
print(f'{total} {wins} {losses} {avg_r:.2f}')
" 2>/dev/null || echo "0 0 0 N/A")
    OUTCOME_COUNT=$(echo "${STATS}" | awk '{print $1}')
    WINS=$(echo "${STATS}" | awk '{print $2}')
    LOSSES=$(echo "${STATS}" | awk '{print $3}')
    AVG_R=$(echo "${STATS}" | awk '{print $4}')
fi

echo "  Scans completed: ${SCAN_COUNT}"
echo "  Paper orders placed: ${ORDER_COUNT}"
echo "  Outcomes evaluated: ${OUTCOME_COUNT}"
echo "  Wins: ${WINS}  Losses: ${LOSSES}"
echo "  Average R: ${AVG_R}"
echo ""
echo "Trial data preserved in ledgers/ and reports/ directories."
echo "Config restored to pre-trial state."
echo ""
echo "=== Trial Stop Complete ==="
