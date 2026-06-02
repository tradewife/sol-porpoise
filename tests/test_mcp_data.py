"""Tests for engine/mcp_data.py — MCP data normalization and prompt building."""

from __future__ import annotations

import pytest

from engine.mcp_data import (
    AccountState,
    MarketOverview,
    RichMarketData,
    format_ai_prompt,
    overview_to_datapoints,
    parse_account_summary,
    parse_perps_markets,
    parse_perps_positions,
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
# parse_account_summary
# ---------------------------------------------------------------------------


class TestParseAccountSummary:
    def test_valid(self):
        raw = {"totalValueUsd": 1000, "availableUsd": 800, "withdrawableUsd": 750}
        result = parse_account_summary(raw)
        assert result.total_value_usd == 1000.0
        assert result.available_usd == 800.0
        assert result.withdrawable_usd == 750.0

    def test_missing_fields(self):
        raw = {}
        result = parse_account_summary(raw)
        assert result.total_value_usd == 0.0

    def test_non_dict(self):
        result = parse_account_summary("not a dict")
        assert result.total_value_usd == 0.0


# ---------------------------------------------------------------------------
# parse_perps_positions
# ---------------------------------------------------------------------------


class TestParsePerpsPositions:
    def test_list(self):
        raw = [{"coin": "BTC", "side": "long", "sizeUsd": 500}]
        result = parse_perps_positions(raw)
        assert len(result) == 1

    def test_dict_with_positions(self):
        raw = {"positions": [{"coin": "ETH", "side": "short"}]}
        result = parse_perps_positions(raw)
        assert len(result) == 1

    def test_empty(self):
        assert parse_perps_positions({}) == []
        assert parse_perps_positions([]) == []


# ---------------------------------------------------------------------------
# parse_perps_markets
# ---------------------------------------------------------------------------


class TestParsePerpsMarkets:
    def test_list_of_dicts(self):
        raw = {"markets": [
            {"coin": "BTC", "fundingRate": 0.001, "openInterest": 50000},
            {"coin": "ETH", "fundingRate": -0.0005, "openInterest": 30000},
        ]}
        result = parse_perps_markets(raw)
        assert "BTC" in result
        assert "ETH" in result
        assert result["BTC"]["fundingRate"] == 0.001

    def test_dict_values(self):
        raw = {"markets": {"btc": {"coin": "BTC", "fundingRate": 0.001}}}
        result = parse_perps_markets(raw)
        assert "BTC" in result

    def test_empty(self):
        assert parse_perps_markets({}) == {}
        assert parse_perps_markets({"markets": []}) == {}


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
