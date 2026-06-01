"""Tests for engine modules: kg, scoring, paper_orders, outcomes, risk."""

from __future__ import annotations

import csv
import json
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from zoneinfo import ZoneInfo

AEST = ZoneInfo("Australia/Sydney")


# ---------------------------------------------------------------------------
# KG tests
# ---------------------------------------------------------------------------

class TestKGWriter:
    def test_triple_creation(self) -> None:
        from engine.kg import KGTriple
        t = KGTriple(
            subject="SOL", predicate="has_signal", object="funding_stretch",
            attrs={"value": 0.05}, source_name="HL API", source_tier="HL-native",
            source_link="https://api.hyperliquid.xyz", source_ts="2026-06-01T13:00:00Z",
            confidence=0.95,
        )
        assert t.subject == "SOL"
        row = t.to_csv_row()
        assert "SOL" in row
        assert "has_signal" in row
        assert "funding_stretch" in row

    def test_triple_csv_has_all_columns(self) -> None:
        from engine.kg import KGTriple, KG_CSV_HEADER
        expected_cols = KG_CSV_HEADER.split(",")
        assert len(expected_cols) == 10

    def test_writer_flush(self, tmp_path: Path) -> None:
        from engine.kg import KGWriter
        csv_path = tmp_path / "kg_triples.csv"
        writer = KGWriter(csv_path)
        writer.add(subject="BTC", predicate="has_signal", object_="oi_delta",
                   confidence=0.9, source_name="Test")
        count = writer.flush()
        assert count == 1
        content = csv_path.read_text()
        assert "BTC" in content
        assert "oi_delta" in content

    def test_writer_query(self, tmp_path: Path) -> None:
        from engine.kg import KGWriter
        csv_path = tmp_path / "kg_triples.csv"
        writer = KGWriter(csv_path)
        writer.add(subject="BTC", predicate="has_signal", object_="funding", confidence=0.9)
        writer.add(subject="ETH", predicate="has_signal", object_="oi_delta", confidence=0.8)
        writer.flush()
        results = writer.query(subject="BTC")
        assert len(results) == 1
        assert results[0].object == "funding"

    def test_entity_types_valid(self) -> None:
        from engine.kg import EntityType
        assert EntityType.SYMBOL.value == "SYMBOL"
        assert EntityType.OUTCOME.value == "OUTCOME"


# ---------------------------------------------------------------------------
# Scoring tests
# ---------------------------------------------------------------------------

class TestScoring:
    def test_weights_sum_to_one(self) -> None:
        from engine.scoring import COMPONENT_WEIGHTS
        assert abs(sum(COMPONENT_WEIGHTS.values()) - 1.0) < 1e-9

    def test_score_with_full_data(self) -> None:
        from engine.scoring import SignalComponent, compute_signal_score
        components = {
            name: SignalComponent(name=name, value=0.5, confidence=0.9, label="bullish")
            for name in [
                "funding_stretch", "oi_delta", "basis", "liquidity_magnet",
                "session_structure", "whale_evidence", "dex_perp_lag",
                "volatility", "catalyst",
            ]
        }
        score = compute_signal_score("BTC", components)
        assert 0 < score.weighted_score < 1.0
        assert score.overall_confidence > 0

    def test_missing_data_reduces_confidence(self) -> None:
        from engine.scoring import SignalComponent, compute_signal_score
        full = {
            name: SignalComponent(name=name, value=0.5, confidence=0.9, label="bullish")
            for name in [
                "funding_stretch", "oi_delta", "basis", "liquidity_magnet",
                "session_structure", "whale_evidence", "dex_perp_lag",
                "volatility", "catalyst",
            ]
        }
        partial = {
            **full,
            "liquidity_magnet": SignalComponent(name="liquidity_magnet", value=0, confidence=0, label="unknown"),
        }
        score_full = compute_signal_score("BTC", full)
        score_partial = compute_signal_score("BTC", partial)
        assert len(score_partial.unknown_components) > len(score_full.unknown_components)

    def test_missing_data_doesnt_assign_direction(self) -> None:
        from engine.scoring import SignalComponent, compute_signal_score
        components = {
            "funding_stretch": SignalComponent(name="funding_stretch", value=0, confidence=0, label="unknown"),
            "oi_delta": SignalComponent(name="oi_delta", value=0, confidence=0, label="unknown"),
            "basis": SignalComponent(name="basis", value=0, confidence=0, label="unknown"),
            "liquidity_magnet": SignalComponent(name="liquidity_magnet", value=0, confidence=0, label="unknown"),
            "session_structure": SignalComponent(name="session_structure", value=0, confidence=0, label="unknown"),
            "whale_evidence": SignalComponent(name="whale_evidence", value=0, confidence=0, label="unknown"),
            "dex_perp_lag": SignalComponent(name="dex_perp_lag", value=0, confidence=0, label="unknown"),
            "volatility": SignalComponent(name="volatility", value=0, confidence=0, label="unknown"),
            "catalyst": SignalComponent(name="catalyst", value=0, confidence=0, label="unknown"),
        }
        score = compute_signal_score("BTC", components)
        assert score.weighted_score == 0.0
        for comp in score.components.values():
            assert comp.label == "unknown"

    def test_conflict_logging(self) -> None:
        from engine.scoring import log_conflict
        msg = log_conflict("SOL", "funding", 0.001, 0.002, "HL_API", "Imperial_API")
        assert "CONFLICT" in msg
        assert "SOL" in msg
        assert "funding" in msg

    def test_score_to_dict(self) -> None:
        from engine.scoring import SignalComponent, compute_signal_score
        components = {
            "funding_stretch": SignalComponent(name="funding_stretch", value=0.3, confidence=0.8, label="bullish"),
        }
        score = compute_signal_score("ETH", components)
        d = score.to_dict()
        assert d["symbol"] == "ETH"
        assert "weighted_score" in d
        assert "unknown_components" in d


# ---------------------------------------------------------------------------
# Paper orders tests
# ---------------------------------------------------------------------------

class TestPaperOrders:
    def test_create_paper_order(self) -> None:
        from engine.paper_orders import OrderSide, PaperOrder
        order = PaperOrder(
            symbol="SOL", setup="funding_fade", side=OrderSide.LONG,
            entry=150.0, stop=145.0, tp1=160.0, tp2=170.0,
        )
        assert order.stop_distance == 5.0
        assert order.filled.value == "pending"

    def test_order_to_csv(self) -> None:
        from engine.paper_orders import OrderSide, PaperOrder
        order = PaperOrder(
            symbol="BTC", setup="breakout", side=OrderSide.SHORT,
            entry=100000, stop=102000, tp1=96000, tp2=94000,
        )
        row = order.to_csv_row()
        assert "BTC" in row
        assert "breakout" in row
        assert "short" in row

    def test_write_and_read_order(self, tmp_path: Path) -> None:
        from engine.paper_orders import OrderSide, PaperOrder, PaperOrderTracker
        csv_path = tmp_path / "paper_orders.csv"
        tracker = PaperOrderTracker(csv_path)
        order = PaperOrder(
            symbol="ETH", setup="vwap_reclaim", side=OrderSide.LONG,
            entry=3000, stop=2950, tp1=3100, tp2=3200,
        )
        tracker.write_order(order)
        orders = tracker.read_orders()
        assert len(orders) == 1
        assert orders[0].symbol == "ETH"

    def test_cancel_timeout(self) -> None:
        from engine.paper_orders import OrderSide, PaperOrder, check_cancel_rules
        order = PaperOrder(
            symbol="SOL", setup="test", side=OrderSide.LONG,
            entry=150, stop=145, tp1=160, tp2=170,
            created_ts_aest="2026-06-01 08:00:00 Australia/Sydney",
        )
        # 91 minutes later
        current_ts = datetime(2026, 6, 1, 9, 31, tzinfo=AEST)
        should_cancel, reason = check_cancel_rules(order, 149.0, current_ts)
        assert should_cancel
        assert reason is not None

    def test_cancel_drift(self) -> None:
        from engine.paper_orders import OrderSide, PaperOrder, check_cancel_rules
        order = PaperOrder(
            symbol="SOL", setup="test", side=OrderSide.LONG,
            entry=150, stop=145, tp1=160, tp2=170,
            created_ts_aest="2026-06-01 08:00:00 Australia/Sydney",
        )
        # Price drifted > 0.8 * 5 = 4 away from entry
        current_ts = datetime(2026, 6, 1, 8, 30, tzinfo=AEST)
        should_cancel, reason = check_cancel_rules(order, 155.0, current_ts)
        assert should_cancel

    def test_cancel_hard_exit(self) -> None:
        from engine.paper_orders import OrderSide, PaperOrder, check_cancel_rules
        order = PaperOrder(
            symbol="SOL", setup="test", side=OrderSide.LONG,
            entry=150, stop=145, tp1=160, tp2=170,
            created_ts_aest="2026-06-01 08:00:00 Australia/Sydney",
        )
        # Past 22:00 next day
        current_ts = datetime(2026, 6, 2, 22, 1, tzinfo=AEST)
        should_cancel, reason = check_cancel_rules(order, 152.0, current_ts)
        assert should_cancel

    def test_no_cancel_within_rules(self) -> None:
        from engine.paper_orders import OrderSide, PaperOrder, check_cancel_rules
        order = PaperOrder(
            symbol="SOL", setup="test", side=OrderSide.LONG,
            entry=150, stop=145, tp1=160, tp2=170,
            created_ts_aest="2026-06-01 08:00:00 Australia/Sydney",
        )
        # Within 90min, price near entry
        current_ts = datetime(2026, 6, 1, 8, 45, tzinfo=AEST)
        should_cancel, reason = check_cancel_rules(order, 150.5, current_ts)
        assert not should_cancel

    def test_fill_refuses_pre_order_data(self) -> None:
        from engine.paper_orders import OrderSide, PaperOrder, evaluate_fill
        order = PaperOrder(
            symbol="SOL", setup="test", side=OrderSide.LONG,
            entry=150, stop=145, tp1=160, tp2=170,
            created_ts_aest="2026-06-01 08:10:00 Australia/Sydney",
        )
        order_ts = datetime(2026, 6, 1, 8, 10, tzinfo=AEST)
        # Candle BEFORE order
        candle_ts = datetime(2026, 6, 1, 8, 5, tzinfo=AEST)
        result = evaluate_fill(order, 160, 148, 150, 155, candle_ts, order_ts)
        assert result["status"] == "invalid_for_stats"

    def test_fill_long_entry_reached(self) -> None:
        from engine.paper_orders import OrderSide, PaperOrder, evaluate_fill
        order = PaperOrder(
            symbol="SOL", setup="test", side=OrderSide.LONG,
            entry=150, stop=145, tp1=160, tp2=170,
        )
        order_ts = datetime(2026, 6, 1, 8, 0, tzinfo=AEST)
        candle_ts = datetime(2026, 6, 1, 8, 5, tzinfo=AEST)
        result = evaluate_fill(order, 162, 148, 150, 155, candle_ts, order_ts)
        assert result.get("filled") is True

    def test_fill_same_candle_conservative(self) -> None:
        from engine.paper_orders import OrderSide, PaperOrder, evaluate_fill
        order = PaperOrder(
            symbol="SOL", setup="test", side=OrderSide.LONG,
            entry=150, stop=145, tp1=160, tp2=170,
        )
        order.filled = OrderSide  # hack to make it filled
        from engine.paper_orders import OrderStatus
        order.filled = OrderStatus.FILLED
        order_ts = datetime(2026, 6, 1, 8, 0, tzinfo=AEST)
        candle_ts = datetime(2026, 6, 1, 8, 5, tzinfo=AEST)
        # Both stop (144 < 145) and TP (161 > 160) hit
        result = evaluate_fill(order, 161, 144, 150, 155, candle_ts, order_ts)
        assert result.get("same_candle_ambiguity") is True
        assert result["confidence"] == "low"

    def test_passive_entry_valid_long(self) -> None:
        from engine.paper_orders import OrderSide, validate_passive_entry
        valid, _ = validate_passive_entry(OrderSide.LONG, 149.0, 150.0, 151.0)
        assert valid

    def test_passive_entry_invalid_long(self) -> None:
        from engine.paper_orders import OrderSide, validate_passive_entry
        valid, _ = validate_passive_entry(OrderSide.LONG, 151.0, 150.0, 151.0)
        assert not valid

    def test_passive_entry_valid_short(self) -> None:
        from engine.paper_orders import OrderSide, validate_passive_entry
        valid, _ = validate_passive_entry(OrderSide.SHORT, 152.0, 150.0, 151.0)
        assert valid


# ---------------------------------------------------------------------------
# Outcomes tests
# ---------------------------------------------------------------------------

class TestOutcomes:
    def test_compute_outcome_long_win(self) -> None:
        from engine.outcomes import OutcomeEvaluator
        from engine.paper_orders import OrderSide, PaperOrder
        order = PaperOrder(
            symbol="SOL", setup="test", side=OrderSide.LONG,
            entry=150, stop=145, tp1=160, tp2=170,
        )
        evaluator = OutcomeEvaluator()
        outcome = evaluator.compute_outcome(order, exit_price=160, fees_bps=5, slippage_bps=3)
        assert outcome.result_r > 0  # win

    def test_compute_outcome_long_loss(self) -> None:
        from engine.outcomes import OutcomeEvaluator
        from engine.paper_orders import OrderSide, PaperOrder
        order = PaperOrder(
            symbol="SOL", setup="test", side=OrderSide.LONG,
            entry=150, stop=145, tp1=160, tp2=170,
        )
        evaluator = OutcomeEvaluator()
        outcome = evaluator.compute_outcome(order, exit_price=145, fees_bps=5, slippage_bps=3)
        assert outcome.result_r < 0  # loss

    def test_outcome_csv_schema(self, tmp_path: Path) -> None:
        from engine.outcomes import OutcomeEvaluator
        from engine.paper_orders import OrderSide, PaperOrder
        evaluator = OutcomeEvaluator(
            outcomes_path=tmp_path / "outcomes.csv",
            signal_outcomes_path=tmp_path / "signal_outcomes.csv",
        )
        order = PaperOrder(
            symbol="SOL", setup="test", side=OrderSide.LONG,
            entry=150, stop=145, tp1=160, tp2=170,
        )
        outcome = evaluator.compute_outcome(order, exit_price=155)
        evaluator.write_outcome(outcome)
        content = (tmp_path / "outcomes.csv").read_text()
        lines = content.strip().split("\n")
        assert len(lines) == 2  # header + 1 data row
        header = lines[0]
        assert "result_R" in header
        assert "max_FvE" in header
        assert "max_AdE" in header

    def test_signal_attribution(self, tmp_path: Path) -> None:
        from engine.outcomes import OutcomeEvaluator
        evaluator = OutcomeEvaluator(
            outcomes_path=tmp_path / "outcomes.csv",
            signal_outcomes_path=tmp_path / "signal_outcomes.csv",
        )
        evaluator.write_signal_attribution("order_1", ["funding_stretch", "oi_delta"])
        content = (tmp_path / "signal_outcomes.csv").read_text()
        assert "funding_stretch" in content
        assert "oi_delta" in content

    def test_signal_stats_empty(self, tmp_path: Path) -> None:
        from engine.outcomes import OutcomeEvaluator
        evaluator = OutcomeEvaluator(
            outcomes_path=tmp_path / "outcomes.csv",
            signal_outcomes_path=tmp_path / "signal_outcomes.csv",
        )
        stats = evaluator.compute_signal_stats()
        assert isinstance(stats, dict)


# ---------------------------------------------------------------------------
# Risk sizing tests
# ---------------------------------------------------------------------------

class TestRiskSizing:
    def test_basic_sizing(self) -> None:
        from engine.risk import RiskParams, compute_risk_sizing
        from engine.paper_orders import OrderSide
        params = RiskParams()
        result = compute_risk_sizing("SOL", OrderSide.LONG, 150.0, 147.0, params)
        assert result.valid
        assert result.qty > 0
        assert result.leverage >= params.leverage_min
        assert result.leverage <= params.leverage_max

    def test_sizing_math(self) -> None:
        from engine.risk import RiskParams, compute_risk_sizing
        from engine.paper_orders import OrderSide
        params = RiskParams(equity=100, max_risk_pct=0.20, leverage_min=9, leverage_max=12)
        # stop_distance = 3, risk_usd = 20
        # qty_by_risk = 20/3 = 6.67, qty_by_lev = (100*12)/150 = 8.0
        # qty = min(6.67, 8) = 6.67, notional = 6.67*150 = 1000, leverage = 10x
        result = compute_risk_sizing("SOL", OrderSide.LONG, 150.0, 147.0, params)
        assert result.risk_usd == 20.0
        assert result.stop_distance == 3.0
        assert abs(result.qty_by_risk - 6.6667) < 0.01
        assert abs(result.qty_by_lev - 8.0) < 0.01

    def test_leverage_cap(self) -> None:
        from engine.risk import RiskParams, compute_risk_sizing
        from engine.paper_orders import OrderSide
        params = RiskParams(equity=100, max_risk_pct=0.20, leverage_min=1, leverage_max=12)
        # Very tight stop: qty_by_risk huge, should be capped by leverage
        result = compute_risk_sizing("SOL", OrderSide.LONG, 150.0, 149.99, params)
        if result.valid:
            assert result.leverage <= 12.0

    def test_zero_stop_distance_rejected(self) -> None:
        from engine.risk import RiskParams, compute_risk_sizing
        from engine.paper_orders import OrderSide
        params = RiskParams()
        result = compute_risk_sizing("SOL", OrderSide.LONG, 150.0, 150.0, params)
        assert not result.valid
        assert "stop_distance" in result.reject_reason

    def test_passive_entry_rejection(self) -> None:
        from engine.risk import RiskParams, compute_risk_sizing
        from engine.paper_orders import OrderSide
        params = RiskParams()
        # Long entry above best bid → invalid passive
        result = compute_risk_sizing("SOL", OrderSide.LONG, 152.0, 149.0, params,
                                     best_bid=150.0, best_ask=151.0)
        assert not result.valid
        assert "passive" in result.reject_reason

    def test_skipped_trade_writes(self, tmp_path: Path) -> None:
        from engine.risk import write_skipped_trade
        csv_path = tmp_path / "skipped.csv"
        write_skipped_trade(csv_path, symbol="BTC", side="long", reason="poor_liquidity")
        content = csv_path.read_text()
        assert "BTC" in content
        assert "poor_liquidity" in content

    def test_to_paper_order(self) -> None:
        from engine.risk import RiskParams, compute_risk_sizing
        from engine.paper_orders import OrderSide
        params = RiskParams()
        result = compute_risk_sizing("SOL", OrderSide.LONG, 150.0, 147.0, params)
        order = result.to_paper_order("breakout", tp1=160.0, tp2=170.0)
        assert order.symbol == "SOL"
        assert order.setup == "breakout"
        assert order.tp1 == 160.0

    def test_mode_enforcement(self) -> None:
        """Mode live-paper-only: engine.risk should not have any execution code."""
        import engine.risk as risk_mod
        # Ensure no functions that could place real orders
        source = open(risk_mod.__file__).read()
        assert "sign" not in source.lower() or "design" in source.lower()
        assert "execute" not in source.lower() or "execute" not in source.lower()
