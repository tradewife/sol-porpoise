#!/bin/bash
# trial_start.sh — Initialize the 24-hour live-paper trial.
#
# 1. Backs up current config/ to data/trial_config_backup/
# 2. Applies hourly trial config (equity 1000, max 4 concurrent, 45-min timeout)
# 3. Verifies with plumbing-dry-run
# 4. Prints cron line for human to install
#
# Usage:
#   ./scripts/trial_start.sh
#
# Exits 0 on success.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"

cd "$PROJECT_ROOT"

BACKUP_DIR="data/trial_config_backup"

echo "=== Imperial Trial Start ==="
echo "Starting trial initialization at $(date -Iseconds)"
echo ""

# Step 1: Backup current config
echo "Step 1: Backing up current config to ${BACKUP_DIR}/"
mkdir -p "${BACKUP_DIR}"
cp config/run.yaml "${BACKUP_DIR}/run.yaml"
cp config/risk.yaml "${BACKUP_DIR}/risk.yaml"
echo "  Backed up config/run.yaml → ${BACKUP_DIR}/run.yaml"
echo "  Backed up config/risk.yaml → ${BACKUP_DIR}/risk.yaml"
echo ""

# Step 2: Apply trial config values
echo "Step 2: Applying hourly trial config values"
.venv/bin/python -c "
import yaml
from pathlib import Path

# --- Update run.yaml ---
run_path = Path('config/run.yaml')
with open(run_path) as f:
    run_cfg = yaml.safe_load(f)

run_cfg['account']['equity'] = 1000
run_cfg['account']['max_open_trades'] = 4
run_cfg['run']['max_candidates'] = 3
run_cfg['schedule']['cron_scan'] = '0 * * * *'
run_cfg['schedule']['cron_outcome_eval'] = ''

with open(run_path, 'w') as f:
    yaml.dump(run_cfg, f, default_flow_style=False, sort_keys=False)
print('  config/run.yaml updated: equity=1000, max_open_trades=4, max_candidates=3, hourly schedule')

# --- Update risk.yaml ---
risk_path = Path('config/risk.yaml')
with open(risk_path) as f:
    risk_cfg = yaml.safe_load(f)

risk_cfg['equity'] = 1000
risk_cfg['cancel_rules']['timeout_minutes'] = 45
risk_cfg['cancel_rules']['hard_exit_time'] = ''
risk_cfg['portfolio']['max_open_trades'] = 4

with open(risk_path, 'w') as f:
    yaml.dump(risk_cfg, f, default_flow_style=False, sort_keys=False)
print('  config/risk.yaml updated: equity=1000, timeout=45min, max_open_trades=4, hard_exit disabled')
"
echo ""

# Step 3: Verify with dry-run
echo "Step 3: Verifying config with plumbing-dry-run"
./scripts/run_scan.sh --mode plumbing-dry-run
echo "  Dry-run completed successfully"
echo ""

# Step 4: Print cron installation instructions
echo "=== Trial Config Applied ==="
echo ""
echo "The trial is configured but NOT yet scheduled."
echo "To start the 24-hour hourly trial, install this cron line:"
echo ""
echo "  0 * * * * ${PROJECT_ROOT}/scripts/cron_hourly.sh >> ${PROJECT_ROOT}/data/cron_hourly.log 2>&1"
echo ""
echo "Install with:"
echo "  (crontab -l 2>/dev/null; echo '0 * * * * ${PROJECT_ROOT}/scripts/cron_hourly.sh >> ${PROJECT_ROOT}/data/cron_hourly.log 2>&1') | crontab -"
echo ""
echo "To stop the trial later, run:"
echo "  ./scripts/trial_stop.sh"
echo ""
echo "=== Trial Start Complete ==="
