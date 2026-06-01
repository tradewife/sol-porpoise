"""Cross-venue analysis: basis comparison, volume/OI dominance, whale signal integration."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from adapters.base import DataPoint
from engine.scoring import GraphSignalScore, SignalComponent, log_conflict


@dataclass
class BasisResult:
    symbol: str
    hl_perp_price: float
    spot_price: float
    spot_venue: str
    basis_bp: float  # basis in basis points
    funding_alignment: str
    divergence_note: str

    @property
    def is_flagged(self) -> bool:
        return abs(self.basis_bp) > 15.0


@dataclass
class VenueDominance:
    symbol: str
    hl_volume: float
    external_volume: float
    hl_share: float
    leading_venue: str
    lag_divergence: str


def compute_basis(
    symbol: str,
    perp_price: float,
    spot_price: float,
    spot_venue: str = "Imperial",
    funding_rate: float | None = None,
) -> BasisResult:
    """Compute perp premium/discount in basis points."""
    if spot_price <= 0:
        return BasisResult(
            symbol=symbol, hl_perp_price=perp_price, spot_price=spot_price,
            spot_venue=spot_venue, basis_bp=0.0,
            funding_alignment="unknown", divergence_note="invalid_spot_price",
        )

    basis_bp = ((perp_price - spot_price) / spot_price) * 10000  # in bp

    funding_alignment = "unknown"
    if funding_rate is not None:
        if basis_bp > 0 and funding_rate > 0:
            funding_alignment = "aligned_positive"
        elif basis_bp < 0 and funding_rate < 0:
            funding_alignment = "aligned_negative"
        else:
            funding_alignment = "divergent"

    divergence_note = ""
    if abs(basis_bp) > 15:
        divergence_note = f"basis_{basis_bp:+.1f}bp_exceeds_15bp_threshold"

    return BasisResult(
        symbol=symbol,
        hl_perp_price=perp_price,
        spot_price=spot_price,
        spot_venue=spot_venue,
        basis_bp=round(basis_bp, 2),
        funding_alignment=funding_alignment,
        divergence_note=divergence_note,
    )


def compute_venue_dominance(
    symbol: str,
    venue_volumes: dict[str, float],
) -> VenueDominance:
    """Compare volume/OI across venues to determine dominance."""
    total = sum(venue_volumes.values())
    if total == 0:
        return VenueDominance(
            symbol=symbol, hl_volume=0, external_volume=0,
            hl_share=0, leading_venue="none", lag_divergence="no_data",
        )

    sorted_venues = sorted(venue_volumes.items(), key=lambda x: x[1], reverse=True)
    leading = sorted_venues[0][0] if sorted_venues else "none"

    hl_vol = venue_volumes.get("Hyperliquid", venue_volumes.get("hl", 0))
    external_vol = total - hl_vol
    hl_share = hl_vol / total

    lag_divergence = ""
    if len(sorted_venues) >= 2:
        top2_diff = sorted_venues[0][1] - sorted_venues[1][1]
        if top2_diff > 0 and sorted_venues[0][1] > 0:
            dominance_pct = top2_diff / sorted_venues[0][1] * 100
            if dominance_pct > 50:
                lag_divergence = f"{sorted_venues[0][0]}_dominant_{dominance_pct:.0f}%"

    return VenueDominance(
        symbol=symbol,
        hl_volume=hl_vol,
        external_volume=external_vol,
        hl_share=round(hl_share, 4),
        leading_venue=leading,
        lag_divergence=lag_divergence,
    )


def integrate_whale_signals(
    score: GraphSignalScore,
    whale_datapoints: list[DataPoint],
    max_whale_weight: float = 0.10,
) -> GraphSignalScore:
    """Integrate whale/smart-money signals into the GraphSignalScore.

    Whale signals are directional evidence ONLY — they contribute to
    the whale_evidence component but cannot independently trigger a trade.
    """
    if not whale_datapoints:
        # Missing data: degrade confidence
        score.components["whale_evidence"] = SignalComponent(
            name="whale_evidence", value=0, confidence=0, label="unknown",
        )
        return score

    smart_money_count = 0
    whale_count = 0
    total_bias = 0.0

    for dp in whale_datapoints:
        entity_type = dp.attrs.get("entity_type", "unknown")
        if entity_type == "smart_money":
            smart_money_count += 1
            pnl = dp.value if dp.value else 0
            total_bias += pnl
        elif entity_type == "whale_unlabeled":
            whale_count += 1

    # Signal direction: aggregate smart money PnL direction
    if smart_money_count > 0:
        avg_bias = total_bias / smart_money_count
        signal_value = min(1.0, max(-1.0, avg_bias / 10000))  # normalized
        confidence = min(0.9, smart_money_count / 5.0)  # max confidence at 5+ smart money
        label = "smart_money_directional"
    elif whale_count > 0:
        signal_value = 0.0  # no directional claim from unlabeled whales
        confidence = 0.3  # low confidence
        label = "whale_unlabeled_present"
    else:
        signal_value = 0.0
        confidence = 0.1
        label = "no_alpha_evidence"

    score.components["whale_evidence"] = SignalComponent(
        name="whale_evidence",
        value=signal_value,
        confidence=confidence,
        label=label,
    )

    return score


def check_cross_venue_consistency(
    symbol: str,
    datapoints: list[DataPoint],
) -> list[str]:
    """Check for conflicts between sources for the same symbol/metric."""
    conflicts: list[str] = []
    metric_values: dict[str, list[tuple[float, str]]] = {}  # metric -> [(value, source)]

    for dp in datapoints:
        if dp.symbol != symbol:
            continue
        if dp.metric not in metric_values:
            metric_values[dp.metric] = []
        if isinstance(dp.value, (int, float)):
            metric_values[dp.metric].append((float(dp.value), dp.provenance.source_name))

    for metric, entries in metric_values.items():
        if len(entries) >= 2:
            values = [e[0] for e in entries]
            sources = [e[1] for e in entries]
            max_val = max(values)
            min_val = min(values)
            if max_val > 0 and (max_val - min_val) / max_val > 0.01:  # >1% divergence
                conflicts.append(
                    log_conflict(symbol, metric, values[0], values[1], sources[0], sources[1])
                )

    return conflicts
