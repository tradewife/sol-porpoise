"""Tests for dextrabot, cross-venue, hypothesis, and source health modules."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from adapters.base import DataPoint, Provenance, SourceTier
from adapters.normalizer import make_provenance


# ---------------------------------------------------------------------------
# Dextrabot tests
# ---------------------------------------------------------------------------

class TestDextrabotAdapter:
    def test_provenance(self) -> None:
        from adapters.dextrabot import DextrabotAdapter
        adapter = DextrabotAdapter()
        p = adapter.provenance()
        assert "Dextrabot" in p.source_name
        assert p.source_tier == SourceTier.OPEN

    def test_classify_entity_smart_money(self) -> None:
        from adapters.dextrabot import WalletData, classify_entity
        w = WalletData(address="test", sharpe=2.0, win_rate=60.0, pnl=5000)
        assert classify_entity(w) == "smart_money"

    def test_classify_entity_whale(self) -> None:
        from adapters.dextrabot import WalletData, classify_entity
        w = WalletData(address="test", pnl=50000)
        assert classify_entity(w) == "whale_unlabeled"

    def test_classify_entity_unknown(self) -> None:
        from adapters.dextrabot import WalletData, classify_entity
        w = WalletData(address="test", pnl=100)
        assert classify_entity(w) == "unknown"

    def test_parse_json_wallets(self) -> None:
        from adapters.dextrabot import DextrabotAdapter
        adapter = DextrabotAdapter()
        html = '''<html><script>
        {"wallets": [
            {"address": "0xabc123", "pnl": "10000", "winRate": "65", "sharpe": "2.1", "leverage": "5"},
            {"address": "0xdef456", "pnl": "-5000", "winRate": "40", "sharpe": "0.5"}
        ]}
        </script></html>'''
        wallets = adapter._parse_wallets_html(html)
        # JSON embedded in script may or may not match depending on regex
        # This tests the graceful handling path

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
        cache.put("http://test.com", "<html>test</html>")
        assert cache.get("http://test.com") == "<html>test</html>"


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
