"""Outcome evaluation: compute R, MAE, MFE, fees, slippage, signal attribution."""

from __future__ import annotations

import csv
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from adapters.normalizer import aest_now_iso
from engine.paper_orders import OrderSide, PaperOrder

OUTCOMES_HEADER = (
    "date_Australia/Sydney,symbol,setup,side,entry,stop,tp1,tp2,filled,"
    "entry_ts_Australia/Sydney,exit_ts_Australia/Sydney,result_R,"
    "max_FvE,max_AdE,fees_bps,slippage_bps,notes,provenance_tags"
)

SIGNAL_OUTCOMES_HEADER = "signal,hit_rate,avg_R,n,last_updated_Australia/Sydney"

KNOWN_SIGNALS = [
    "funding_stretch",
    "OI_delta",
    "basis",
    "whale_bias",
    "DEX_lead",
    "catalyst",
    "liquidity_magnet",
    "session_structure",
]


@dataclass
class Outcome:
    order: PaperOrder
    exit_price: float
    result_r: float
    max_fve: float = 0.0  # Max Favorable Excursion in R
    max_ade: float = 0.0  # Max Adverse Excursion in R
    fees_bps: float = 0.0
    slippage_bps: float = 3.0
    notes: str = ""
    provenance_tags: str = ""

    def to_csv_row(self) -> str:
        o = self.order
        return (
            f"{o.created_ts_aest.split(' ')[0] if o.created_ts_aest else ''},"
            f"{o.symbol},{o.setup},{o.side.value},{o.entry},{o.stop},{o.tp1},{o.tp2},"
            f"{o.filled.value},{o.entry_ts_aest},{o.exit_ts_aest},"
            f"{round(self.result_r, 4)},{round(self.max_fve, 4)},{round(self.max_ade, 4)},"
            f"{self.fees_bps},{self.slippage_bps},{self.notes},{self.provenance_tags}"
        )


class OutcomeEvaluator:
    """Evaluates paper order outcomes and writes to outcomes.csv."""

    def __init__(
        self,
        outcomes_path: str | Path = "ledgers/outcomes.csv",
        signal_outcomes_path: str | Path = "ledgers/signal_outcomes.csv",
    ) -> None:
        self.outcomes_path = Path(outcomes_path)
        self.signal_outcomes_path = Path(signal_outcomes_path)

    def compute_outcome(
        self,
        order: PaperOrder,
        exit_price: float,
        mae_price: float | None = None,
        mfe_price: float | None = None,
        fees_bps: float = 5.0,
        slippage_bps: float = 3.0,
    ) -> Outcome:
        """Compute outcome metrics from order and exit data."""
        stop_distance = order.stop_distance
        if stop_distance == 0:
            result_r = 0.0
        elif order.side == OrderSide.LONG:
            result_r = (exit_price - order.entry) / stop_distance
        else:
            result_r = (order.entry - exit_price) / stop_distance

        # Adjust for fees and slippage
        cost_r = (fees_bps + slippage_bps) / 10000.0
        result_r -= cost_r

        # MAE/MFE in R-terms
        max_ade = 0.0
        max_fve = 0.0
        if mae_price is not None and stop_distance > 0:
            if order.side == OrderSide.LONG:
                max_ade = (order.entry - mae_price) / stop_distance
            else:
                max_ade = (mae_price - order.entry) / stop_distance
        if mfe_price is not None and stop_distance > 0:
            if order.side == OrderSide.LONG:
                max_fve = (mfe_price - order.entry) / stop_distance
            else:
                max_fve = (order.entry - mfe_price) / stop_distance

        return Outcome(
            order=order,
            exit_price=exit_price,
            result_r=result_r,
            max_fve=max(0.0, max_fve),
            max_ade=max(0.0, max_ade),
            fees_bps=fees_bps,
            slippage_bps=slippage_bps,
        )

    def write_outcome(self, outcome: Outcome) -> None:
        header_needed = not self.outcomes_path.exists() or self.outcomes_path.stat().st_size == 0
        with open(self.outcomes_path, "a", encoding="utf-8", newline="") as f:
            if header_needed:
                f.write(OUTCOMES_HEADER + "\n")
            f.write(outcome.to_csv_row() + "\n")

    def write_signal_attribution(self, order_id: str, signals: list[str], result_r: float | None = None) -> None:
        """Link signals to an order outcome for later hit-rate computation."""
        header_needed = (
            not self.signal_outcomes_path.exists()
            or self.signal_outcomes_path.stat().st_size == 0
        )
        with open(self.signal_outcomes_path, "a", encoding="utf-8", newline="") as f:
            if header_needed:
                f.write("order_id,signal,result_r,timestamp_Australia/Sydney\n")
            r_str = str(round(result_r, 4)) if result_r is not None else ""
            for signal in signals:
                f.write(f"{order_id},{signal},{r_str},{aest_now_iso()}\n")

    def compute_signal_stats(self) -> dict[str, dict[str, Any]]:
        """Compute per-signal hit rate and average R from outcomes."""
        stats: dict[str, dict[str, Any]] = {}
        if not self.signal_outcomes_path.exists():
            return stats

        signal_data: dict[str, list[float]] = {}
        with open(self.signal_outcomes_path, encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                signal = row.get("signal", "")
                result = row.get("result_r", "")
                if signal and result:
                    try:
                        r = float(result)
                        signal_data.setdefault(signal, []).append(r)
                    except ValueError:
                        pass

        for signal, rs in signal_data.items():
            n = len(rs)
            wins = [r for r in rs if r > 0]
            stats[signal] = {
                "hit_rate": len(wins) / n if n > 0 else 0.0,
                "avg_R": sum(rs) / n if n > 0 else 0.0,
                "n": n,
            }

        return stats
