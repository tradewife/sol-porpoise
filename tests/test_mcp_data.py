"""Tests for engine/mcp_data.py — MCP data normalization and prompt building.

Includes tests for extract_sm_tilt, format_hawk_prompt_section, and
hawk_signals parameter (VAL-MCP-001 through VAL-MCP-009, VAL-XFLOW-002/003).
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import pytest

from engine.mcp_data import (
    AccountState,
    MarketOverview,
    RichMarketData,
    extract_sm_tilt,
    format_ai_prompt,
    format_hawk_prompt_section,
    overview_to_datapoints,
    parse_hl_account,
    parse_hl_datapoints,
    parse_hl_positions,
    parse_trading_overview,
)


# ---------------------------------------------------------------------------
# parse_trading_overview
# ---------------------------------------------------------------------------


class TestParseTradingOverview:
    def test_list_of_dicts(self):
        raw = {
            "markets": [
                {"symbol": "BTC", "price": 100000, "maxLeverage": 20, "poolUtilizationPct": 45.2, "side": "long"},
                {"symbol": "ETH", "price": 3000, "maxLeverage": 15, "poolUtilizationPct": 30.1, "side": "short"},
            ]
        }
        result = parse_trading_overview(raw)
        assert len(result) == 2
        assert result[0].symbol == "BTC"
        assert result[0].price == 100000.0
        assert result[1].symbol == "ETH"

    def test_empty(self):
        assert parse_trading_overview({}) == []
        assert parse_trading_overview({"markets": []}) == []

    def test_non_dict(self):
        assert parse_trading_overview("not a dict") == []

    def test_missing_fields(self):
        raw = {"markets": [{"symbol": "BTC"}]}
        result = parse_trading_overview(raw)
        assert len(result) == 1
        assert result[0].price == 0.0

    def test_dict_values(self):
        raw = {"markets": {"btc": {"symbol": "BTC", "price": 100000, "maxLeverage": 20, "poolUtilizationPct": 45, "side": "long"}}}
        result = parse_trading_overview(raw)
        assert len(result) == 1
        assert result[0].symbol == "BTC"


# ---------------------------------------------------------------------------
# parse_hl_datapoints
# ---------------------------------------------------------------------------


class TestParseHlDatapoints:
    def test_extracts_prices_funding_oi(self):
        from adapters.base import DataPoint, Provenance, SourceTier
        prov = Provenance(
            source_name="HL", source_tier=SourceTier.HL_NATIVE,
            source_link="", source_ts="", fetched_ts_aest="", confidence=0.9,
        )
        points = [
            DataPoint(symbol="SOL", metric="mark_price_hl", value=150.0, provenance=prov),
            DataPoint(symbol="SOL", metric="funding_rate_hl", value=0.001, provenance=prov),
            DataPoint(symbol="SOL", metric="open_interest_hl", value=50000.0, provenance=prov),
            DataPoint(symbol="BTC", metric="mark_price_hl", value=100000.0, provenance=prov),
        ]
        prices, funding, oi, vol = parse_hl_datapoints(points)
        assert prices["SOL"] == 150.0
        assert prices["BTC"] == 100000.0
        assert funding["SOL"] == 0.001
        assert oi["SOL"] == 50000.0

    def test_empty_list(self):
        prices, funding, oi, vol = parse_hl_datapoints([])
        assert prices == {}
        assert funding == {}
        assert oi == {}
        assert vol == {}


# ---------------------------------------------------------------------------
# parse_hl_account
# ---------------------------------------------------------------------------


class TestParseHlAccount:
    def test_extracts_account_metrics(self):
        from adapters.base import DataPoint, Provenance, SourceTier
        prov = Provenance(
            source_name="HL", source_tier=SourceTier.HL_NATIVE,
            source_link="", source_ts="", fetched_ts_aest="", confidence=0.9,
        )
        points = [
            DataPoint(symbol="ACCOUNT", metric="perps_total_value_usd", value=1000.0, provenance=prov),
            DataPoint(symbol="ACCOUNT", metric="perps_available_usd", value=800.0, provenance=prov),
        ]
        result = parse_hl_account(points)
        assert result.total_value_usd == 1000.0
        assert result.available_usd == 800.0

    def test_empty_points(self):
        result = parse_hl_account([])
        assert result.total_value_usd == 0.0
        assert result.available_usd == 0.0


# ---------------------------------------------------------------------------
# parse_hl_positions
# ---------------------------------------------------------------------------


class TestParseHlPositions:
    def test_returns_empty_list(self):
        result = parse_hl_positions([])
        assert result == []


# ---------------------------------------------------------------------------
# overview_to_datapoints
# ---------------------------------------------------------------------------


class TestOverviewToDatapoints:
    def test_converts_markets(self):
        data = RichMarketData(
            markets=[
                MarketOverview(symbol="BTC", price=100000, max_leverage=20, pool_utilization_pct=45, side="long"),
            ],
            raw_prices={"ETH": 3000},
            funding_rates={"SOL": 0.001},
            open_interest={"BTC": 50000},
            volume_24h={"ETH": 1000000},
        )
        points = overview_to_datapoints(data)
        assert len(points) > 0
        symbols = {p.symbol for p in points}
        assert "BTC" in symbols
        assert "ETH" in symbols
        assert "SOL" in symbols
        # Check metrics
        metrics = {p.metric for p in points}
        assert "mark_price_flash" in metrics
        assert "funding_rate" in metrics
        assert "open_interest" in metrics
        assert "volume_24h" in metrics

    def test_includes_account(self):
        data = RichMarketData(
            account=AccountState(total_value_usd=1000, available_usd=800, withdrawable_usd=750),
        )
        points = overview_to_datapoints(data)
        account_points = [p for p in points if p.symbol == "ACCOUNT"]
        assert len(account_points) == 2

    def test_empty(self):
        data = RichMarketData()
        points = overview_to_datapoints(data)
        assert points == []


# ---------------------------------------------------------------------------
# format_ai_prompt
# ---------------------------------------------------------------------------


class TestFormatAIPrompt:
    def test_basic_prompt(self):
        data = RichMarketData(
            markets=[
                MarketOverview(symbol="BTC", price=100000, max_leverage=20, pool_utilization_pct=45, side="long"),
            ],
            account=AccountState(total_value_usd=1000, available_usd=800, withdrawable_usd=750),
        )
        prompt = format_ai_prompt(data, equity=1000, max_open_trades=4, max_candidates=3)
        assert "1000 USDC" in prompt
        assert "BTC" in prompt
        assert "100,000" in prompt or "100000" in prompt
        assert "Market Data" in prompt

    def test_with_positions(self):
        data = RichMarketData()
        prompt = format_ai_prompt(
            data, equity=1000, max_open_trades=4, max_candidates=3,
            existing_positions=[{"coin": "BTC", "side": "long", "sizeUsd": 500, "entryPrice": 100000}],
        )
        assert "BTC" in prompt
        assert "Existing Positions" in prompt

    def test_with_signal_stats(self):
        data = RichMarketData()
        prompt = format_ai_prompt(
            data, equity=1000, max_open_trades=4, max_candidates=3,
            prior_signal_stats=[{"signal": "funding_stretch", "hit_rate": 0.6, "avg_R": 0.5, "n": 10}],
        )
        assert "funding_stretch" in prompt
        assert "Prior Signal Performance" in prompt

    def test_empty_data(self):
        data = RichMarketData()
        prompt = format_ai_prompt(data, equity=1000, max_open_trades=4, max_candidates=3)
        assert "1000 USDC" in prompt
        assert '"trades"' in prompt
        assert '"prompt_id"' in prompt

    def test_prompt_includes_prompt_id_and_skills(self):
        data = RichMarketData()
        prompt = format_ai_prompt(
            data,
            equity=1000,
            max_open_trades=4,
            max_candidates=3,
            prompt_id="run_test",
            active_skills="## Active Trading Skills\n\n### core\nTrade well.",
        )
        assert "Prompt ID: run_test" in prompt
        assert "Active Trading Skills" in prompt
        assert '"prompt_id": "run_test"' in prompt


# ---------------------------------------------------------------------------
# Helpers for hawk signal tests
# ---------------------------------------------------------------------------


def _make_whale_dp(symbol: str, metric: str) -> Any:
    """Create a mock whale DataPoint."""
    dp = MagicMock()
    dp.symbol = symbol
    dp.metric = metric
    return dp


def _make_hawk_signal(
    market: str = "SOL",
    signal: str = "long",
    score: int = 7,
    basis: dict | None = None,
    notes: str = "test notes",
) -> Any:
    """Create a mock HawkSignal."""
    hs = MagicMock()
    hs.market = market
    hs.signal = signal
    hs.score = score
    hs.basis = basis or {
        "htf_breakout": True,
        "sm_tilt_supports": True,
        "sm_long_pct": 62.0,
        "breakout_magnitude_pct": 0.5,
        "4h_trend_aligned": True,
        "volume_spike": True,
        "structure_classification": "structure_confirmed",
    }
    hs.notes = notes
    return hs


def _make_rich_data() -> RichMarketData:
    """Create a minimal RichMarketData for prompt tests."""
    return RichMarketData(
        markets=[
            MarketOverview(
                symbol="SOL", price=150.0, max_leverage=10.0,
                pool_utilization_pct=30.0, side="long",
            ),
        ],
        account=AccountState(
            total_value_usd=1000.0, available_usd=800.0, withdrawable_usd=500.0,
        ),
        raw_prices={"SOL": 150.0, "BTC": 50000.0, "ETH": 3000.0},
        funding_rates={"SOL": 0.0001},
        open_interest={"SOL": 1000000.0},
        volume_24h={"SOL": 50000000.0},
    )


# ===========================================================================
# extract_sm_tilt tests (VAL-MCP-001 through VAL-MCP-004)
# ===========================================================================


class TestExtractSmTilt:
    """Tests for extract_sm_tilt() helper."""

    def test_hl_leaderboard_ratio_returns_percentage(self):
        """VAL-MCP-001: HL leaderboard ratio converted to 0-100 percentage."""
        hl_market = {"topTraderLongRatio": 0.62}
        result = extract_sm_tilt("SOL", [], hl_market)
        assert result == 62.0

    def test_hl_long_ratio_key(self):
        """HL data with 'longRatio' key also works."""
        hl_market = {"longRatio": 0.75}
        result = extract_sm_tilt("BTC", [], hl_market)
        assert result == 75.0

    def test_hl_data_preferred_over_whale(self):
        """VAL-MCP-004: HL data takes precedence over whale DataPoint counting."""
        hl_market = {"topTraderLongRatio": 0.62}
        whale_points = [
            _make_whale_dp("SOL", "short_flow"),
            _make_whale_dp("SOL", "short_flow"),
            _make_whale_dp("SOL", "short_flow"),
        ]
        result = extract_sm_tilt("SOL", whale_points, hl_market)
        assert result == 62.0  # HL value, not whale-derived 0.0

    def test_whale_fallback_counting(self):
        """VAL-MCP-002: Falls back to whale DataPoint long/short counting."""
        whale_points = [
            _make_whale_dp("SOL", "long_accumulation"),
            _make_whale_dp("SOL", "long_accumulation"),
            _make_whale_dp("SOL", "long_accumulation"),
            _make_whale_dp("SOL", "short_flow"),
            _make_whale_dp("SOL", "short_flow"),
        ]
        result = extract_sm_tilt("SOL", whale_points, None)
        assert result == 60.0  # 3 long / 5 total * 100

    def test_whale_fallback_ignores_other_symbols(self):
        """Whale fallback only counts DataPoints for the target symbol."""
        whale_points = [
            _make_whale_dp("SOL", "long_accumulation"),
            _make_whale_dp("BTC", "long_accumulation"),
            _make_whale_dp("BTC", "short_flow"),
        ]
        result = extract_sm_tilt("SOL", whale_points, None)
        assert result == 100.0  # 1 long / 1 total for SOL

    def test_returns_none_when_no_data(self):
        """VAL-MCP-003: Returns None when no data available."""
        result = extract_sm_tilt("SOL", [], None)
        assert result is None

    def test_returns_none_empty_whale_no_hl(self):
        """Returns None with empty whale list and no HL data."""
        result = extract_sm_tilt("ETH", [], None)
        assert result is None

    def test_returns_none_hl_none_values(self):
        """Returns None when HL values are None."""
        hl_market = {"topTraderLongRatio": None, "longRatio": None}
        result = extract_sm_tilt("SOL", [], hl_market)
        assert result is None

    def test_hl_invalid_ratio_graceful(self):
        """Non-numeric HL ratio falls through to whale fallback."""
        hl_market = {"topTraderLongRatio": "invalid"}
        whale_points = [
            _make_whale_dp("SOL", "long_flow"),
            _make_whale_dp("SOL", "short_flow"),
        ]
        result = extract_sm_tilt("SOL", whale_points, hl_market)
        assert result == 50.0  # whale fallback


# ===========================================================================
# format_hawk_prompt_section tests (VAL-MCP-005, VAL-MCP-006)
# ===========================================================================


class TestFormatHawkPromptSection:
    """Tests for format_hawk_prompt_section() helper."""

    def test_formats_single_signal(self):
        """VAL-MCP-005: Formats HawkSignal list into prompt section."""
        sig = _make_hawk_signal(market="SOL", signal="long", score=7)
        result = format_hawk_prompt_section([sig])
        assert "## Hawk Breakout Signals" in result
        assert "### SOL" in result
        assert "signal: long" in result
        assert "score: 7/9" in result
        assert "basis:" in result
        assert "notes: test notes" in result

    def test_formats_multiple_signals(self):
        """Multiple signals produce multiple sections."""
        signals = [
            _make_hawk_signal(market="SOL", signal="long", score=7),
            _make_hawk_signal(market="BTC", signal="short", score=8),
        ]
        result = format_hawk_prompt_section(signals)
        assert "### SOL" in result
        assert "### BTC" in result
        assert "signal: short" in result
        assert "score: 8/9" in result

    def test_empty_list_shows_no_signals(self):
        """VAL-MCP-006: Empty list produces 'No signals computed' message."""
        result = format_hawk_prompt_section([])
        assert "## Hawk Breakout Signals" in result
        assert "No signals computed this cycle." in result


# ===========================================================================
# format_ai_prompt hawk_signals tests (VAL-MCP-007 through VAL-MCP-009)
# ===========================================================================


class TestFormatAiPromptHawkSignals:
    """Tests for format_ai_prompt() hawk_signals parameter."""

    def test_accepts_hawk_signals_parameter(self):
        """VAL-MCP-007: format_ai_prompt accepts hawk_signals without TypeError."""
        data = _make_rich_data()
        sig = _make_hawk_signal()
        prompt = format_ai_prompt(
            market_data=data,
            equity=1000.0,
            max_open_trades=4,
            max_candidates=3,
            prompt_id="test-001",
            hawk_signals=[sig],
        )
        assert "## Hawk Breakout Signals" in prompt

    def test_backward_compatible_default_none(self):
        """VAL-MCP-008: No hawk_signals produces same output as before."""
        data = _make_rich_data()
        prompt = format_ai_prompt(
            market_data=data,
            equity=1000.0,
            max_open_trades=4,
            max_candidates=3,
            prompt_id="test-002",
        )
        assert "## Hawk Breakout Signals" not in prompt

    def test_hawk_section_after_twitter_before_account(self):
        """VAL-MCP-009: Hawk section injected after twitter, before Account."""
        data = _make_rich_data()
        sig = _make_hawk_signal()
        prompt = format_ai_prompt(
            market_data=data,
            equity=1000.0,
            max_open_trades=4,
            max_candidates=3,
            prompt_id="test-003",
            twitter_results=[],  # triggers twitter section
            hawk_signals=[sig],
        )
        hawk_idx = prompt.index("Hawk Breakout Signals")
        account_idx = prompt.index("## Account")
        # Hawk must come before Account
        assert hawk_idx < account_idx

    def test_hawk_section_position_no_twitter(self):
        """Hawk section appears before Account even without twitter_results."""
        data = _make_rich_data()
        sig = _make_hawk_signal()
        prompt = format_ai_prompt(
            market_data=data,
            equity=1000.0,
            max_open_trades=4,
            max_candidates=3,
            prompt_id="test-004",
            hawk_signals=[sig],
        )
        hawk_idx = prompt.index("Hawk Breakout Signals")
        account_idx = prompt.index("## Account")
        assert hawk_idx < account_idx

    def test_hawk_signals_empty_list_shows_section(self):
        """Empty hawk_signals list still shows the 'No signals' message."""
        data = _make_rich_data()
        prompt = format_ai_prompt(
            market_data=data,
            equity=1000.0,
            max_open_trades=4,
            max_candidates=3,
            prompt_id="test-005",
            hawk_signals=[],
        )
        assert "## Hawk Breakout Signals" in prompt
        assert "No signals computed this cycle." in prompt


# ===========================================================================
# Integration: extract_sm_tilt → hawk_signal → prompt section (VAL-XFLOW)
# ===========================================================================


class TestHawkDataFlowIntegration:
    """VAL-XFLOW-002/003: End-to-end data flow tests."""

    def test_sm_tilt_from_hl_flows_to_hawk_signal(self):
        """SM tilt from HL data feeds into hawk signal computation."""
        from engine.hawk_breakout import compute_hawk_breakout_signal

        sm_pct = extract_sm_tilt("SOL", [], {"topTraderLongRatio": 0.65})
        assert sm_pct == 65.0

        closes = [100.0] * 167 + [101.5]  # 7d high breakout
        vols = [100.0] * 167 + [200.0]    # volume spike

        sig = compute_hawk_breakout_signal(
            market="SOL",
            closes_1h=closes,
            closes_4h=closes,
            volume_1h=vols,
            sm_long_pct=sm_pct,
            structure_classification="structure_confirmed",
        )
        assert sig.signal == "long"
        assert sig.score >= 5

        prompt = format_ai_prompt(
            market_data=_make_rich_data(),
            equity=1000.0,
            max_open_trades=4,
            max_candidates=3,
            prompt_id="xflow-test",
            hawk_signals=[sig],
        )
        assert "## Hawk Breakout Signals" in prompt
        assert "signal: long" in prompt
        assert f"score: {sig.score}/9" in prompt

    def test_structure_rejected_propagates_to_prompt(self):
        """VAL-XFLOW-003: structure_rejected → signal=none → shown in prompt."""
        from engine.hawk_breakout import compute_hawk_breakout_signal

        closes = [100.0] * 167 + [101.5]
        sig = compute_hawk_breakout_signal(
            market="SOL",
            closes_1h=closes,
            closes_4h=closes,
            volume_1h=[100.0] * 168,
            sm_long_pct=65.0,
            structure_classification="structure_rejected",
        )
        assert sig.signal == "none"
        assert sig.score == 0

        prompt = format_ai_prompt(
            market_data=_make_rich_data(),
            equity=1000.0,
            max_open_trades=4,
            max_candidates=3,
            prompt_id="xflow-reject",
            hawk_signals=[sig],
        )
        assert "signal: none" in prompt
