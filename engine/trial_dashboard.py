"""Trial dashboard: real-time summary of 24-hour paper-trading trial progress.

Reads reports/, ledgers/paper_orders.csv, ledgers/outcomes.csv,
ledgers/signal_outcomes.csv and produces a formatted summary including:
- Scan count
- Order counts (filled / cancelled / open)
- Outcome metrics (wins / losses / expectancy R)
- Per-signal hit rates
- Per-setup-type stats
- Trial elapsed / remaining time

Handles empty or missing data gracefully with zeros and 'no data yet' messages.
"""

from __future__ import annotations

import csv
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from zoneinfo import ZoneInfo

AEST = ZoneInfo("Australia/Sydney")

# Trial duration in hours
TRIAL_DURATION_HOURS = 24

# ---------------------------------------------------------------------------
# Internal helpers — read CSV data
# ---------------------------------------------------------------------------


def _count_scans(reports_dir: Path) -> int:
    """Count report files matching the timestamp naming pattern."""
    if not reports_dir.exists():
        return 0
    return len(list(reports_dir.glob("*_report.md")))


def _read_paper_orders(ledger_dir: Path) -> dict[str, int]:
    """Read paper_orders.csv and return counts by status.

    Returns dict with keys: filled, cancelled, open (pending), total.
    """
    counts: dict[str, int] = {"filled": 0, "cancelled": 0, "open": 0, "total": 0}
    path = ledger_dir / "paper_orders.csv"
    if not path.exists():
        return counts

    try:
        with open(path, encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                status = row.get("filled", "").strip().lower()
                counts["total"] += 1
                if status == "filled":
                    counts["filled"] += 1
                elif status == "cancelled":
                    counts["cancelled"] += 1
                elif status in ("pending", "expired"):
                    counts["open"] += 1
                else:
                    # Unknown status — count as open
                    counts["open"] += 1
    except Exception:
        pass

    return counts


def _read_outcomes(ledger_dir: Path) -> dict[str, Any]:
    """Read outcomes.csv and compute win/loss/expectancy and per-setup stats.

    Returns dict with keys:
      wins, losses, expectancy_r, setup_stats (dict of setup -> {wins, losses, total, avg_r})
    """
    result: dict[str, Any] = {
        "wins": 0,
        "losses": 0,
        "expectancy_r": 0.0,
        "setup_stats": {},
    }
    path = ledger_dir / "outcomes.csv"
    if not path.exists():
        return result

    r_values: list[float] = []
    setup_data: dict[str, list[float]] = {}

    try:
        with open(path, encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                r_str = row.get("result_R", "").strip()
                if not r_str:
                    continue
                try:
                    r = float(r_str)
                except (ValueError, TypeError):
                    continue

                r_values.append(r)
                if r > 0:
                    result["wins"] += 1
                elif r < 0:
                    result["losses"] += 1

                # Per-setup tracking
                setup = row.get("setup", "").strip()
                if setup:
                    setup_data.setdefault(setup, []).append(r)
    except Exception:
        pass

    if r_values:
        result["expectancy_r"] = sum(r_values) / len(r_values)

    # Compute per-setup stats
    for setup, rs in setup_data.items():
        n = len(rs)
        wins = sum(1 for r in rs if r > 0)
        losses = sum(1 for r in rs if r < 0)
        avg_r = sum(rs) / n if n > 0 else 0.0
        result["setup_stats"][setup] = {
            "wins": wins,
            "losses": losses,
            "total": n,
            "avg_r": avg_r,
        }

    return result


def _read_signal_outcomes(ledger_dir: Path) -> list[dict[str, Any]]:
    """Read signal_outcomes.csv and return per-signal stats.

    Returns list of dicts with keys: signal, hit_rate, avg_R, n.
    """
    path = ledger_dir / "signal_outcomes.csv"
    if not path.exists():
        return []

    signals: list[dict[str, Any]] = []
    try:
        with open(path, encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                signal = row.get("signal", "").strip()
                if not signal:
                    continue
                try:
                    hit_rate = float(row.get("hit_rate", "0"))
                    avg_r = float(row.get("avg_R", "0"))
                    n = int(row.get("n", "0"))
                except (ValueError, TypeError):
                    continue
                signals.append({
                    "signal": signal,
                    "hit_rate": hit_rate,
                    "avg_R": avg_r,
                    "n": n,
                })
    except Exception:
        pass

    return signals


def _compute_trial_time(reports_dir: Path) -> dict[str, str]:
    """Compute trial elapsed/remaining time from first report timestamp.

    Derives trial start from the earliest report filename timestamp.
    Returns dict with keys: elapsed, remaining, status.
    """
    result: dict[str, str] = {
        "elapsed": "unknown",
        "remaining": "unknown",
        "status": "no reports yet",
    }

    if not reports_dir.exists():
        return result

    report_files = sorted(reports_dir.glob("*_report.md"))
    if not report_files:
        return result

    # Parse timestamp from first report filename
    # Pattern: 2026-06-02T03-35-37_Australia-Sydney_report.md
    first_name = report_files[0].stem  # e.g. 2026-06-02T03-35-37_Australia-Sydney_report
    try:
        # Extract timestamp portion: everything before _Australia or the tz part
        ts_part = first_name.split("_report")[0]  # 2026-06-02T03-35-37_Australia-Sydney
        # Split at the timezone part
        if "_Australia" in ts_part:
            dt_part = ts_part.split("_Australia")[0]  # 2026-06-02T03-35-37
        else:
            dt_part = ts_part.rsplit("_", 1)[0]  # fallback

        # Replace dashes (in time portion) with colons
        # dt_part looks like: 2026-06-02T03-35-37
        parts = dt_part.split("T")
        if len(parts) == 2:
            date_part = parts[0]  # 2026-06-02
            time_part = parts[1].replace("-", ":")  # 03:35:37
            iso_str = f"{date_part}T{time_part}+10:00"
            trial_start = datetime.fromisoformat(iso_str)
        else:
            return result
    except (ValueError, IndexError):
        return result

    now = datetime.now(AEST)
    elapsed = now - trial_start
    remaining = timedelta(hours=TRIAL_DURATION_HOURS) - elapsed

    # Format elapsed
    elapsed_hours = int(elapsed.total_seconds() // 3600)
    elapsed_mins = int((elapsed.total_seconds() % 3600) // 60)
    result["elapsed"] = f"{elapsed_hours}h {elapsed_mins}m"

    if remaining.total_seconds() <= 0:
        result["remaining"] = "0h 0m (complete)"
        result["status"] = "complete"
    else:
        remaining_hours = int(remaining.total_seconds() // 3600)
        remaining_mins = int((remaining.total_seconds() % 3600) // 60)
        result["remaining"] = f"{remaining_hours}h {remaining_mins}m"
        result["status"] = "in progress"

    return result


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def run_dashboard(
    project_root: Path | str | None = None,
    *,
    reports_dir: str = "reports",
    ledger_dir: str = "ledgers",
) -> str:
    """Run the trial dashboard and return formatted summary string.

    Parameters
    ----------
    project_root : Path or str or None
        Root directory of the project. Defaults to the repository root
        (parent of ``engine/``).
    reports_dir : str
        Relative path to reports directory from project root.
    ledger_dir : str
        Relative path to ledgers directory from project root.

    Returns
    -------
    str
        Formatted dashboard summary.
    """
    if project_root is None:
        project_root = Path(__file__).resolve().parent.parent
    else:
        project_root = Path(project_root)

    reports_path = project_root / reports_dir
    ledger_path = project_root / ledger_dir

    # Gather data
    scan_count = _count_scans(reports_path)
    orders = _read_paper_orders(ledger_path)
    outcomes = _read_outcomes(ledger_path)
    signals = _read_signal_outcomes(ledger_path)
    trial_time = _compute_trial_time(reports_path)

    # Build output
    lines: list[str] = []
    lines.append("=" * 60)
    lines.append("  IMPERIAL AGENT — 24-HOUR TRIAL DASHBOARD")
    lines.append("=" * 60)
    lines.append("")

    # --- Trial Time ---
    lines.append("─── TRIAL TIME ───")
    if trial_time["status"] == "no reports yet":
        lines.append("  No trial data yet — waiting for first scan")
    else:
        lines.append(f"  Status:   {trial_time['status']}")
        lines.append(f"  Elapsed:  {trial_time['elapsed']}")
        lines.append(f"  Remaining: {trial_time['remaining']}")
    lines.append("")

    # --- Scan Summary ---
    lines.append("─── SCAN SUMMARY ───")
    lines.append(f"  Scans completed: {scan_count}")
    lines.append("")

    # --- Order Counts ---
    lines.append("─── ORDER COUNTS ───")
    lines.append(f"  Total orders: {orders['total']}")
    lines.append(f"  Filled:       {orders['filled']}")
    lines.append(f"  Cancelled:    {orders['cancelled']}")
    lines.append(f"  Open:         {orders['open']}")
    lines.append("")

    # --- Outcome Metrics ---
    lines.append("─── OUTCOME METRICS ───")
    if outcomes["wins"] == 0 and outcomes["losses"] == 0:
        lines.append("  No outcome data yet")
        lines.append(f"  Wins:         {outcomes['wins']}")
        lines.append(f"  Losses:       {outcomes['losses']}")
        lines.append(f"  Expectancy R: {outcomes['expectancy_r']:.2f}")
    else:
        lines.append(f"  Wins:         {outcomes['wins']}")
        lines.append(f"  Losses:       {outcomes['losses']}")
        lines.append(f"  Expectancy R: {outcomes['expectancy_r']:.2f}")
    lines.append("")

    # --- Per-Setup-Type Stats ---
    lines.append("─── PER-SETUP-TYPE STATS ───")
    setup_stats = outcomes["setup_stats"]
    if not setup_stats:
        lines.append("  No setup data yet")
    else:
        for setup, stats in sorted(setup_stats.items()):
            win_rate = (
                f"{stats['wins'] / stats['total'] * 100:.0f}%"
                if stats["total"] > 0
                else "0%"
            )
            lines.append(
                f"  {setup:30s}  n={stats['total']}  "
                f"W/L={stats['wins']}/{stats['losses']}  "
                f"win_rate={win_rate}  "
                f"avg_R={stats['avg_r']:+.2f}"
            )
    lines.append("")

    # --- Per-Signal Hit Rates ---
    lines.append("─── PER-SIGNAL HIT RATES ───")
    if not signals:
        lines.append("  No signal data yet")
    else:
        for s in signals:
            pct = f"{s['hit_rate'] * 100:.0f}%"
            lines.append(
                f"  {s['signal']:25s}  hit_rate={pct:>4s}  "
                f"avg_R={s['avg_R']:+.2f}  n={s['n']}"
            )
    lines.append("")

    lines.append("=" * 60)
    return "\n".join(lines)


def main() -> None:
    """CLI entry point — print dashboard to stdout."""
    output = run_dashboard()
    print(output)


if __name__ == "__main__":
    main()
