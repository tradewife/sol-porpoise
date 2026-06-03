"""Tests for dextrabot, cross-venue, hypothesis, and source health modules."""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from adapters.base import DataPoint, Provenance, SourceTier
from adapters.normalizer import make_provenance


# ---------------------------------------------------------------------------
# Dextrabot fixture data (mimics real API response)
# ---------------------------------------------------------------------------

DEXT_FIXTURE_WALLETS = {
    "count": 3,
    "next": "?limit=3&offset=3",
    "previous": None,
    "results": [
        {
            "user_token": "0xabc1230000000000000000000000000000000001",
            "portfolio_perp_week_pnl": "125000.50",
            "total_unrealized_pnl": "5000.00",
            "win_complated_rate": "65.0",
            "total_win_rate": "62.0",
            "portfolio_perp_week_sharpe": "2.1",
            "avg_uleverage_value": "5.0",
            "portfolio_perp_week_growth_rate": "45.0",
            "portfolio_perp_week_dd": "-12.5",
            "rtx_count": "150",
            "margin_roi": 150.2,
        },
        {
            "user_token": "0xdef4560000000000000000000000000000000002",
            "portfolio_perp_week_pnl": "-80000.25",
            "total_unrealized_pnl": "-2000.00",
            "win_complated_rate": "40.0",
            "total_win_rate": "42.0",
            "portfolio_perp_week_sharpe": "0.5",
            "avg_uleverage_value": "3.0",
            "portfolio_perp_week_growth_rate": "350.0",
            "portfolio_perp_week_dd": "-8.0",
            "rtx_count": "55",
            "margin_roi": 4461.4,
        },
        {
            "user_token": "0x7890000000000000000000000000000000000003",
            "portfolio_perp_week_pnl": "55000.75",
            "total_unrealized_pnl": "1000.00",
            "win_complated_rate": "30.0",
            "total_win_rate": "35.0",
            "portfolio_perp_week_sharpe": "0.8",
            "avg_uleverage_value": "2.5",
            "portfolio_perp_week_growth_rate": "50.0",
            "portfolio_perp_week_dd": "-15.0",
            "rtx_count": "20",
            "margin_roi": 67.9,
        },
    ],
}


# ---------------------------------------------------------------------------
# Dextrabot tests (VAL-DEX-001 through VAL-DEX-004)
# ---------------------------------------------------------------------------

class TestDextrabotAdapter:
    def test_provenance(self) -> None:
        from adapters.dextrabot import DextrabotAdapter
        adapter = DextrabotAdapter()
        p = adapter.provenance()
        assert "Dextrabot" in p.source_name
        assert p.source_tier == SourceTier.OPEN

    def test_classify_entity_smart_money(self) -> None:
        """VAL-DEX-002: sharpe > 1.5 AND win_rate > 55 → smart_money."""
        from adapters.dextrabot import WalletData, classify_entity
        w = WalletData(address="test", sharpe=2.0, win_rate=60.0, pnl=5000)
        assert classify_entity(w) == "smart_money"

    def test_classify_entity_whale_unlabeled(self) -> None:
        """VAL-DEX-002: |pnl| > 10000 but not smart_money → whale_unlabeled."""
        from adapters.dextrabot import WalletData, classify_entity
        w = WalletData(address="test", pnl=50000)
        assert classify_entity(w) == "whale_unlabeled"

    def test_classify_entity_roi_whale(self) -> None:
        """VAL-DEX-002: growth_rate > 200 AND tx_count > 30 → roi_whale."""
        from adapters.dextrabot import WalletData, classify_entity
        w = WalletData(address="test", growth_rate=350.0, tx_count=55)
        assert classify_entity(w) == "roi_whale"

    def test_classify_entity_roi_whale_low_tx_count(self) -> None:
        """VAL-DEX-002: growth_rate > 200 but tx_count <= 30 → not roi_whale."""
        from adapters.dextrabot import WalletData, classify_entity
        w = WalletData(address="test", growth_rate=350.0, tx_count=20, pnl=50000)
        assert classify_entity(w) == "whale_unlabeled"

    def test_classify_entity_roi_whale_low_growth(self) -> None:
        """VAL-DEX-002: tx_count > 30 but growth_rate <= 200 → not roi_whale."""
        from adapters.dextrabot import WalletData, classify_entity
        w = WalletData(address="test", growth_rate=150.0, tx_count=50, pnl=50000)
        assert classify_entity(w) == "whale_unlabeled"

    def test_classify_entity_unknown(self) -> None:
        """VAL-DEX-002: no criteria met → unknown."""
        from adapters.dextrabot import WalletData, classify_entity
        w = WalletData(address="test", pnl=100)
        assert classify_entity(w) == "unknown"

    def test_classify_entity_smart_money_takes_priority(self) -> None:
        """VAL-DEX-002: smart_money takes priority over roi_whale and whale_unlabeled."""
        from adapters.dextrabot import WalletData, classify_entity
        w = WalletData(address="test", sharpe=2.0, win_rate=60.0, pnl=100000, growth_rate=300, tx_count=50)
        assert classify_entity(w) == "smart_money"

    def test_fetch_wallets_returns_whale_pnl_metric(self, tmp_path: Path) -> None:
        """VAL-DEX-001: fetch_wallets returns DataPoints with whale_pnl metric."""
        from adapters.dextrabot import DextrabotAdapter

        adapter = DextrabotAdapter(cache_dir=tmp_path)
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = DEXT_FIXTURE_WALLETS
        mock_response.text = json.dumps(DEXT_FIXTURE_WALLETS)
        mock_response.raise_for_status = MagicMock()

        with patch.object(adapter._session, "get", return_value=mock_response):
            points = adapter.fetch_wallets()

        assert len(points) > 0
        assert all(isinstance(p, DataPoint) for p in points)
        assert all(p.metric == "whale_pnl" for p in points)
        assert all(isinstance(p.value, float) and math.isfinite(p.value) for p in points)
        assert all(p.provenance.source_name == "Dextrabot" for p in points)

    def test_fetch_wallets_entity_types_in_attrs(self, tmp_path: Path) -> None:
        """VAL-DEX-001: DataPoints contain entity_type in attrs."""
        from adapters.dextrabot import DextrabotAdapter

        adapter = DextrabotAdapter(cache_dir=tmp_path)
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = DEXT_FIXTURE_WALLETS
        mock_response.text = json.dumps(DEXT_FIXTURE_WALLETS)
        mock_response.raise_for_status = MagicMock()

        with patch.object(adapter._session, "get", return_value=mock_response):
            points = adapter.fetch_wallets()

        entity_types = {p.attrs.get("entity_type") for p in points}
        assert len(entity_types) > 0
        # Should include smart_money (wallet 1), roi_whale (wallet 2), whale_unlabeled (wallet 3)
        assert "smart_money" in entity_types
        assert "roi_whale" in entity_types
        assert "whale_unlabeled" in entity_types

    def test_fetch_wallets_filter_params(self, tmp_path: Path) -> None:
        """VAL-DEX-003: optimal filter params applied in request."""
        from adapters.dextrabot import DextrabotAdapter, DEFAULT_FILTERS

        adapter = DextrabotAdapter(cache_dir=tmp_path)
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"count": 0, "results": []}
        mock_response.text = '{"count": 0, "results": []}'
        mock_response.raise_for_status = MagicMock()

        with patch.object(adapter._session, "get", return_value=mock_response) as mock_get:
            adapter.fetch_wallets()

        assert mock_get.called
        call_args = mock_get.call_args
        params = call_args[1].get("params", call_args.kwargs.get("params", {}))

        # Verify all 6 optimal filters are present
        assert params.get("period") == DEFAULT_FILTERS["period"]
        assert params.get("min_pnl") == DEFAULT_FILTERS["min_pnl"]
        assert params.get("min_win_complated_rate") == DEFAULT_FILTERS["min_win_complated_rate"]
        assert params.get("min_complated_trades_count") == DEFAULT_FILTERS["min_complated_trades_count"]
        assert params.get("order") == DEFAULT_FILTERS["order"]
        assert params.get("coin") == DEFAULT_FILTERS["coin"]

    def test_fetch_wallets_empty_response(self, tmp_path: Path) -> None:
        """VAL-DEX-004: empty response returns [] gracefully."""
        from adapters.dextrabot import DextrabotAdapter

        adapter = DextrabotAdapter(cache_dir=tmp_path)
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"count": 0, "next": None, "previous": None, "results": []}
        mock_response.text = '{"count": 0, "next": null, "previous": null, "results": []}'
        mock_response.raise_for_status = MagicMock()

        with patch.object(adapter._session, "get", return_value=mock_response):
            points = adapter.fetch_wallets()

        assert points == []

    def test_fetch_wallets_connection_error_returns_empty(self, tmp_path: Path) -> None:
        """VAL-DEX-004: connection error returns [] without raising."""
        from adapters.dextrabot import DextrabotAdapter

        adapter = DextrabotAdapter(cache_dir=tmp_path)
        with patch.object(adapter._session, "get", side_effect=ConnectionError("timeout")):
            points = adapter.fetch_wallets()

        assert points == []

    def test_parse_period(self) -> None:
        from adapters.dextrabot import DextrabotAdapter
        assert DextrabotAdapter._parse_period("7D") == 7
        assert DextrabotAdapter._parse_period("30D") == 30
        assert DextrabotAdapter._parse_period("1D") == 1
        assert DextrabotAdapter._parse_period("14d") == 14
        assert DextrabotAdapter._parse_period("invalid") == 7  # default

    def test_map_sort(self) -> None:
        from adapters.dextrabot import DextrabotAdapter
        assert DextrabotAdapter._map_sort("roe") == "-margin_roi"
        assert DextrabotAdapter._map_sort("pnl") == "-perp_pnl"
        assert DextrabotAdapter._map_sort("unknown") == "-margin_roi"  # default

    def test_rate_limiter(self) -> None:
        from adapters.dextrabot import RateLimiter
        rl = RateLimiter(max_requests=3, per_seconds=1)
        # First 3 should be instant
        for _ in range(3):
            rl.wait()
        # Fourth might need to wait — just test it doesn't crash

    def test_response_cache(self, tmp_path: Path) -> None:
        from adapters.dextrabot import ResponseCache
        cache = ResponseCache(tmp_path, ttl_seconds=60)
        assert cache.get("http://test.com") is None
        cache.put("http://test.com", '{"key": "value"}')
        assert cache.get("http://test.com") == '{"key": "value"}'

    def test_api_url_constant(self) -> None:
        """Verify the discovered API endpoint URL is correct."""
        from adapters.dextrabot import DEXT_API_URL
        assert "dextradata.nftinit.io" in DEXT_API_URL
        assert "get_wallets_profit_new" in DEXT_API_URL

    def test_default_filters(self) -> None:
        """Verify optimal default filter params."""
        from adapters.dextrabot import DEFAULT_FILTERS
        assert DEFAULT_FILTERS["period"] == 7
        assert DEFAULT_FILTERS["order"] == "-margin_roi"
        assert DEFAULT_FILTERS["coin"] == "SOL"
        assert DEFAULT_FILTERS["min_pnl"] == 50000
        assert DEFAULT_FILTERS["min_win_complated_rate"] == 55
        assert DEFAULT_FILTERS["min_complated_trades_count"] == 30

    def test_health_check(self) -> None:
        from adapters.dextrabot import DextrabotAdapter
        adapter = DextrabotAdapter()
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        with patch.object(adapter._session, "get", return_value=mock_resp):
            health = adapter.health_check()
        assert health.name == "Dextrabot"
        assert health.healthy is True

    def test_health_check_failure(self) -> None:
        from adapters.dextrabot import DextrabotAdapter
        adapter = DextrabotAdapter()
        with patch.object(adapter._session, "get", side_effect=ConnectionError("fail")):
            health = adapter.health_check()
        assert health.name == "Dextrabot"
        assert health.healthy is False
        assert health.error_message is not None


# ---------------------------------------------------------------------------
# Cross-venue tests
# ---------------------------------------------------------------------------

class TestCrossVenue:
    def test_compute_basis_normal(self) -> None:
        from engine.cross_venue import compute_basis
        result = compute_basis("SOL", 150.5, 150.0, "Imperial")
        assert abs(result.basis_bp - 33.33) < 1
        assert result.is_flagged  # > 15bp

    def test_compute_basis_within_threshold(self) -> None:
        from engine.cross_venue import compute_basis
        result = compute_basis("BTC", 100000.0, 99995.0, "Imperial")
        assert abs(result.basis_bp) < 15
        assert not result.is_flagged

    def test_compute_basis_funding_alignment(self) -> None:
        from engine.cross_venue import compute_basis
        result = compute_basis("ETH", 3010, 3000, "Imperial", funding_rate=0.0001)
        assert result.funding_alignment == "aligned_positive"

    def test_venue_dominance(self) -> None:
        from engine.cross_venue import compute_venue_dominance
        result = compute_venue_dominance("BTC", {
            "Hyperliquid": 50000, "Binance": 30000, "OKX": 20000,
        })
        assert result.leading_venue == "Hyperliquid"
        assert abs(result.hl_share - 0.5) < 0.01

    def test_whale_signal_integration(self) -> None:
        from engine.cross_venue import integrate_whale_signals
        from engine.scoring import GraphSignalScore
        score = GraphSignalScore(symbol="SOL")
        prov = make_provenance("Dextrabot", SourceTier.OPEN, confidence=0.7)
        whales = [
            DataPoint(symbol="HYPERLIQUID", metric="whale_pnl", value=10000, provenance=prov,
                      attrs={"entity_type": "smart_money", "sharpe": 2.0}),
            DataPoint(symbol="HYPERLIQUID", metric="whale_pnl", value=5000, provenance=prov,
                      attrs={"entity_type": "smart_money", "sharpe": 1.8}),
        ]
        updated = integrate_whale_signals(score, whales)
        assert "whale_evidence" in updated.components
        assert updated.components["whale_evidence"].label == "smart_money_directional"

    def test_whale_signal_missing_data(self) -> None:
        from engine.cross_venue import integrate_whale_signals
        from engine.scoring import GraphSignalScore
        score = GraphSignalScore(symbol="BTC")
        updated = integrate_whale_signals(score, [])
        assert updated.components["whale_evidence"].label == "unknown"

    def test_cross_venue_conflict_detection(self) -> None:
        from engine.cross_venue import check_cross_venue_consistency
        prov1 = make_provenance("HL API", SourceTier.HL_NATIVE, confidence=0.95)
        prov2 = make_provenance("Imperial API", SourceTier.SOLANA_NATIVE, confidence=0.95)
        points = [
            DataPoint(symbol="SOL", metric="mark_price", value=150.0, provenance=prov1),
            DataPoint(symbol="SOL", metric="mark_price", value=152.0, provenance=prov2),
        ]
        conflicts = check_cross_venue_consistency("SOL", points)
        assert len(conflicts) >= 1
        assert "CONFLICT" in conflicts[0]


# ---------------------------------------------------------------------------
# Hypothesis tests
# ---------------------------------------------------------------------------

class TestHypothesis:
    def test_create_hypothesis(self, tmp_path: Path) -> None:
        from engine.hypothesis import Hypothesis, HypothesisRegistry
        reg = HypothesisRegistry(tmp_path / "hypothesis.csv")
        h = Hypothesis(
            hypothesis_id="hyp_001", created_ts="2026-06-01 08:00:00 Australia/Sydney",
            edge_claim="Funding stretch mean reversion", mechanism="Fade stretched funding",
            symbol_scope="BTC,ETH,SOL", min_sample=10,
        )
        reg.create(h)
        all_h = reg.read_all()
        assert len(all_h) == 1
        assert all_h[0].edge_claim == "Funding stretch mean reversion"

    def test_update_status(self, tmp_path: Path) -> None:
        from engine.hypothesis import Hypothesis, HypothesisRegistry, HypothesisStatus
        reg = HypothesisRegistry(tmp_path / "hypothesis.csv")
        h = Hypothesis(hypothesis_id="hyp_001", created_ts="2026-06-01 08:00:00 Australia/Sydney")
        reg.create(h)
        reg.update_status("hyp_001", HypothesisStatus.REJECTED, "Insufficient edge")
        active = reg.query_active()
        assert len(active) == 0
        all_h = reg.read_all()
        assert all_h[0].status == "rejected"

    def test_update_result(self, tmp_path: Path) -> None:
        from engine.hypothesis import Hypothesis, HypothesisRegistry
        reg = HypothesisRegistry(tmp_path / "hypothesis.csv")
        h = Hypothesis(hypothesis_id="hyp_002", created_ts="2026-06-01 08:00:00 Australia/Sydney")
        reg.create(h)
        reg.update_result("hyp_002", 15, "avg_R=0.3")
        all_h = reg.read_all()
        assert all_h[0].current_n == 15

    def test_csv_schema(self) -> None:
        from engine.hypothesis import HYPOTHESIS_HEADER
        assert "hypothesis_id" in HYPOTHESIS_HEADER
        assert "provenance_tags" in HYPOTHESIS_HEADER
        assert len(HYPOTHESIS_HEADER.split(",")) == 15


# ---------------------------------------------------------------------------
# Source health tests
# ---------------------------------------------------------------------------

class TestSourceHealth:
    def test_record_success(self, tmp_path: Path) -> None:
        from engine.source_health import SourceHealthTracker
        tracker = SourceHealthTracker(tmp_path / "source_health.csv")
        tracker.record_success("Imperial API", "Solana-native", latency_ms=50)
        records = tracker.read_all()
        assert len(records) == 1
        assert records[0].status == "healthy"

    def test_record_failure(self, tmp_path: Path) -> None:
        from engine.source_health import SourceHealthTracker
        tracker = SourceHealthTracker(tmp_path / "source_health.csv")
        tracker.record_failure("Dextrabot", "Open", error="HTML parse failed")
        records = tracker.read_all()
        assert len(records) == 1
        assert records[0].status == "degraded"
        assert records[0].confidence_adjustment < 1.0

    def test_confidence_default(self, tmp_path: Path) -> None:
        from engine.source_health import SourceHealthTracker
        tracker = SourceHealthTracker(tmp_path / "source_health.csv")
        assert tracker.get_confidence("unknown_source") == 1.0

    def test_upsert_merges(self, tmp_path: Path) -> None:
        from engine.source_health import SourceHealthTracker
        tracker = SourceHealthTracker(tmp_path / "source_health.csv")
        tracker.record_success("Imperial API", "Solana-native")
        tracker.record_failure("Imperial API", "Solana-native", error="timeout")
        records = tracker.read_all()
        assert len(records) == 1
        assert records[0].last_success_ts  # kept from first write
        assert records[0].status == "degraded"

    def test_signal_outcome_scorer(self, tmp_path: Path) -> None:
        from engine.source_health import SignalOutcomeScorer
        scorer = SignalOutcomeScorer(tmp_path / "signal_outcomes.csv")
        scorer.update_stats({
            "funding_stretch": [1.5, -0.5, 2.0, 0.3, -1.0],
            "oi_delta": [0.5, 0.8],
        })
        stats = scorer.read_stats()
        assert "funding_stretch" in stats
        assert stats["funding_stretch"]["n"] == 5
        assert stats["oi_delta"]["hit_rate"] == 1.0  # both positive
