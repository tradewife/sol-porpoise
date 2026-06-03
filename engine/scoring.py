"""GraphSignalScore: weighted signal scoring per symbol."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

# Component weights from MISSION.md
COMPONENT_WEIGHTS: dict[str, float] = {
    "funding_stretch": 0.15,
    "oi_delta": 0.15,
    "basis": 0.05,
    "liquidity_magnet": 0.15,
    "session_structure": 0.10,
    "whale_evidence": 0.07,
    "dex_perp_lag": 0.10,
    "volatility": 0.10,
    "catalyst": 0.05,
    "book_imbalance": 0.08,
}

assert sum(COMPONENT_WEIGHTS.values()) == 1.0, "Weights must sum to 1.0"


@dataclass
class SignalComponent:
    name: str
    value: float  # -1.0 to 1.0 (bearish to bullish)
    confidence: float  # 0.0 to 1.0
    label: str = ""  # "unknown" if missing

    @property
    def is_unknown(self) -> bool:
        return self.label == "unknown"


@dataclass
class GraphSignalScore:
    symbol: str
    components: dict[str, SignalComponent] = field(default_factory=dict)
    conflicts: list[str] = field(default_factory=list)

    @property
    def weighted_score(self) -> float:
        """Compute weighted score, treating unknown components as 0 contribution."""
        total = 0.0
        for name, weight in COMPONENT_WEIGHTS.items():
            comp = self.components.get(name)
            if comp and not comp.is_unknown:
                total += weight * comp.value * comp.confidence
        return total

    @property
    def overall_confidence(self) -> float:
        """Average confidence across all components, reduced for missing ones."""
        if not COMPONENT_WEIGHTS:
            return 0.0
        total_weight = 0.0
        total_conf = 0.0
        for name, weight in COMPONENT_WEIGHTS.items():
            comp = self.components.get(name)
            if comp and not comp.is_unknown:
                total_weight += weight
                total_conf += weight * comp.confidence
            # Unknown components reduce confidence proportionally
        if total_weight == 0:
            return 0.0
        return total_conf / total_weight

    @property
    def unknown_components(self) -> list[str]:
        missing = []
        for name in COMPONENT_WEIGHTS:
            comp = self.components.get(name)
            if not comp or comp.is_unknown:
                missing.append(name)
        return missing

    def to_dict(self) -> dict[str, Any]:
        return {
            "symbol": self.symbol,
            "weighted_score": round(self.weighted_score, 4),
            "overall_confidence": round(self.overall_confidence, 4),
            "unknown_components": self.unknown_components,
            "conflicts": self.conflicts,
            "components": {
                name: {
                    "value": comp.value,
                    "confidence": comp.confidence,
                    "label": comp.label,
                }
                for name, comp in self.components.items()
            },
        }


def compute_signal_score(
    symbol: str,
    components: dict[str, SignalComponent],
    conflicts: list[str] | None = None,
) -> GraphSignalScore:
    """Create a GraphSignalScore with validation."""
    score = GraphSignalScore(
        symbol=symbol,
        components=components,
        conflicts=conflicts or [],
    )
    return score


def log_conflict(
    symbol: str, metric: str, value_a: Any, value_b: Any,
    source_a: str, source_b: str,
) -> str:
    """Create a conflict log string for the evidence ledger."""
    return (
        f"CONFLICT | {symbol} | {metric} | "
        f"{source_a}={value_a} vs {source_b}={value_b}"
    )
