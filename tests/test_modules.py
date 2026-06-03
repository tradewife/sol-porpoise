"""Tests for dextrabot, cross-venue, hypothesis, and source health modules."""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import httpx
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


# ---------------------------------------------------------------------------
# Hyperdash fixture data (mimics real GraphQL response)
# ---------------------------------------------------------------------------

HYPERDASH_FIXTURE_RESPONSE = {
    "data": {
        "analytics": {
            "cohortSummary": {
                "timestamp": "2026-06-03T03:42:33.230Z",
                "totalTraders": 100159,
                "sizeCohorts": [
                    {
                        "id": "apex",
                        "label": "Apex",
                        "range": "$5M+",
                        "totalTraders": 182,
                        "longNotional": 1143647899.766456,
                        "shortNotional": 1708273004.4482234,
                        "topMarkets": [
                            {"ticker": "BTC", "longNotional": 190135793.40987, "shortNotional": 329801070.46634},
                            {"ticker": "HYPE", "longNotional": 117183597.59758, "shortNotional": 253376125.82778},
                            {"ticker": "ETH", "longNotional": 93735178.50809, "shortNotional": 165796745.77965},
                        ],
                    },
                    {
                        "id": "whale",
                        "label": "Whale",
                        "range": "$1M - $5M",
                        "totalTraders": 585,
                        "longNotional": 752723222.1816001,
                        "shortNotional": 661097650.954182,
                        "topMarkets": [
                            {"ticker": "BTC", "longNotional": 132800714.51548, "shortNotional": 133096986.84744},
                            {"ticker": "HYPE", "longNotional": 135810629.06266, "shortNotional": 119229862.64846},
                            {"ticker": "ETH", "longNotional": 40074255.35068, "shortNotional": 62353007.64627},
                            {"ticker": "SOL", "longNotional": 20182134.24771, "shortNotional": 20137246.467},
                        ],
                    },
                    {
                        "id": "large",
                        "label": "Large",
                        "range": "$100K - $1M",
                        "totalTraders": 3595,
                        "longNotional": 805013831.5355067,
                        "shortNotional": 507006051.6757757,
                        "topMarkets": [
                            {"ticker": "BTC", "longNotional": 147160599.55454, "shortNotional": 154358756.42594},
                            {"ticker": "HYPE", "longNotional": 121805660.21106, "shortNotional": 68977717.12142},
                            {"ticker": "ETH", "longNotional": 39771250.39831, "shortNotional": 44389879.32998},
                            {"ticker": "SOL", "longNotional": 24198252.04956, "shortNotional": 15022489.01901},
                        ],
                    },
                    {
                        "id": "medium",
                        "label": "Medium",
                        "range": "$10K - $100K",
                        "totalTraders": 10923,
                        "longNotional": 315278859.79741824,
                        "shortNotional": 182731499.35139278,
                        "topMarkets": [
                            {"ticker": "BTC", "longNotional": 63959462.85377, "shortNotional": 56831553.59398},
                            {"ticker": "SOL", "longNotional": 9611972.62173, "shortNotional": 7139011.10826},
                        ],
                    },
                ],
            }
        }
    }
}


# ---------------------------------------------------------------------------
# Hyperdash tests (VAL-HDASH-001 through VAL-HDASH-005)
# ---------------------------------------------------------------------------


class TestHyperdashAdapter:
    def test_protocol_conformance(self) -> None:
        """VAL-HDASH-001: HyperdashAdapter implements DataAdapter protocol."""
        from adapters.base import DataAdapter
        from adapters.hyperdash import HyperdashAdapter
        adapter = HyperdashAdapter()
        assert isinstance(adapter, DataAdapter)
        assert hasattr(adapter, "fetch")
        assert hasattr(adapter, "provenance")
        assert hasattr(adapter, "health_check")
        assert callable(adapter.fetch)
        assert callable(adapter.provenance)
        assert callable(adapter.health_check)

    def test_cohort_extraction(self) -> None:
        """VAL-HDASH-002: Extracts cohort metrics for Large Whale and Whale tiers."""
        from adapters.hyperdash import HyperdashAdapter

        adapter = HyperdashAdapter()
        points = adapter._parse_response(HYPERDASH_FIXTURE_RESPONSE)

        assert len(points) >= 6  # 3 metrics x 2 tiers = 6 minimum

        metrics = {}
        for p in points:
            key = (p.symbol, p.metric, p.attrs.get("cohort_id"))
            metrics[key] = p

        # Check whale cohort ($1M-$5M): SOL long=20,182,134 / (20,182,134 + 20,137,246) ≈ 50.06%
        whale_long_pct = metrics.get(("SOL", "whale_cohort_long_pct", "whale"))
        assert whale_long_pct is not None
        assert isinstance(whale_long_pct.value, float)
        assert abs(whale_long_pct.value - 50.06) < 1.0  # ~50% neutral

        whale_direction = metrics.get(("SOL", "cohort_direction", "whale"))
        assert whale_direction is not None
        assert whale_direction.value == "neutral"  # 50% is in 45-55% range

        whale_oi = metrics.get(("SOL", "cohort_oi_usd", "whale"))
        assert whale_oi is not None
        assert isinstance(whale_oi.value, float)
        assert whale_oi.value > 0

        # Check large cohort ($100K-$1M): SOL long=24,198,252 / (24,198,252 + 15,022,489) ≈ 61.69%
        large_long_pct = metrics.get(("SOL", "whale_cohort_long_pct", "large"))
        assert large_long_pct is not None
        assert abs(large_long_pct.value - 61.69) < 1.0  # ~62%

        large_direction = metrics.get(("SOL", "cohort_direction", "large"))
        assert large_direction is not None
        assert large_direction.value == "long"  # >55%

        large_oi = metrics.get(("SOL", "cohort_oi_usd", "large"))
        assert large_oi is not None
        assert isinstance(large_oi.value, float)
        assert large_oi.value > 0

    def test_cohort_direction_thresholds(self) -> None:
        """VAL-HDASH-002: cohort_direction thresholds: long >55%, short <45%, neutral 45-55%."""
        from adapters.hyperdash import _compute_cohort_direction
        assert _compute_cohort_direction(62.0) == "long"
        assert _compute_cohort_direction(55.1) == "long"
        assert _compute_cohort_direction(38.0) == "short"
        assert _compute_cohort_direction(44.9) == "short"
        assert _compute_cohort_direction(50.0) == "neutral"
        assert _compute_cohort_direction(45.0) == "neutral"
        assert _compute_cohort_direction(55.0) == "neutral"
        assert _compute_cohort_direction(45.1) == "neutral"
        assert _compute_cohort_direction(54.9) == "neutral"

    def test_provenance(self) -> None:
        """VAL-HDASH-003: Provenance source_tier=OPEN, confidence=0.75."""
        from adapters.hyperdash import HyperdashAdapter
        adapter = HyperdashAdapter()
        prov = adapter.provenance()
        assert prov.source_tier == SourceTier.OPEN
        assert prov.confidence == 0.75
        assert "Hyperdash" in prov.source_name
        assert "graphql" in prov.source_link

    def test_graceful_failure_connection_error(self) -> None:
        """VAL-HDASH-004: Connection error returns empty list."""
        import asyncio
        from adapters.hyperdash import HyperdashAdapter

        adapter = HyperdashAdapter()
        with patch.object(adapter._client, "post", side_effect=httpx.ConnectError("timeout")):
            result = asyncio.run(adapter.fetch())
        assert result == []

    def test_graceful_failure_timeout(self) -> None:
        """VAL-HDASH-004: Timeout returns empty list."""
        import asyncio
        from adapters.hyperdash import HyperdashAdapter

        adapter = HyperdashAdapter()
        with patch.object(adapter._client, "post", side_effect=httpx.ReadTimeout("read timeout")):
            result = asyncio.run(adapter.fetch())
        assert result == []

    def test_graceful_failure_invalid_json(self) -> None:
        """VAL-HDASH-004: Invalid JSON returns empty list."""
        import asyncio
        from adapters.hyperdash import HyperdashAdapter

        adapter = HyperdashAdapter()
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.raise_for_status = MagicMock()
        mock_response.json.side_effect = ValueError("invalid json")

        with patch.object(adapter._client, "post", return_value=mock_response):
            result = asyncio.run(adapter.fetch())
        assert result == []

    def test_graceful_failure_http_500(self) -> None:
        """VAL-HDASH-004: HTTP 500 returns empty list."""
        import asyncio
        from adapters.hyperdash import HyperdashAdapter

        adapter = HyperdashAdapter()
        mock_response = MagicMock()
        mock_response.status_code = 500
        mock_response.raise_for_status.side_effect = httpx.HTTPStatusError(
            "Server Error", request=MagicMock(), response=mock_response,
        )

        with patch.object(adapter._client, "post", return_value=mock_response):
            result = asyncio.run(adapter.fetch())
        assert result == []

    def test_sol_only_filter(self) -> None:
        """VAL-HDASH-005: Only SOL data is returned, other assets excluded."""
        from adapters.hyperdash import HyperdashAdapter

        adapter = HyperdashAdapter()
        points = adapter._parse_response(HYPERDASH_FIXTURE_RESPONSE)

        # All DataPoints should be SOL only
        assert all(p.symbol == "SOL" for p in points)

        # Should NOT have BTC, ETH, HYPE data points
        non_sol = [p for p in points if p.symbol != "SOL"]
        assert len(non_sol) == 0

    def test_sol_only_fixture_with_multiple_assets(self) -> None:
        """VAL-HDASH-005: Fixture with BTC and SOL only returns SOL points."""
        from adapters.hyperdash import HyperdashAdapter

        fixture = {
            "data": {
                "analytics": {
                    "cohortSummary": {
                        "timestamp": "2026-06-03T03:42:33.230Z",
                        "totalTraders": 100,
                        "sizeCohorts": [
                            {
                                "id": "whale",
                                "label": "Whale",
                                "range": "$1M - $5M",
                                "totalTraders": 10,
                                "longNotional": 1000000,
                                "shortNotional": 500000,
                                "topMarkets": [
                                    {"ticker": "BTC", "longNotional": 500000, "shortNotional": 300000},
                                    {"ticker": "SOL", "longNotional": 200000, "shortNotional": 100000},
                                ],
                            },
                        ],
                    }
                }
            }
        }

        adapter = HyperdashAdapter()
        points = adapter._parse_response(fixture)

        # Only SOL DataPoints
        assert all(p.symbol == "SOL" for p in points)
        assert len(points) == 3  # 3 metrics for 1 cohort

    def test_health_check_success(self) -> None:
        """Health check returns healthy=True on 200 response."""
        from adapters.hyperdash import HyperdashAdapter

        adapter = HyperdashAdapter()
        mock_response = MagicMock()
        mock_response.status_code = 200

        with patch.object(adapter._client, "post", return_value=mock_response):
            health = adapter.health_check()

        assert health.name == "Hyperdash"
        assert health.healthy is True
        assert health.latency_ms is not None

    def test_health_check_failure(self) -> None:
        """Health check returns healthy=False on connection error."""
        from adapters.hyperdash import HyperdashAdapter

        adapter = HyperdashAdapter()
        with patch.object(adapter._client, "post", side_effect=httpx.ConnectError("fail")):
            health = adapter.health_check()

        assert health.name == "Hyperdash"
        assert health.healthy is False
        assert health.error_message is not None

    def test_no_sol_in_top_markets_returns_empty(self) -> None:
        """When SOL is not in any cohort's topMarkets, returns empty list."""
        from adapters.hyperdash import HyperdashAdapter

        fixture = {
            "data": {
                "analytics": {
                    "cohortSummary": {
                        "timestamp": "2026-06-03T03:42:33.230Z",
                        "totalTraders": 100,
                        "sizeCohorts": [
                            {
                                "id": "whale",
                                "label": "Whale",
                                "range": "$1M - $5M",
                                "totalTraders": 10,
                                "longNotional": 1000000,
                                "shortNotional": 500000,
                                "topMarkets": [
                                    {"ticker": "BTC", "longNotional": 500000, "shortNotional": 300000},
                                    {"ticker": "ETH", "longNotional": 200000, "shortNotional": 100000},
                                ],
                            },
                        ],
                    }
                }
            }
        }

        adapter = HyperdashAdapter()
        points = adapter._parse_response(fixture)
        assert points == []

    def test_empty_response_returns_empty(self) -> None:
        """Empty GraphQL response returns empty list."""
        from adapters.hyperdash import HyperdashAdapter

        adapter = HyperdashAdapter()
        points = adapter._parse_response({"data": {"analytics": {"cohortSummary": {"sizeCohorts": []}}}})
        assert points == []

    def test_target_cohorts_constant(self) -> None:
        """Verify target cohort IDs are correct."""
        from adapters.hyperdash import TARGET_COHORTS
        assert "whale" in TARGET_COHORTS
        assert "large" in TARGET_COHORTS
        assert "$1M" in TARGET_COHORTS["whale"]
        assert "$100K" in TARGET_COHORTS["large"]
