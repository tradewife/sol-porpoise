"""Weekly review: summarize paper-trade performance over the past week.

Reads outcomes.csv, signal_outcomes.csv, and paper_orders.csv to compute
key performance metrics and generate improvement recommendations.
"""

from __future__ import annotations

import csv
import math
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class WeeklyReviewResult:
    """Aggregated weekly performance review."""

    total_trades: int
    expectancy_r: float
    max_drawdown_r: float
    profit_factor: float
    fill_rate: float
    cancel_rate: float
    no_trade_rate: float
    signal_stats: dict[str, dict[str, float]]
    top_recommendations: list[str]
    next_build_pick: str


def _read_result_r_values(outcomes_path: Path) -> list[float]:
    """Read result_R values from outcomes.csv, skipping header."""
    if not outcomes_path.exists():
        return []
    values: list[float] = []
    try:
        with open(outcomes_path, encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                r_str = row.get("result_R", "")
                if r_str:
                    try:
                        values.append(float(r_str))
                    except (ValueError, TypeError):
                        pass
    except Exception:
        pass
    return values


def _compute_expectancy(r_values: list[float]) -> float:
    """Mean of all result_R values. Empty → 0.0."""
    if not r_values:
        return 0.0
    return sum(r_values) / len(r_values)


def _compute_max_drawdown(r_values: list[float]) -> float:
    """Worst peak-to-trough in cumulative R series. Empty → 0.0."""
    if not r_values:
        return 0.0
    cumulative = 0.0
    peak = 0.0
    max_dd = 0.0
    for r in r_values:
        cumulative += r
        if cumulative > peak:
            peak = cumulative
        dd = peak - cumulative
        if dd > max_dd:
            max_dd = dd
    return max_dd


def _compute_profit_factor(r_values: list[float]) -> float:
    """gross_wins / gross_losses. No losses → inf. No wins → 0.0. Empty → 0.0."""
    if not r_values:
        return 0.0
    gross_wins = sum(r for r in r_values if r > 0)
    gross_losses = sum(-r for r in r_values if r < 0)
    if gross_losses == 0.0:
        return float("inf") if gross_wins > 0 else 0.0
    return gross_wins / gross_losses


def _compute_order_rates(paper_orders_path: Path) -> tuple[float, float, float]:
    """Compute fill_rate, cancel_rate, no_trade_rate from paper_orders.csv."""
    if not paper_orders_path.exists():
        return 0.0, 0.0, 0.0

    filled = 0
    cancelled = 0
    no_trade = 0  # pending + expired
    total = 0

    try:
        with open(paper_orders_path, encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                status = row.get("filled", "").strip()
                if not status:
                    continue
                total += 1
                if status == "filled":
                    filled += 1
                elif status == "cancelled":
                    cancelled += 1
                elif status in ("pending", "expired"):
                    no_trade += 1
    except Exception:
        pass

    if total == 0:
        return 0.0, 0.0, 0.0

    return filled / total, cancelled / total, no_trade / total


def _compute_signal_stats(signal_outcomes_path: Path) -> dict[str, dict[str, float]]:
    """Compute per-signal hit_rate and avg_R from signal_outcomes.csv."""
    if not signal_outcomes_path.exists():
        return {}

    signal_data: dict[str, list[float]] = {}
    try:
        with open(signal_outcomes_path, encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                signal = row.get("signal", "").strip()
                r_str = row.get("result_r", "").strip()
                if signal and r_str:
                    try:
                        r = float(r_str)
                        signal_data.setdefault(signal, []).append(r)
                    except (ValueError, TypeError):
                        pass
    except Exception:
        pass

    stats: dict[str, dict[str, float]] = {}
    for signal, rs in signal_data.items():
        n = len(rs)
        wins = sum(1 for r in rs if r > 0)
        stats[signal] = {
            "hit_rate": wins / n if n > 0 else 0.0,
            "avg_R": sum(rs) / n if n > 0 else 0.0,
        }
    return stats


def _generate_recommendations(
    expectancy: float,
    max_drawdown: float,
    profit_factor: float,
    fill_rate: float,
    cancel_rate: float,
    total_trades: int,
    signal_stats: dict[str, dict[str, float]],
) -> tuple[list[str], str]:
    """Generate top 3 improvement recommendations ranked by impact.

    Returns (recommendations, next_build_pick).
    """
    candidates: list[tuple[float, str]] = []

    if total_trades == 0:
        recs = [
            "Accumulate more paper trades — need at least 20 for statistical significance",
            "Ensure scan loop runs on schedule to generate order flow",
            "Verify signal extraction is producing non-unknown components",
        ]
        return recs, "Run scan loop for 7 consecutive days to build trade sample"

    # Impact heuristic: score each recommendation by how much it would improve expectancy
    if expectancy < 0:
        impact = abs(expectancy) * 10
        candidates.append((impact, f"Improve signal quality — negative expectancy ({expectancy:+.2f}R) indicates poor edge"))

    if max_drawdown > 3.0:
        impact = min(max_drawdown / 3.0, 5.0) * 5
        candidates.append((impact, f"Reduce position sizing — max drawdown of {max_drawdown:.1f}R exceeds 3R safety threshold"))

    if profit_factor < 1.5 and profit_factor != float("inf"):
        impact = (1.5 - profit_factor) * 4
        candidates.append((impact, f"Sharpen entry criteria — profit factor {profit_factor:.2f} below 1.5 target"))

    if fill_rate < 0.5 and total_trades > 0:
        impact = (1.0 - fill_rate) * 3
        candidates.append((impact, f"Adjust entry positioning — fill rate {fill_rate:.0%} below 50%"))

    if cancel_rate > 0.3 and total_trades > 0:
        impact = cancel_rate * 3
        candidates.append((impact, f"Tighten timeout/drift rules — cancel rate {cancel_rate:.0%} above 30%"))

    # Per-signal diagnostics
    worst_signal = None
    worst_avg_r = float("inf")
    for sig, stats in signal_stats.items():
        if stats["avg_R"] < worst_avg_r:
            worst_avg_r = stats["avg_R"]
            worst_signal = sig
    if worst_signal and worst_avg_r < 0:
        impact = abs(worst_avg_r) * 3
        candidates.append((impact, f"Disable or tune '{worst_signal}' signal — avg R {worst_avg_r:+.2f} is dragging performance"))

    # Default recommendation if not enough candidates
    if len(candidates) < 3:
        if total_trades < 20:
            candidates.append((1.0, f"Continue trading — sample of {total_trades} trades too small for reliable statistics"))
        else:
            candidates.append((1.0, "Review recent losses for common patterns (time-of-day, setup type, regime)"))

    if len(candidates) < 3:
        candidates.append((0.5, "Consider adding catalyst signal integration for event-driven setups"))

    if len(candidates) < 3:
        candidates.append((0.3, "Cross-validate signal weights against realized outcomes"))

    # Sort by impact descending
    candidates.sort(key=lambda x: x[0], reverse=True)

    recs = [c[1] for c in candidates[:3]]

    # Next build pick: highest-impact recommendation as action item
    next_build = recs[0] if recs else "Build out signal-to-outcome feedback loop"

    return recs, next_build


def run_weekly_review(
    outcomes_path: Path,
    signal_outcomes_path: Path,
    paper_orders_path: Path,
) -> WeeklyReviewResult:
    """Run the weekly review computation.

    Args:
        outcomes_path: Path to outcomes.csv with result_R column.
        signal_outcomes_path: Path to signal_outcomes.csv with signal and result_r columns.
        paper_orders_path: Path to paper_orders.csv with filled status column.

    Returns:
        WeeklyReviewResult with all computed metrics.
    """
    # Read data
    r_values = _read_result_r_values(outcomes_path)

    # Compute metrics
    expectancy = _compute_expectancy(r_values)
    max_drawdown = _compute_max_drawdown(r_values)
    profit_factor = _compute_profit_factor(r_values)
    fill_rate, cancel_rate, no_trade_rate = _compute_order_rates(paper_orders_path)
    signal_stats = _compute_signal_stats(signal_outcomes_path)

    # Generate recommendations
    recommendations, next_build = _generate_recommendations(
        expectancy=expectancy,
        max_drawdown=max_drawdown,
        profit_factor=profit_factor,
        fill_rate=fill_rate,
        cancel_rate=cancel_rate,
        total_trades=len(r_values),
        signal_stats=signal_stats,
    )

    return WeeklyReviewResult(
        total_trades=len(r_values),
        expectancy_r=expectancy,
        max_drawdown_r=max_drawdown,
        profit_factor=profit_factor,
        fill_rate=fill_rate,
        cancel_rate=cancel_rate,
        no_trade_rate=no_trade_rate,
        signal_stats=signal_stats,
        top_recommendations=recommendations,
        next_build_pick=next_build,
    )


def main() -> None:
    """CLI entry point for weekly review."""
    import sys

    # Default paths
    project_root = Path(__file__).resolve().parent.parent
    outcomes_path = project_root / "ledgers" / "outcomes.csv"
    signal_outcomes_path = project_root / "ledgers" / "signal_outcomes.csv"
    paper_orders_path = project_root / "ledgers" / "paper_orders.csv"

    result = run_weekly_review(outcomes_path, signal_outcomes_path, paper_orders_path)

    print("=== Weekly Performance Review ===")
    print(f"Total trades:    {result.total_trades}")
    print(f"Expectancy (R):  {result.expectancy_r:+.3f}")
    print(f"Max drawdown (R): {result.max_drawdown_r:.3f}")
    pf = result.profit_factor
    pf_str = f"{pf:.2f}" if pf != float("inf") else "inf (no losses)"
    print(f"Profit factor:   {pf_str}")
    print(f"Fill rate:       {result.fill_rate:.1%}")
    print(f"Cancel rate:     {result.cancel_rate:.1%}")
    print(f"No-trade rate:   {result.no_trade_rate:.1%}")

    if result.signal_stats:
        print("\nPer-signal stats:")
        for sig, stats in sorted(result.signal_stats.items()):
            print(f"  {sig}: hit_rate={stats['hit_rate']:.1%}, avg_R={stats['avg_R']:+.3f}")
    else:
        print("\nNo signal stats available.")

    print("\nTop recommendations:")
    for i, rec in enumerate(result.top_recommendations, 1):
        print(f"  {i}. {rec}")

    print(f"\nNext build pick: {result.next_build_pick}")

    sys.exit(0)


if __name__ == "__main__":
    main()
