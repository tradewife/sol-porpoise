"""Playbook generation: create trading setup candidates from signal components.

Generates up to 3 Playbook candidates per symbol from 7 setup types:
- breakout: oi_delta + price beyond VWAP key level
- fade: funding_stretch > 1.5 stdev (|value| > 0.5), contrarian side
- vwap_reclaim: session_structure active, price expected to return to VWAP
- funding_fade: funding_stretch AND oi_delta both active, contrarian to funding
- momentum_continuation: oi_delta aligned with price trend
- liquidity_sweep: liquidity_magnet active, sweep toward depth cluster
- lvn_rejection: future (not yet implemented)

Structural constraints:
- Stop distance >= compute_min_stop (0.8 * ATR floor)
- TP1 >= 2R, TP2 >= 3R
- Entry passively placeable (LONG: entry <= best_bid, SHORT: entry >= best_ask)
- Maximum 3 playbooks per symbol
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from engine.paper_orders import OrderSide
from engine.scoring import SignalComponent
from engine.volatility import compute_min_stop


# Minimum |value| for fade trigger: |z_score| > 1.5 ⟹ |value| > 0.5
# (since value = -z_score / 3.0 in signals.py)
FADE_VALUE_THRESHOLD = 0.5

SETUP_TYPES = frozenset({
    "breakout", "fade", "vwap_reclaim", "funding_fade",
    "momentum_continuation", "liquidity_sweep", "lvn_rejection",
})


@dataclass
class Playbook:
    """A trade setup candidate with concrete entry/stop/TP levels."""

    setup_type: str
    side: OrderSide
    entry: float
    stop: float
    tp1: float
    tp2: float
    invalidation: float
    expected_r_r: float
    probability_band: str  # "high", "medium", "low"
    rationale: str


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _is_active(comp: SignalComponent) -> bool:
    """Check if a signal component has meaningful (non-unknown) data."""
    return comp.confidence > 0 and comp.label != "unknown"


def _compute_playbook_levels(
    side: OrderSide,
    price: float,
    atr: float,
    best_bid: float,
    best_ask: float,
) -> Optional[tuple[float, float, float, float, float, float]]:
    """Compute entry, stop, tp1, tp2, invalidation, expected_r_r.

    Returns None if levels cannot be computed (atr <= 0, no bid/ask, etc.).
    """
    if atr <= 0:
        return None
    if best_bid <= 0 and best_ask <= 0:
        return None

    # Determine passive entry price
    if side == OrderSide.LONG:
        if best_bid <= 0:
            return None
        entry = best_bid
    else:
        if best_ask <= 0:
            return None
        entry = best_ask

    # Compute stop via compute_min_stop (enforces 0.8 * ATR floor)
    try:
        stop = compute_min_stop(atr, entry, side)
    except ValueError:
        return None

    stop_distance = abs(entry - stop)
    if stop_distance <= 0:
        return None

    # TP1 >= 2R, TP2 >= 3R
    if side == OrderSide.LONG:
        tp1 = entry + 2.0 * stop_distance
        tp2 = entry + 3.0 * stop_distance
        invalidation = stop - 0.2 * atr
    else:
        tp1 = entry - 2.0 * stop_distance
        tp2 = entry - 3.0 * stop_distance
        invalidation = stop + 0.2 * atr

    expected_r_r = 2.0  # TP1 target = 2R

    return entry, stop, tp1, tp2, invalidation, expected_r_r


def _count_confirming_signals(
    side: OrderSide,
    signals: dict[str, SignalComponent],
    relevant_keys: list[str],
) -> int:
    """Count how many relevant signals confirm the playbook side."""
    count = 0
    for key in relevant_keys:
        comp = signals.get(key)
        if comp and _is_active(comp):
            if (side == OrderSide.LONG and comp.value > 0) or \
               (side == OrderSide.SHORT and comp.value < 0):
                count += 1
    return count


def _determine_probability_band(confirming: int) -> str:
    """Map number of confirming signals to probability band."""
    if confirming >= 3:
        return "high"
    elif confirming >= 2:
        return "medium"
    else:
        return "low"


def _make_playbook(
    setup_type: str,
    side: OrderSide,
    price: float,
    atr: float,
    best_bid: float,
    best_ask: float,
    signals: dict[str, SignalComponent],
    confirming_keys: list[str],
    rationale: str,
) -> Optional[Playbook]:
    """Create a Playbook with computed levels, or None if impossible."""
    levels = _compute_playbook_levels(side, price, atr, best_bid, best_ask)
    if levels is None:
        return None

    entry, stop, tp1, tp2, invalidation, expected_r_r = levels
    confirming = _count_confirming_signals(side, signals, confirming_keys)
    band = _determine_probability_band(confirming)

    return Playbook(
        setup_type=setup_type,
        side=side,
        entry=entry,
        stop=stop,
        tp1=tp1,
        tp2=tp2,
        invalidation=invalidation,
        expected_r_r=expected_r_r,
        probability_band=band,
        rationale=rationale,
    )


# ---------------------------------------------------------------------------
# Setup type checkers
# ---------------------------------------------------------------------------


def _check_breakout_or_momentum(
    oi_delta: SignalComponent,
    session_structure: Optional[SignalComponent],
    price: float,
    atr: float,
    best_bid: float,
    best_ask: float,
    signals: dict[str, SignalComponent],
) -> list[Playbook]:
    """Check for breakout (OI + session_structure agree) or momentum (OI alone).

    Breakout and momentum are mutually exclusive per direction:
    - If session_structure agrees with oi_delta → breakout (stronger)
    - Otherwise → momentum_continuation (weaker but valid)
    """
    results: list[Playbook] = []
    if not _is_active(oi_delta):
        return results

    oi_side = OrderSide.LONG if oi_delta.value > 0 else OrderSide.SHORT

    # Check if session_structure agrees (price beyond VWAP in same direction)
    session_agrees = False
    if session_structure and _is_active(session_structure):
        if oi_side == OrderSide.LONG and session_structure.value > 0:
            session_agrees = True
        elif oi_side == OrderSide.SHORT and session_structure.value < 0:
            session_agrees = True

    if session_agrees:
        pb = _make_playbook(
            setup_type="breakout",
            side=oi_side,
            price=price,
            atr=atr,
            best_bid=best_bid,
            best_ask=best_ask,
            signals=signals,
            confirming_keys=["oi_delta", "session_structure", "volatility"],
            rationale=(
                f"Breakout: oi_delta ({oi_delta.label}) confirms directional move "
                f"beyond VWAP key level"
            ),
        )
        if pb:
            results.append(pb)
    else:
        pb = _make_playbook(
            setup_type="momentum_continuation",
            side=oi_side,
            price=price,
            atr=atr,
            best_bid=best_bid,
            best_ask=best_ask,
            signals=signals,
            confirming_keys=["oi_delta", "session_structure", "liquidity_magnet"],
            rationale=(
                f"Momentum continuation: oi_delta ({oi_delta.label}) aligned "
                f"with price trend"
            ),
        )
        if pb:
            results.append(pb)

    return results


def _check_fade(
    funding_stretch: SignalComponent,
    price: float,
    atr: float,
    best_bid: float,
    best_ask: float,
    signals: dict[str, SignalComponent],
) -> Optional[Playbook]:
    """Check for fade setup (funding stretched > 1.5 stdev, contrarian)."""
    if not _is_active(funding_stretch):
        return None
    if abs(funding_stretch.value) <= FADE_VALUE_THRESHOLD:
        return None

    # Contrarian: positive value (bullish signal) → LONG, negative → SHORT
    side = OrderSide.LONG if funding_stretch.value > 0 else OrderSide.SHORT

    return _make_playbook(
        setup_type="fade",
        side=side,
        price=price,
        atr=atr,
        best_bid=best_bid,
        best_ask=best_ask,
        signals=signals,
        confirming_keys=["funding_stretch", "session_structure", "volatility"],
        rationale=(
            f"Fade: funding_stretch at {funding_stretch.value:.2f} "
            f"(>{FADE_VALUE_THRESHOLD} threshold), contrarian play"
        ),
    )


def _check_vwap_reclaim(
    session_structure: SignalComponent,
    price: float,
    atr: float,
    best_bid: float,
    best_ask: float,
    signals: dict[str, SignalComponent],
) -> Optional[Playbook]:
    """Check for VWAP reclaim setup."""
    if not _is_active(session_structure):
        return None

    # Price above VWAP → expect pullback → SHORT
    # Price below VWAP → expect reclaim → LONG
    side = OrderSide.SHORT if session_structure.value > 0 else OrderSide.LONG

    return _make_playbook(
        setup_type="vwap_reclaim",
        side=side,
        price=price,
        atr=atr,
        best_bid=best_bid,
        best_ask=best_ask,
        signals=signals,
        confirming_keys=["session_structure", "oi_delta", "volatility"],
        rationale=(
            f"VWAP reclaim: price "
            f"{'above' if session_structure.value > 0 else 'below'} VWAP, "
            f"expecting return to mean"
        ),
    )


def _check_funding_fade(
    funding_stretch: SignalComponent,
    oi_delta: SignalComponent,
    price: float,
    atr: float,
    best_bid: float,
    best_ask: float,
    signals: dict[str, SignalComponent],
) -> Optional[Playbook]:
    """Check for compound funding_fade (both funding AND OI active)."""
    if not _is_active(funding_stretch) or not _is_active(oi_delta):
        return None

    # Contrarian to funding direction
    side = OrderSide.LONG if funding_stretch.value > 0 else OrderSide.SHORT

    return _make_playbook(
        setup_type="funding_fade",
        side=side,
        price=price,
        atr=atr,
        best_bid=best_bid,
        best_ask=best_ask,
        signals=signals,
        confirming_keys=["funding_stretch", "oi_delta", "session_structure", "volatility"],
        rationale=(
            f"Funding fade: funding_stretch + oi_delta both active, "
            f"contrarian to funding direction"
        ),
    )


def _check_liquidity_sweep(
    liquidity_magnet: SignalComponent,
    price: float,
    atr: float,
    best_bid: float,
    best_ask: float,
    signals: dict[str, SignalComponent],
) -> Optional[Playbook]:
    """Check for liquidity sweep toward depth cluster."""
    if not _is_active(liquidity_magnet):
        return None

    # Bid-heavy (value > 0) → liquidity below → SHORT sweep toward bids
    # Ask-heavy (value < 0) → liquidity above → LONG sweep toward asks
    side = OrderSide.SHORT if liquidity_magnet.value > 0 else OrderSide.LONG

    return _make_playbook(
        setup_type="liquidity_sweep",
        side=side,
        price=price,
        atr=atr,
        best_bid=best_bid,
        best_ask=best_ask,
        signals=signals,
        confirming_keys=["liquidity_magnet", "oi_delta", "volatility"],
        rationale=(
            f"Liquidity sweep: {liquidity_magnet.label} cluster detected, "
            f"sweep toward liquidity"
        ),
    )


# ---------------------------------------------------------------------------
# Quality ranking
# ---------------------------------------------------------------------------


def _quality_score(pb: Playbook) -> float:
    """Score a playbook for ranking (higher = better)."""
    type_bonus = {
        "breakout": 1.5,
        "funding_fade": 1.4,
        "fade": 1.3,
        "momentum_continuation": 1.2,
        "vwap_reclaim": 1.1,
        "liquidity_sweep": 1.0,
    }
    band_score = {"high": 3.0, "medium": 2.0, "low": 1.0}
    return band_score.get(pb.probability_band, 0) * type_bonus.get(pb.setup_type, 1.0)


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------


def generate_playbooks(
    symbol: str,
    price: float,
    atr: float,
    signals: dict[str, SignalComponent],
    best_bid: float,
    best_ask: float,
) -> list[Playbook]:
    """Generate up to 3 playbook candidates from signal components.

    Args:
        symbol: Trading symbol.
        price: Current mid price.
        atr: 1-hour ATR value.
        signals: Dict of signal components from extract_signals().
        best_bid: Best bid price for passive entry validation.
        best_ask: Best ask price for passive entry validation.

    Returns:
        List of up to 3 Playbook candidates, sorted by quality score.
        Empty list when no setup conditions are met or all signals unknown.
    """
    candidates: list[Playbook] = []

    oi_delta = signals.get("oi_delta")
    funding_stretch = signals.get("funding_stretch")
    session_structure = signals.get("session_structure")
    liquidity_magnet = signals.get("liquidity_magnet")

    # Ensure we have the signal objects (use unknown defaults if missing)
    from engine.scoring import SignalComponent as SC
    if oi_delta is None:
        oi_delta = SC("oi_delta", 0, 0, "unknown")
    if funding_stretch is None:
        funding_stretch = SC("funding_stretch", 0, 0, "unknown")
    if session_structure is None:
        session_structure = SC("session_structure", 0, 0, "unknown")
    if liquidity_magnet is None:
        liquidity_magnet = SC("liquidity_magnet", 0, 0, "unknown")

    # Breakout or Momentum Continuation (mutually exclusive per direction)
    bm = _check_breakout_or_momentum(
        oi_delta, session_structure, price, atr,
        best_bid, best_ask, signals,
    )
    candidates.extend(bm)

    # Fade: funding stretched > 1.5 stdev
    fade = _check_fade(funding_stretch, price, atr, best_bid, best_ask, signals)
    if fade:
        candidates.append(fade)

    # VWAP Reclaim: session_structure active
    vwap = _check_vwap_reclaim(session_structure, price, atr, best_bid, best_ask, signals)
    if vwap:
        candidates.append(vwap)

    # Funding Fade: funding_stretch AND oi_delta both active
    ff = _check_funding_fade(
        funding_stretch, oi_delta, price, atr,
        best_bid, best_ask, signals,
    )
    if ff:
        candidates.append(ff)

    # Liquidity Sweep: liquidity_magnet active
    ls = _check_liquidity_sweep(liquidity_magnet, price, atr, best_bid, best_ask, signals)
    if ls:
        candidates.append(ls)

    # LVN Rejection: future (not yet implemented)

    # Sort by quality score (descending)
    candidates.sort(key=_quality_score, reverse=True)

    # Deduplicate by (setup_type, side) — keep highest quality only
    seen: set[tuple[str, str]] = set()
    unique: list[Playbook] = []
    for pb in candidates:
        key = (pb.setup_type, pb.side.value)
        if key not in seen:
            seen.add(key)
            unique.append(pb)

    # Return at most 3
    return unique[:3]
