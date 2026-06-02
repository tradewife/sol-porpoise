"""Tests for engine/hawk_breakout.py — Senpi Hawk v1.0.0 breakout logic.

Covers all 7 spec scenarios plus edge cases:
  1. Full long breakout (score >= 7)
  2. Long breakout without volume spike (score = 6)
  3. Long breakout with structure_partial (score >= 5)
  4. Structure rejected veto (signal = none)
  5. SM tilt gate fails (signal = none)
  6. No breakout within range (signal = none)
  7. Breakdown, SM short, structure_confirmed (score >= 7)

Edge cases:
  - SM tilt = None blocks signal
  - Insufficient data (single close)
  - Score below minimum threshold
  - SM strong tilt bonus (+1)
  - Works without strategy.yaml
  - Basis dict fully populated
  - Score always 0-9
  - Minimal data (2 closes)
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from engine.hawk_breakout import (
    HawkSignal,
    _get_hawk_config,
    compute_hawk_breakout_signal,
)


# ---------------------------------------------------------------------------
# Helpers to build test data
# ---------------------------------------------------------------------------

def _make_closes(
    base: float = 100.0,
    length: int = 168,
    breakout: str | None = None,
    magnitude_pct: float = 0.5,
) -> list[float]:
    """Build a flat series of closes; optionally break out on the last bar.

    breakout: "long" -> last close above prior high
              "short" -> last close below prior low
              None -> flat (no breakout)
    """
    series = [base] * length
    if breakout == "long":
        # Last close is magnitude_pct above base
        series[-1] = base * (1 + magnitude_pct / 100)
    elif breakout == "short":
        # Last close is magnitude_pct below base
        series[-1] = base * (1 - magnitude_pct / 100)
    return series


def _make_volumes(length: int = 168, spike: bool = False) -> list[float]:
    """Build a flat volume series; optionally spike on the last bar."""
    series = [1000.0] * length
    if spike:
        series[-1] = 2000.0  # 2x average -> exceeds 1.5x threshold
    return series


# ---------------------------------------------------------------------------
# 7 Spec Scenarios
# ---------------------------------------------------------------------------


class TestScenario1FullLongBreakout:
    """7d high breakout, SM 60% long, structure_confirmed, 4h aligned, vol spike
    -> signal=long, score >= 7"""

    def test_full_long(self):
        closes = _make_closes(breakout="long", magnitude_pct=0.5)
        closes_4h = _make_closes(base=95.0, length=42, breakout="long", magnitude_pct=5.0)
        volumes = _make_volumes(spike=True)

        sig = compute_hawk_breakout_signal(
            market="SOL",
            closes_1h=closes,
            closes_4h=closes_4h,
            volume_1h=volumes,
            sm_long_pct=60.0,
            structure_classification="structure_confirmed",
        )
        assert sig.signal == "long"
        assert sig.score >= 7
        assert sig.market == "SOL"
        # Basis dict fully populated
        _assert_basis_keys(sig)


class TestScenario2NoVolSpike:
    """7d high breakout, SM 60% long, structure_confirmed, 4h aligned, no vol spike
    -> signal=long, score = 6"""

    def test_no_vol_spike(self):
        # magnitude 0.5% -> +2, SM confirmed +2, 4h aligned +2 = 6 (no vol spike)
        closes = _make_closes(breakout="long", magnitude_pct=0.5)
        closes_4h = _make_closes(base=95.0, length=42, breakout="long", magnitude_pct=5.0)
        volumes = _make_volumes(spike=False)  # no spike

        sig = compute_hawk_breakout_signal(
            market="SOL",
            closes_1h=closes,
            closes_4h=closes_4h,
            volume_1h=volumes,
            sm_long_pct=60.0,
            structure_classification="structure_confirmed",
        )
        assert sig.signal == "long"
        assert sig.score == 6


class TestScenario3StructurePartial:
    """7d high breakout, SM 60% long, structure_partial -> signal=long, score >= 5"""

    def test_structure_partial(self):
        closes = _make_closes(breakout="long", magnitude_pct=0.5)
        closes_4h = _make_closes(base=95.0, length=42, breakout="long", magnitude_pct=5.0)
        volumes = _make_volumes(spike=False)

        sig = compute_hawk_breakout_signal(
            market="SOL",
            closes_1h=closes,
            closes_4h=closes_4h,
            volume_1h=volumes,
            sm_long_pct=60.0,
            structure_classification="structure_partial",
        )
        assert sig.signal == "long"
        assert sig.score >= 5


class TestScenario4StructureRejected:
    """7d high breakout with structure_rejected -> signal=none, score=0"""

    def test_structure_rejected(self):
        closes = _make_closes(breakout="long", magnitude_pct=0.5)
        closes_4h = _make_closes(base=95.0, length=42, breakout="long", magnitude_pct=5.0)
        volumes = _make_volumes(spike=True)

        sig = compute_hawk_breakout_signal(
            market="SOL",
            closes_1h=closes,
            closes_4h=closes_4h,
            volume_1h=volumes,
            sm_long_pct=60.0,
            structure_classification="structure_rejected",
        )
        assert sig.signal == "none"
        assert sig.score == 0
        assert "structure" in sig.notes.lower() or "rejected" in sig.notes.lower()


class TestScenario5SMGateFails:
    """7d high breakout with SM only 40% -> signal=none, score=0"""

    def test_sm_gate_fails(self):
        closes = _make_closes(breakout="long", magnitude_pct=0.5)
        closes_4h = _make_closes(base=95.0, length=42, breakout="long", magnitude_pct=5.0)
        volumes = _make_volumes(spike=True)

        sig = compute_hawk_breakout_signal(
            market="SOL",
            closes_1h=closes,
            closes_4h=closes_4h,
            volume_1h=volumes,
            sm_long_pct=40.0,
            structure_classification="structure_confirmed",
        )
        assert sig.signal == "none"
        assert sig.score == 0
        assert "sm" in sig.notes.lower() or "tilt" in sig.notes.lower()


class TestScenario6NoBreakout:
    """Price within 7-day range -> signal=none, score=0"""

    def test_no_breakout(self):
        closes = _make_closes(breakout=None)  # flat, no breakout
        closes_4h = _make_closes(base=100.0, length=42)
        volumes = _make_volumes(spike=True)

        sig = compute_hawk_breakout_signal(
            market="SOL",
            closes_1h=closes,
            closes_4h=closes_4h,
            volume_1h=volumes,
            sm_long_pct=60.0,
            structure_classification="structure_confirmed",
        )
        assert sig.signal == "none"
        assert sig.score == 0


class TestScenario7ShortBreakdown:
    """7d low breakdown, SM 65% short, structure_confirmed -> signal=short, score >= 7"""

    def test_short_breakdown(self):
        # SM 65% short means sm_long_pct = 35, so effective_tilt for short = 65
        closes = _make_closes(breakout="short", magnitude_pct=0.5)
        closes_4h = _make_closes(base=105.0, length=42, breakout="short", magnitude_pct=5.0)
        volumes = _make_volumes(spike=True)

        sig = compute_hawk_breakout_signal(
            market="SOL",
            closes_1h=closes,
            closes_4h=closes_4h,
            volume_1h=volumes,
            sm_long_pct=35.0,  # 65% short
            structure_classification="structure_confirmed",
        )
        assert sig.signal == "short"
        assert sig.score >= 7


# ---------------------------------------------------------------------------
# Edge Cases
# ---------------------------------------------------------------------------


class TestSMTiltNone:
    """Signal is none when sm_long_pct is None (VAL-HAWK-004)."""

    def test_sm_none_blocks(self):
        closes = _make_closes(breakout="long", magnitude_pct=0.5)
        closes_4h = _make_closes(base=95.0, length=42, breakout="long", magnitude_pct=5.0)
        volumes = _make_volumes(spike=True)

        sig = compute_hawk_breakout_signal(
            market="SOL",
            closes_1h=closes,
            closes_4h=closes_4h,
            volume_1h=volumes,
            sm_long_pct=None,
            structure_classification="structure_confirmed",
        )
        assert sig.signal == "none"
        assert sig.score == 0


class TestInsufficientData:
    """Handle edge cases with minimal data (VAL-HAWK-015)."""

    def test_single_close(self):
        sig = compute_hawk_breakout_signal(
            market="SOL",
            closes_1h=[100.0],
            closes_4h=[100.0],
            volume_1h=[500.0],
            sm_long_pct=60.0,
            structure_classification="structure_confirmed",
        )
        assert sig.signal == "none"
        assert sig.score == 0

    def test_two_closes_breakout(self):
        # Two closes: [100, 101] -> last > prior -> breakout
        sig = compute_hawk_breakout_signal(
            market="SOL",
            closes_1h=[100.0, 101.0],
            closes_4h=[100.0, 101.0],
            volume_1h=[500.0, 1000.0],
            sm_long_pct=60.0,
            structure_classification="structure_confirmed",
        )
        # Should produce a signal (1% breakout = magnitude 1.0% -> +3)
        # SM confirmed +2, 4h aligned +2 = 7 minimum
        assert sig.signal == "long"
        assert sig.score >= 5


class TestScoreBounds:
    """Score is always between 0 and 9 inclusive (VAL-HAWK-006)."""

    @pytest.mark.parametrize(
        "magnitude_pct,sm_long_pct,structure,vol_spike",
        [
            (0.1, 55.0, "structure_partial", False),   # low everything
            (2.0, 80.0, "structure_confirmed", True),   # high everything
            (0.5, 60.0, "structure_partial", False),
            (1.0, 70.0, "structure_confirmed", True),
        ],
    )
    def test_score_in_range(self, magnitude_pct, sm_long_pct, structure, vol_spike):
        closes = _make_closes(breakout="long", magnitude_pct=magnitude_pct)
        closes_4h = _make_closes(base=95.0, length=42, breakout="long", magnitude_pct=5.0)
        volumes = _make_volumes(spike=vol_spike)

        sig = compute_hawk_breakout_signal(
            market="SOL",
            closes_1h=closes,
            closes_4h=closes_4h,
            volume_1h=volumes,
            sm_long_pct=sm_long_pct,
            structure_classification=structure,
        )
        assert 0 <= sig.score <= 9


class TestMinScoreThreshold:
    """Signal is none when gates pass but score < 5 (VAL-HAWK-013)."""

    def test_below_threshold(self):
        # magnitude < 0.3% -> +1, SM confirmed +2 = 3 (no 4h, no vol)
        # Need to ensure 4h is NOT aligned and no vol spike
        closes = _make_closes(breakout="long", magnitude_pct=0.1)
        # 4h not aligned: closes_4h flat so no direction
        closes_4h = [100.0] * 42
        volumes = _make_volumes(spike=False)

        sig = compute_hawk_breakout_signal(
            market="SOL",
            closes_1h=closes,
            closes_4h=closes_4h,
            volume_1h=volumes,
            sm_long_pct=60.0,  # above 55% gate, below 70% strong
            structure_classification="structure_partial",
        )
        # Score: magnitude +1, SM +2 = 3 < 5 -> signal = none
        assert sig.signal == "none"
        assert sig.score == 0


class TestSMStrongTiltBonus:
    """SM tilt >= 70% in signal direction adds +1 bonus (VAL-HAWK-014)."""

    def test_strong_tilt_bonus(self):
        # With sm_long_pct=65% (not strong) vs 75% (strong), all else equal
        closes = _make_closes(breakout="long", magnitude_pct=0.5)
        closes_4h = _make_closes(base=95.0, length=42, breakout="long", magnitude_pct=5.0)
        volumes = _make_volumes(spike=True)

        sig_65 = compute_hawk_breakout_signal(
            market="SOL",
            closes_1h=closes,
            closes_4h=closes_4h,
            volume_1h=volumes,
            sm_long_pct=65.0,
            structure_classification="structure_confirmed",
        )
        sig_75 = compute_hawk_breakout_signal(
            market="SOL",
            closes_1h=closes,
            closes_4h=closes_4h,
            volume_1h=volumes,
            sm_long_pct=75.0,
            structure_classification="structure_confirmed",
        )
        assert sig_75.score == sig_65.score + 1


class TestWorksWithoutStrategyYaml:
    """Module functions correctly when config/strategy.yaml does not exist (VAL-HAWK-016)."""

    def test_without_config(self):
        with patch("engine.hawk_breakout._load_yaml_config", return_value={}):
            closes = _make_closes(breakout="long", magnitude_pct=0.5)
            closes_4h = _make_closes(base=95.0, length=42, breakout="long", magnitude_pct=5.0)
            volumes = _make_volumes(spike=True)

            sig = compute_hawk_breakout_signal(
                market="SOL",
                closes_1h=closes,
                closes_4h=closes_4h,
                volume_1h=volumes,
                sm_long_pct=60.0,
                structure_classification="structure_confirmed",
            )
            assert sig.signal == "long"
            assert sig.score >= 5


class TestBasisDictPopulation:
    """Basis dict contains all required fields when signal != none (VAL-HAWK-012)."""

    def test_basis_keys_present(self):
        closes = _make_closes(breakout="long", magnitude_pct=0.5)
        closes_4h = _make_closes(base=95.0, length=42, breakout="long", magnitude_pct=5.0)
        volumes = _make_volumes(spike=True)

        sig = compute_hawk_breakout_signal(
            market="SOL",
            closes_1h=closes,
            closes_4h=closes_4h,
            volume_1h=volumes,
            sm_long_pct=60.0,
            structure_classification="structure_confirmed",
        )
        assert sig.signal != "none"
        _assert_basis_keys(sig)
        assert sig.basis["htf_breakout"] is True
        assert sig.basis["sm_tilt_supports"] is True
        assert sig.basis["sm_long_pct"] == 60.0
        assert isinstance(sig.basis["breakout_magnitude_pct"], float)
        assert sig.basis["4h_trend_aligned"] is True
        assert sig.basis["volume_spike"] is True
        assert sig.basis["structure_classification"] == "structure_confirmed"


class TestBasisOnGateFailure:
    """Basis is populated even when gates fail."""

    def test_basis_on_no_breakout(self):
        closes = _make_closes(breakout=None)
        closes_4h = _make_closes(base=100.0, length=42)
        volumes = _make_volumes(spike=False)

        sig = compute_hawk_breakout_signal(
            market="SOL",
            closes_1h=closes,
            closes_4h=closes_4h,
            volume_1h=volumes,
            sm_long_pct=60.0,
            structure_classification="structure_confirmed",
        )
        assert sig.signal == "none"
        assert "htf_breakout" in sig.basis

    def test_basis_on_structure_rejected(self):
        closes = _make_closes(breakout="long", magnitude_pct=0.5)
        closes_4h = _make_closes(base=95.0, length=42, breakout="long", magnitude_pct=5.0)
        volumes = _make_volumes(spike=True)

        sig = compute_hawk_breakout_signal(
            market="SOL",
            closes_1h=closes,
            closes_4h=closes_4h,
            volume_1h=volumes,
            sm_long_pct=60.0,
            structure_classification="structure_rejected",
        )
        assert sig.signal == "none"
        assert sig.basis["structure_classification"] == "structure_rejected"


class TestDefaultConfig:
    """_get_hawk_config returns defaults when no strategy.yaml exists."""

    def test_defaults(self):
        with patch("engine.hawk_breakout._load_yaml_config", return_value={}):
            cfg = _get_hawk_config()
            assert cfg["sm_tilt_min_pct"] == 55
            assert cfg["sm_tilt_strong_pct"] == 70
            assert cfg["min_score"] == 5
            assert cfg["lookback_days"] == 7
            assert cfg["breakout_mag_high"] == 1.0
            assert cfg["breakout_mag_mid"] == 0.3
            assert cfg["volume_spike_multiplier"] == 1.5


class TestHawkSignalDataclass:
    """HawkSignal dataclass has required fields."""

    def test_fields(self):
        sig = HawkSignal(market="SOL", signal="long", score=7)
        assert sig.market == "SOL"
        assert sig.signal == "long"
        assert sig.score == 7
        assert sig.basis == {}
        assert sig.notes == ""

    def test_with_basis(self):
        sig = HawkSignal(
            market="ETH",
            signal="short",
            score=5,
            basis={"htf_breakout": True},
            notes="test",
        )
        assert sig.basis["htf_breakout"] is True
        assert sig.notes == "test"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_REQUIRED_BASIS_KEYS = {
    "htf_breakout",
    "sm_tilt_supports",
    "sm_long_pct",
    "breakout_magnitude_pct",
    "4h_trend_aligned",
    "volume_spike",
    "structure_classification",
}


def _assert_basis_keys(sig: HawkSignal) -> None:
    """Assert that all required basis keys are present."""
    for key in _REQUIRED_BASIS_KEYS:
        assert key in sig.basis, f"Missing basis key: {key}"
