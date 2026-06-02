"""Senpi Hawk v1.0.0 breakout signal module.

Deterministic breakout logic: 7-day high/low breakout gate, Smart Money tilt
gate, structure gate, and 0-9 scoring. Pure Python, no LLM calls.

Reads config/strategy.yaml via _load_yaml_config("strategy") but falls back
to Python defaults when the file is absent.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parent.parent

# ---------------------------------------------------------------------------
# Python defaults (used when config/strategy.yaml is missing or incomplete)
# ---------------------------------------------------------------------------
_DEFAULTS: dict[str, Any] = {
    "enabled": True,
    "lookback_days": 7,
    "sm_tilt_min_pct": 55,
    "sm_tilt_strong_pct": 70,
    "min_score": 5,
    "breakout_mag_high": 1.0,
    "breakout_mag_mid": 0.3,
    "volume_spike_multiplier": 1.5,
    "structure_partial_confidence_cap": "medium",
}


def _load_yaml_config(name: str) -> dict:
    """Load a YAML config file from config/<name>.yaml, returning {} if absent."""
    import yaml

    path = PROJECT_ROOT / "config" / f"{name}.yaml"
    if path.exists():
        return yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return {}


def _get_hawk_config() -> dict[str, Any]:
    """Return hawk_breakout config merged with defaults for any missing keys."""
    raw = _load_yaml_config("strategy")
    hawk = raw.get("hawk_breakout", {}) if isinstance(raw, dict) else {}
    cfg = dict(_DEFAULTS)
    cfg.update(hawk)
    return cfg


# ---------------------------------------------------------------------------
# HawkSignal dataclass
# ---------------------------------------------------------------------------


@dataclass
class HawkSignal:
    """Result of Hawk breakout computation for one market."""

    market: str
    signal: str  # "long" | "short" | "none"
    score: int  # 0-9
    basis: dict[str, Any] = field(default_factory=dict)
    notes: str = ""


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def compute_hawk_breakout_signal(
    market: str,
    closes_1h: list[float],  # >= 168 values (7 days x 24h); most-recent last
    closes_4h: list[float],  # >= 42 values (7 days x 6 bars); most-recent last
    volume_1h: list[float],  # same length as closes_1h
    sm_long_pct: float | None,  # smart-money long % from leaderboard/whale data, 0-100
    structure_classification: str,  # from market-structure-context evaluator
) -> HawkSignal:
    """Compute Hawk breakout signal for a single market.

    Gate checks (all must pass for a non-none signal):
      1. 7-day high/low breakout: latest close > max(prior 167) or < min(prior 167)
      2. SM tilt >= 55 % in breakout direction
      3. structure_classification != "structure_rejected"

    Scoring (0-9): magnitude (1-3) + SM confirmed (+2) + SM strong tilt (+1)
    + 4h aligned (+2) + volume spike (+1).  Minimum score threshold is 5.
    """
    cfg = _get_hawk_config()

    # Handle degenerate inputs
    if len(closes_1h) < 2:
        return HawkSignal(
            market=market,
            signal="none",
            score=0,
            notes="Insufficient price data (fewer than 2 closes).",
        )

    # Pad short series so the function still works with limited data
    lookback = cfg["lookback_days"] * 24  # 7 * 24 = 168
    if len(closes_1h) < lookback:
        closes_1h = list(closes_1h)

    # --- Gate 1: 7-day breakout ---
    boundary_count = min(len(closes_1h) - 1, lookback)
    if boundary_count < 1:
        return HawkSignal(
            market=market,
            signal="none",
            score=0,
            notes="Not enough history for breakout check.",
        )

    prior = closes_1h[:-1][-boundary_count:]
    latest_close = closes_1h[-1]
    high_7d = max(prior)
    low_7d = min(prior)

    direction: str | None = None
    breakout_magnitude_pct = 0.0
    htf_breakout = False

    if latest_close > high_7d:
        direction = "long"
        htf_breakout = True
        breakout_magnitude_pct = abs(latest_close - high_7d) / high_7d * 100
    elif latest_close < low_7d:
        direction = "short"
        htf_breakout = True
        breakout_magnitude_pct = abs(latest_close - low_7d) / low_7d * 100

    if direction is None:
        return HawkSignal(
            market=market,
            signal="none",
            score=0,
            basis={
                "htf_breakout": False,
                "sm_tilt_supports": False,
                "sm_long_pct": sm_long_pct,
                "breakout_magnitude_pct": 0.0,
                "4h_trend_aligned": False,
                "volume_spike": False,
                "structure_classification": structure_classification,
            },
            notes="No 7-day breakout: price within range.",
        )

    # --- Gate 2: SM tilt ---
    sm_tilt_min = cfg["sm_tilt_min_pct"]
    if sm_long_pct is None:
        return HawkSignal(
            market=market,
            signal="none",
            score=0,
            basis={
                "htf_breakout": htf_breakout,
                "sm_tilt_supports": False,
                "sm_long_pct": None,
                "breakout_magnitude_pct": round(breakout_magnitude_pct, 4),
                "4h_trend_aligned": False,
                "volume_spike": False,
                "structure_classification": structure_classification,
            },
            notes="SM tilt gate failed: sm_long_pct is None.",
        )

    effective_tilt = sm_long_pct if direction == "long" else (100 - sm_long_pct)
    sm_tilt_supports = effective_tilt >= sm_tilt_min

    if not sm_tilt_supports:
        return HawkSignal(
            market=market,
            signal="none",
            score=0,
            basis={
                "htf_breakout": htf_breakout,
                "sm_tilt_supports": False,
                "sm_long_pct": sm_long_pct,
                "breakout_magnitude_pct": round(breakout_magnitude_pct, 4),
                "4h_trend_aligned": False,
                "volume_spike": False,
                "structure_classification": structure_classification,
            },
            notes=f"SM tilt gate failed: effective tilt {effective_tilt:.1f}% < {sm_tilt_min}%.",
        )

    # --- Gate 3: Structure not rejected ---
    if structure_classification == "structure_rejected":
        return HawkSignal(
            market=market,
            signal="none",
            score=0,
            basis={
                "htf_breakout": htf_breakout,
                "sm_tilt_supports": sm_tilt_supports,
                "sm_long_pct": sm_long_pct,
                "breakout_magnitude_pct": round(breakout_magnitude_pct, 4),
                "4h_trend_aligned": False,
                "volume_spike": False,
                "structure_classification": structure_classification,
            },
            notes="Structure rejected: veto applied.",
        )

    # --- All gates passed: compute score ---
    score = 0

    # Magnitude scoring
    mag_high = cfg["breakout_mag_high"]
    mag_mid = cfg["breakout_mag_mid"]
    if breakout_magnitude_pct >= mag_high:
        score += 3
    elif breakout_magnitude_pct >= mag_mid:
        score += 2
    else:
        score += 1

    # SM confirmed
    score += 2

    # SM strong tilt bonus
    sm_tilt_strong = cfg["sm_tilt_strong_pct"]
    if effective_tilt >= sm_tilt_strong:
        score += 1

    # 4h trend alignment
    four_h_aligned = False
    if len(closes_4h) >= 2:
        if direction == "long" and closes_4h[-1] > closes_4h[0]:
            four_h_aligned = True
            score += 2
        elif direction == "short" and closes_4h[-1] < closes_4h[0]:
            four_h_aligned = True
            score += 2

    # Volume spike
    vol_spike_mult = cfg["volume_spike_multiplier"]
    volume_spike = False
    if volume_1h and len(volume_1h) >= 2:
        avg_vol = sum(volume_1h[:-1]) / max(len(volume_1h) - 1, 1)
        if avg_vol > 0 and volume_1h[-1] >= vol_spike_mult * avg_vol:
            volume_spike = True
            score += 1

    # --- Minimum score threshold ---
    min_score = cfg["min_score"]
    if score < min_score:
        return HawkSignal(
            market=market,
            signal="none",
            score=0,
            basis={
                "htf_breakout": htf_breakout,
                "sm_tilt_supports": sm_tilt_supports,
                "sm_long_pct": sm_long_pct,
                "breakout_magnitude_pct": round(breakout_magnitude_pct, 4),
                "4h_trend_aligned": four_h_aligned,
                "volume_spike": volume_spike,
                "structure_classification": structure_classification,
            },
            notes=f"Score {score} below minimum threshold {min_score}.",
        )

    return HawkSignal(
        market=market,
        signal=direction,
        score=score,
        basis={
            "htf_breakout": htf_breakout,
            "sm_tilt_supports": sm_tilt_supports,
            "sm_long_pct": sm_long_pct,
            "breakout_magnitude_pct": round(breakout_magnitude_pct, 4),
            "4h_trend_aligned": four_h_aligned,
            "volume_spike": volume_spike,
            "structure_classification": structure_classification,
        },
        notes=f"{direction} breakout: score={score}/9, magnitude={breakout_magnitude_pct:.2f}%.",
    )
