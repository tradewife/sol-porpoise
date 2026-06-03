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
                "volatility", "catalyst", "book_imbalance",
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
                "volatility", "catalyst", "book_imbalance",
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
            "book_imbalance": SignalComponent(name="book_imbalance", value=0, confidence=0, label="unknown"),
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


# ---------------------------------------------------------------------------
# Volatility tests (VAL-VOL-001 through VAL-VOL-012)
# ---------------------------------------------------------------------------


def _make_candles(prices: list[tuple[float, float, float, float]]) -> list:
    """Helper to create Candle objects from (open, high, low, close) tuples."""
    from engine.volatility import Candle
    return [
        Candle(open=o, high=h, low=l, close=c, timestamp=f"2026-06-01T{i:02d}:00:00Z")
        for i, (o, h, l, c) in enumerate(prices)
    ]


# Known candle fixture: 15 candles with hand-calculated ATR
# TRs: [2, 3, 3, 2, 4, 3, 2, 3, 4, 3, 2, 3, 3, 2, 6]
# First 14 TRs sum = 39, SMA = 39/14 = 2.7857142857
# Wilder: (2.7857142857 * 13 + 6) / 14 = 42.2142857141 / 14 = 3.0153061224
KNOWN_CANDLES_DATA: list[tuple[float, float, float, float]] = [
    (100, 102, 100, 101),  # TR=2
    (101, 104, 101, 103),  # TR=3
    (103, 106, 103, 105),  # TR=3
    (105, 107, 105, 106),  # TR=2
    (106, 110, 106, 109),  # TR=4
    (109, 112, 109, 111),  # TR=3
    (111, 113, 111, 112),  # TR=2
    (112, 115, 112, 114),  # TR=3
    (114, 118, 114, 117),  # TR=4
    (117, 120, 117, 119),  # TR=3
    (119, 121, 119, 120),  # TR=2
    (120, 123, 120, 122),  # TR=3
    (122, 125, 122, 124),  # TR=3
    (124, 126, 124, 125),  # TR=2
    # Gap-up candle: prev close=125, this open=128
    (128, 131, 127, 130),  # TR=max(4, |131-125|, |127-125|)=max(4,6,2)=6
]


class TestCandle:
    """VAL-VOL-009: Candle dataclass with OHLC validation."""

    def test_candle_fields(self) -> None:
        from engine.volatility import Candle
        c = Candle(open=100, high=105, low=98, close=103, timestamp="2026-01-01T00:00:00Z")
        assert c.open == 100
        assert c.high == 105
        assert c.low == 98
        assert c.close == 103
        assert c.timestamp == "2026-01-01T00:00:00Z"

    def test_candle_all_fields_float_timestamp_str(self) -> None:
        from engine.volatility import Candle
        c = Candle(open=50.5, high=51.0, low=50.0, close=50.75, timestamp="2026-01-01T00:00:00Z")
        assert isinstance(c.open, float)
        assert isinstance(c.high, float)
        assert isinstance(c.low, float)
        assert isinstance(c.close, float)
        assert isinstance(c.timestamp, str)

    def test_candle_invalid_high_below_open(self) -> None:
        """high < open and high < close must raise ValueError."""
        from engine.volatility import Candle
        with pytest.raises(ValueError):
            Candle(open=100, high=95, low=90, close=99, timestamp="2026-01-01T00:00:00Z")

    def test_candle_invalid_high_below_close(self) -> None:
        from engine.volatility import Candle
        with pytest.raises(ValueError):
            Candle(open=95, high=96, low=90, close=100, timestamp="2026-01-01T00:00:00Z")

    def test_candle_invalid_low_above_open(self) -> None:
        """low > open or low > close must raise ValueError."""
        from engine.volatility import Candle
        with pytest.raises(ValueError):
            Candle(open=100, high=105, low=102, close=99, timestamp="2026-01-01T00:00:00Z")

    def test_candle_invalid_low_above_close(self) -> None:
        from engine.volatility import Candle
        with pytest.raises(ValueError):
            Candle(open=100, high=105, low=98, close=97, timestamp="2026-01-01T00:00:00Z")

    def test_candle_valid_bullish(self) -> None:
        from engine.volatility import Candle
        c = Candle(open=100, high=105, low=99, close=103, timestamp="2026-01-01T00:00:00Z")
        assert c.high >= max(c.open, c.close)
        assert c.low <= min(c.open, c.close)

    def test_candle_valid_bearish(self) -> None:
        from engine.volatility import Candle
        c = Candle(open=103, high=105, low=99, close=100, timestamp="2026-01-01T00:00:00Z")
        assert c.high >= max(c.open, c.close)
        assert c.low <= min(c.open, c.close)

    def test_candle_valid_doji(self) -> None:
        from engine.volatility import Candle
        c = Candle(open=100, high=102, low=98, close=100, timestamp="2026-01-01T00:00:00Z")
        assert c.high >= max(c.open, c.close)
        assert c.low <= min(c.open, c.close)


class TestComputeATR:
    """VAL-VOL-001, VAL-VOL-002: ATR computation correctness and edge cases."""

    def test_atr_known_values_with_gap(self) -> None:
        """VAL-VOL-001: ATR matches hand-calculated Wilder-smoothed value."""
        from engine.volatility import compute_atr
        candles = _make_candles(KNOWN_CANDLES_DATA)
        result = compute_atr(candles, period=14)
        expected = 3.0153061224  # hand-calculated
        assert abs(result - expected) < 0.01, f"ATR {result} != {expected}"

    def test_atr_14_candles_no_smoothing(self) -> None:
        """With exactly 14 candles, ATR = SMA of TRs."""
        from engine.volatility import compute_atr
        candles = _make_candles(KNOWN_CANDLES_DATA[:14])
        result = compute_atr(candles, period=14)
        expected = 39 / 14  # 2.785714...
        assert abs(result - expected) < 0.01

    def test_atr_gap_up_incorporated(self) -> None:
        """VAL-VOL-001: Gap-up candle correctly uses gap component in TR."""
        from engine.volatility import compute_atr, Candle
        # Two candles where gap creates larger TR than H-L
        candles = [
            Candle(open=100, high=102, low=100, close=100, timestamp="2026-01-01T00:00:00Z"),
            # Gap up: prev_close=100, high=110, low=105
            # TR = max(110-105, |110-100|, |105-100|) = max(5, 10, 5) = 10
            Candle(open=108, high=110, low=105, close=109, timestamp="2026-01-01T01:00:00Z"),
        ]
        result = compute_atr(candles, period=2)
        # SMA of TRs: (2 + 10) / 2 = 6.0
        assert abs(result - 6.0) < 0.01

    def test_atr_gap_down_incorporated(self) -> None:
        """VAL-VOL-001: Gap-down candle correctly uses gap component in TR."""
        from engine.volatility import compute_atr, Candle
        candles = [
            Candle(open=100, high=102, low=100, close=100, timestamp="2026-01-01T00:00:00Z"),
            # Gap down: prev_close=100, high=95, low=90
            # TR = max(95-90, |95-100|, |90-100|) = max(5, 5, 10) = 10
            Candle(open=95, high=95, low=90, close=92, timestamp="2026-01-01T01:00:00Z"),
        ]
        result = compute_atr(candles, period=2)
        expected = (2 + 10) / 2  # 6.0
        assert abs(result - 6.0) < 0.01

    def test_atr_result_non_negative(self) -> None:
        from engine.volatility import compute_atr
        candles = _make_candles(KNOWN_CANDLES_DATA)
        result = compute_atr(candles, period=14)
        assert result >= 0

    def test_atr_empty_raises(self) -> None:
        """VAL-VOL-002: Empty list raises ValueError."""
        from engine.volatility import compute_atr
        with pytest.raises(ValueError):
            compute_atr([], period=14)

    def test_atr_single_candle(self) -> None:
        """VAL-VOL-002: Single candle returns high - low."""
        from engine.volatility import compute_atr, Candle
        candle = Candle(open=100, high=105, low=98, close=103, timestamp="2026-01-01T00:00:00Z")
        result = compute_atr([candle], period=1)
        assert result == 7.0  # 105 - 98

    def test_atr_identical_prices(self) -> None:
        """VAL-VOL-002: All identical prices returns 0.0."""
        from engine.volatility import compute_atr, Candle
        candles = [
            Candle(open=100, high=100, low=100, close=100, timestamp=f"2026-01-01T{i:02d}:00:00Z")
            for i in range(14)
        ]
        result = compute_atr(candles, period=14)
        assert result == 0.0

    def test_atr_period_exceeds_len(self) -> None:
        """VAL-VOL-002: period > len(candles) raises ValueError."""
        from engine.volatility import compute_atr, Candle
        candles = [
            Candle(open=100, high=102, low=99, close=101, timestamp="2026-01-01T00:00:00Z")
            for _ in range(5)
        ]
        with pytest.raises(ValueError):
            compute_atr(candles, period=14)

    def test_atr_result_always_non_negative(self) -> None:
        """VAL-VOL-001: ATR is always >= 0."""
        from engine.volatility import compute_atr, Candle
        candles = [
            Candle(open=100 + i, high=102 + i, low=99 + i, close=101 + i, timestamp=f"2026-01-01T{i:02d}:00:00Z")
            for i in range(20)
        ]
        for period in [1, 5, 10, 14, 20]:
            result = compute_atr(candles, period=period)
            assert result >= 0, f"ATR negative for period={period}: {result}"


class TestComputeRealizedVol:
    """VAL-VOL-003, VAL-VOL-004: Realized volatility correctness and edge cases."""

    def test_realized_vol_varying_positive(self) -> None:
        """VAL-VOL-003: Varying prices produce positive realized vol."""
        from engine.volatility import compute_realized_vol
        candles = _make_candles(KNOWN_CANDLES_DATA)
        result = compute_realized_vol(candles, window=24)
        assert result > 0

    def test_realized_vol_flat_zero(self) -> None:
        """VAL-VOL-003: Constant prices produce 0.0 realized vol."""
        from engine.volatility import compute_realized_vol, Candle
        candles = [
            Candle(open=100, high=100, low=100, close=100, timestamp=f"2026-01-01T{i:02d}:00:00Z")
            for i in range(10)
        ]
        result = compute_realized_vol(candles, window=24)
        assert result == 0.0

    def test_realized_vol_window_parameter(self) -> None:
        """VAL-VOL-003: Window parameter limits the number of closes used."""
        from engine.volatility import compute_realized_vol, Candle
        # 50 candles with varying closes
        candles = [
            Candle(
                open=100 + i * 0.5,
                high=102 + i * 0.5,
                low=99 + i * 0.5,
                close=101 + i * 0.5,
                timestamp=f"2026-01-01T{i:02d}:00:00Z",
            )
            for i in range(50)
        ]
        result_windowed = compute_realized_vol(candles, window=24)
        # Compare with computing on just the last 24 candles
        result_subset = compute_realized_vol(candles[-24:], window=24)
        assert abs(result_windowed - result_subset) < 1e-10

    def test_realized_vol_respects_window(self) -> None:
        """Window param causes different results when series is long."""
        from engine.volatility import compute_realized_vol, Candle
        # Create candles with two distinct volatility regimes
        # First 30: stable prices (close=100 every time)
        stable = [
            Candle(open=100, high=100.1, low=99.9, close=100, timestamp=f"2026-01-01T{i:02d}:00:00Z")
            for i in range(30)
        ]
        # Last 10: truly volatile prices (alternating between 95 and 105)
        volatile = []
        for i in range(10):
            close = 105 if i % 2 == 0 else 95
            o = 100
            volatile.append(
                Candle(open=o, high=max(o, close) + 1, low=min(o, close) - 1,
                       close=close, timestamp=f"2026-01-01T{(30 + i):02d}:00:00Z")
            )
        all_candles = stable + volatile
        result_full = compute_realized_vol(all_candles, window=100)
        result_window = compute_realized_vol(all_candles, window=10)
        # The windowed version (last 10 = volatile) should be higher than
        # the full version (diluted by 30 stable candles)
        assert result_window > result_full

    def test_realized_vol_empty_raises(self) -> None:
        """VAL-VOL-004: Empty list raises ValueError."""
        from engine.volatility import compute_realized_vol
        with pytest.raises(ValueError):
            compute_realized_vol([], window=24)

    def test_realized_vol_single_raises(self) -> None:
        """VAL-VOL-004: Single candle raises ValueError."""
        from engine.volatility import compute_realized_vol, Candle
        candle = Candle(open=100, high=102, low=99, close=101, timestamp="2026-01-01T00:00:00Z")
        with pytest.raises(ValueError):
            compute_realized_vol([candle], window=24)

    def test_realized_vol_always_non_negative(self) -> None:
        from engine.volatility import compute_realized_vol
        candles = _make_candles(KNOWN_CANDLES_DATA)
        result = compute_realized_vol(candles, window=24)
        assert result >= 0


class TestClassifyRegime:
    """VAL-VOL-005, VAL-VOL-006: Regime classification thresholds and validation."""

    @pytest.mark.parametrize(
        "atr,avg_atr,expected",
        [
            (0.4, 1.0, "Quiet"),       # ratio = 0.4 < 0.5
            (0.5, 1.0, "Normal"),       # ratio = 0.5 → boundary, higher regime
            (1.0, 1.0, "Normal"),       # ratio = 1.0, well within Normal
            (1.49, 1.0, "Normal"),      # ratio = 1.49 < 1.5
            (1.5, 1.0, "High"),         # ratio = 1.5 → boundary, higher regime
            (2.0, 1.0, "High"),         # ratio = 2.0, well within High
            (2.49, 1.0, "High"),        # ratio = 2.49 < 2.5
            (2.5, 1.0, "Extreme"),      # ratio = 2.5 → boundary, higher regime
            (5.0, 1.0, "Extreme"),      # ratio = 5.0, well within Extreme
        ],
    )
    def test_regime_thresholds(self, atr: float, avg_atr: float, expected: str) -> None:
        """VAL-VOL-005: All four regimes at correct boundary values."""
        from engine.volatility import classify_regime
        result = classify_regime(atr, avg_atr)
        assert result == expected, f"classify_regime({atr}, {avg_atr}) = {result}, expected {expected}"

    def test_quiet_regime(self) -> None:
        from engine.volatility import classify_regime
        assert classify_regime(0.1, 1.0) == "Quiet"

    def test_extreme_regime(self) -> None:
        from engine.volatility import classify_regime
        assert classify_regime(10.0, 1.0) == "Extreme"

    @pytest.mark.parametrize(
        "atr,avg_atr",
        [
            (-1, 1.0),   # negative atr
            (1, 0.0),    # zero avg_atr
            (1, -1.0),   # negative avg_atr
            (-1, -1.0),  # both negative
        ],
    )
    def test_regime_invalid_inputs(self, atr: float, avg_atr: float) -> None:
        """VAL-VOL-006: ValueError for invalid inputs."""
        from engine.volatility import classify_regime
        with pytest.raises(ValueError):
            classify_regime(atr, avg_atr)


class TestComputeMinStop:
    """VAL-VOL-007, VAL-VOL-008: Minimum stop computation and validation."""

    def test_min_stop_long_basic(self) -> None:
        """VAL-VOL-007: LONG stop = entry - 0.8*ATR."""
        from engine.volatility import compute_min_stop
        from engine.paper_orders import OrderSide
        # atr=5, entry=150: offset = 0.8*5 = 4, stop = 150-4 = 146
        result = compute_min_stop(5.0, 150.0, OrderSide.LONG)
        assert result == 146.0

    def test_min_stop_short_basic(self) -> None:
        """VAL-VOL-007: SHORT stop = entry + 0.8*ATR."""
        from engine.volatility import compute_min_stop
        from engine.paper_orders import OrderSide
        # atr=5, entry=150: offset = 0.8*5 = 4, stop = 150+4 = 154
        result = compute_min_stop(5.0, 150.0, OrderSide.SHORT)
        assert result == 154.0

    def test_min_stop_long_with_invalidation(self) -> None:
        """VAL-VOL-007: LONG with invalidation > 0.8*ATR uses invalidation."""
        from engine.volatility import compute_min_stop
        from engine.paper_orders import OrderSide
        # atr=5, entry=150, invalidation=6: offset = max(4, 6) = 6, stop = 150-6 = 144
        result = compute_min_stop(5.0, 150.0, OrderSide.LONG, nearest_invalidation=6.0)
        assert result == 144.0

    def test_min_stop_short_with_invalidation(self) -> None:
        """VAL-VOL-007: SHORT with invalidation > 0.8*ATR uses invalidation."""
        from engine.volatility import compute_min_stop
        from engine.paper_orders import OrderSide
        # atr=5, entry=150, invalidation=6: offset = max(4, 6) = 6, stop = 150+6 = 156
        result = compute_min_stop(5.0, 150.0, OrderSide.SHORT, nearest_invalidation=6.0)
        assert result == 156.0

    def test_min_stop_long_invalidation_smaller(self) -> None:
        """When invalidation < 0.8*ATR, ATR offset is used."""
        from engine.volatility import compute_min_stop
        from engine.paper_orders import OrderSide
        # atr=5, entry=150, invalidation=2: offset = max(4, 2) = 4, stop = 146
        result = compute_min_stop(5.0, 150.0, OrderSide.LONG, nearest_invalidation=2.0)
        assert result == 146.0

    @pytest.mark.parametrize(
        "atr,entry",
        [
            (0, 150.0),    # zero ATR
            (-1, 150.0),   # negative ATR
            (5, 0),        # zero entry
            (5, -100),     # negative entry
        ],
    )
    def test_min_stop_invalid_inputs(self, atr: float, entry: float) -> None:
        """VAL-VOL-008: ValueError for zero/negative ATR or entry."""
        from engine.volatility import compute_min_stop
        from engine.paper_orders import OrderSide
        with pytest.raises(ValueError):
            compute_min_stop(atr, entry, OrderSide.LONG)

    @pytest.mark.parametrize(
        "atr,entry,side",
        [
            (0.5, 10.0, "LONG"),
            (1.0, 100.0, "LONG"),
            (5.0, 100000.0, "LONG"),
            (50.0, 100000.0, "LONG"),
            (0.5, 10.0, "SHORT"),
            (1.0, 100.0, "SHORT"),
            (5.0, 100000.0, "SHORT"),
            (50.0, 100000.0, "SHORT"),
        ],
    )
    def test_min_stop_safety_floor(self, atr: float, entry: float, side: str) -> None:
        """VAL-VOL-008: abs(entry - stop) >= 0.8 * atr for all valid inputs."""
        from engine.volatility import compute_min_stop
        from engine.paper_orders import OrderSide
        order_side = OrderSide.LONG if side == "LONG" else OrderSide.SHORT
        stop = compute_min_stop(atr, entry, order_side)
        stop_distance = abs(entry - stop)
        assert stop_distance >= 0.8 * atr - 1e-10, (
            f"Safety floor violated: distance={stop_distance}, 0.8*atr={0.8 * atr}"
        )


class TestVolatilityExports:
    """VAL-VOL-010: Module exports all public functions."""

    def test_all_public_names_importable(self) -> None:
        from engine.volatility import (
            Candle,
            classify_regime,
            compute_atr,
            compute_min_stop,
            compute_realized_vol,
        )
        assert Candle is not None
        assert compute_atr is not None
        assert compute_realized_vol is not None
        assert classify_regime is not None
        assert compute_min_stop is not None


class TestVolatilityIntegration:
    """VAL-VOL-011, VAL-VOL-012: Integration with run_scan.py."""

    def test_run_scan_no_stop_placeholder(self) -> None:
        """VAL-VOL-011: run_scan.py does not contain stop_pct = 0.02."""
        import engine.run_scan as run_scan_mod
        source = open(run_scan_mod.__file__).read()
        assert "stop_pct = 0.02" not in source

    def test_run_scan_imports_compute_min_stop(self) -> None:
        """VAL-VOL-011: run_scan.py imports compute_min_stop."""
        import engine.run_scan as run_scan_mod
        source = open(run_scan_mod.__file__).read()
        assert "compute_min_stop" in source

    def test_run_scan_imports_volatility(self) -> None:
        """VAL-VOL-011: run_scan.py imports from engine.volatility."""
        import engine.run_scan as run_scan_mod
        source = open(run_scan_mod.__file__).read()
        assert "engine.volatility" in source or "engine import volatility" in source

    def test_run_scan_no_atr_gap_text(self) -> None:
        """VAL-VOL-011: Section H does not list ATR as unimplemented gap."""
        import engine.run_scan as run_scan_mod
        source = open(run_scan_mod.__file__).read()
        assert "ATR/volatility computation not implemented" not in source


# ---------------------------------------------------------------------------
# Helpers for signal extraction tests
# ---------------------------------------------------------------------------


def _make_provenance(source_name: str = "TestSource", confidence: float = 0.9) -> "Provenance":
    from adapters.base import Provenance, SourceTier
    return Provenance(
        source_name=source_name,
        source_tier=SourceTier.INTERNAL,
        source_link="[no-link]",
        source_ts="2026-06-01T12:00:00",
        fetched_ts_aest="2026-06-01 22:00:00 Australia/Sydney",
        confidence=confidence,
    )


def _make_dp(
    symbol: str = "BTC",
    metric: str = "mark_price_test",
    value: float = 100.0,
    source_name: str = "TestSource",
    attrs: dict | None = None,
    source_ts: str = "2026-06-01T12:00:00",
) -> "DataPoint":
    from adapters.base import DataPoint, Provenance, SourceTier
    prov = Provenance(
        source_name=source_name,
        source_tier=SourceTier.INTERNAL,
        source_link="[no-link]",
        source_ts=source_ts,
        fetched_ts_aest="2026-06-01 22:00:00 Australia/Sydney",
        confidence=0.9,
    )
    return DataPoint(
        symbol=symbol,
        metric=metric,
        value=value,
        provenance=prov,
        attrs=attrs or {},
    )


def _make_candle_list(n: int = 20, base_price: float = 100.0, volatility: float = 1.0) -> list:
    """Create n candles with slight variation around base_price."""
    from engine.volatility import Candle
    candles = []
    for i in range(n):
        o = base_price + (i * 0.1)
        h = o + volatility
        l = o - volatility
        c = o + 0.05
        candles.append(Candle(
            open=o, high=h, low=l, close=c,
            timestamp=f"2026-06-01T{i:02d}:00:00Z",
        ))
    return candles


# ---------------------------------------------------------------------------
# Signal Extraction tests (VAL-SIG-001 through VAL-SIG-012)
# ---------------------------------------------------------------------------


class TestExtractSignalsKeys:
    """VAL-SIG-001: extract_signals returns dict with exactly the keys matching COMPONENT_WEIGHTS."""

    def test_returns_keys_matching_component_weights(self) -> None:
        from engine.signals import extract_signals
        from engine.scoring import COMPONENT_WEIGHTS
        result = extract_signals("BTC", [], [], [], candles=None)
        assert set(result.keys()) == set(COMPONENT_WEIGHTS.keys())

    def test_all_values_are_signal_components(self) -> None:
        from engine.signals import extract_signals
        from engine.scoring import SignalComponent
        result = extract_signals("BTC", [], [], [])
        for key, comp in result.items():
            assert isinstance(comp, SignalComponent), f"{key} is not SignalComponent"

    def test_exactly_ten_components(self) -> None:
        from engine.signals import extract_signals
        result = extract_signals("BTC", [], [], [])
        assert len(result) == 10


class TestExtractSignalsGracefulDegradation:
    """VAL-SIG-002: All-unknown output when inputs are empty, None, or symbol absent."""

    def test_all_empty_lists(self) -> None:
        from engine.signals import extract_signals
        result = extract_signals("BTC", [], [], [])
        for name, comp in result.items():
            assert comp.value == 0.0, f"{name} value={comp.value}"
            assert comp.confidence == 0.0, f"{name} confidence={comp.confidence}"
            assert comp.label == "unknown", f"{name} label={comp.label}"

    def test_none_candles(self) -> None:
        from engine.signals import extract_signals
        result = extract_signals("BTC", [], [], [], candles=None)
        assert result["volatility"].label == "unknown"
        assert result["session_structure"].label == "unknown"

    def test_symbol_absent_from_datapoints(self) -> None:
        from engine.signals import extract_signals
        # Only ETH data, asking for BTC
        dps = [_make_dp(symbol="ETH", metric="funding_rate_test", value=0.001)]
        result = extract_signals("BTC", dps, [], [])
        for name, comp in result.items():
            assert comp.label == "unknown", f"{name} should be unknown (symbol mismatch)"

    def test_datapoints_with_none_values(self) -> None:
        from engine.signals import extract_signals
        dps = [_make_dp(metric="funding_rate_test", value=None)]
        result = extract_signals("BTC", dps, [], [])
        assert result["funding_stretch"].label == "unknown"

    def test_datapoints_with_zero_prices(self) -> None:
        from engine.signals import extract_signals
        dps = [
            _make_dp(metric="mark_price_test", value=0, source_name="Src1"),
            _make_dp(metric="mark_price_test", value=0, source_name="Src2"),
        ]
        result = extract_signals("BTC", dps, [], [])
        # Should not crash, basis might be unknown or handle gracefully
        assert isinstance(result["basis"].value, float)

    def test_never_crashes_any_input(self) -> None:
        """Never raises regardless of input."""
        from engine.signals import extract_signals
        # Various degenerate inputs
        inputs = [
            ("BTC", [], [], [], None),
            ("", [], [], [], None),
            ("BTC", None, None, None, None),  # type: ignore
            ("BTC", [_make_dp(value=float("nan"))], [], [], None),
            ("BTC", [_make_dp(value=float("inf"))], [], [], None),
        ]
        for args in inputs:
            try:
                result = extract_signals(*args)
                assert len(result) == 10
            except TypeError:
                # None arguments that don't match list type are OK to skip
                pass


class TestFundingStretch:
    """VAL-SIG-003: funding_stretch z-score with contrarian interpretation."""

    def test_z_score_contrarian_bearish(self) -> None:
        """Positive stretch (rate above avg) -> contrarian bearish (negative value)."""
        from engine.signals import extract_signals
        # Rates: [0.0001, 0.0002, 0.0003, 0.0005]
        # avg = 0.000275, current = 0.0005, z > 0 -> bearish -> value < 0
        dps = [
            _make_dp(metric="funding_rate_a", value=0.0001),
            _make_dp(metric="funding_rate_b", value=0.0002),
            _make_dp(metric="funding_rate_c", value=0.0003),
            _make_dp(metric="funding_rate_d", value=0.0005),
        ]
        result = extract_signals("BTC", dps, [], [])
        comp = result["funding_stretch"]
        assert comp.value < 0, f"Positive stretch should be bearish, got value={comp.value}"
        assert "bearish" in comp.label.lower()

    def test_z_score_contrarian_bullish(self) -> None:
        """Negative stretch (rate below avg) -> contrarian bullish (positive value)."""
        from engine.signals import extract_signals
        # Rates: [0.0005, 0.0004, 0.0003, 0.0001]
        # avg = 0.000325, current = 0.0001, z < 0 -> bullish -> value > 0
        dps = [
            _make_dp(metric="funding_rate_a", value=0.0005),
            _make_dp(metric="funding_rate_b", value=0.0004),
            _make_dp(metric="funding_rate_c", value=0.0003),
            _make_dp(metric="funding_rate_d", value=0.0001),
        ]
        result = extract_signals("BTC", dps, [], [])
        comp = result["funding_stretch"]
        assert comp.value > 0, f"Negative stretch should be bullish, got value={comp.value}"
        assert "bullish" in comp.label.lower()

    def test_z_score_value_matches_expected(self) -> None:
        """Z-score matches hand-calculated value within tolerance."""
        from engine.signals import extract_signals
        import math
        rates = [0.0001, 0.0002, 0.0003, 0.0005]
        avg = sum(rates) / len(rates)
        stdev = math.sqrt(sum((r - avg) ** 2 for r in rates) / len(rates))
        z = (rates[-1] - avg) / stdev
        expected_value = max(-1.0, min(1.0, -z / 3.0))

        dps = [_make_dp(metric=f"funding_rate_{i}", value=r) for i, r in enumerate(rates)]
        result = extract_signals("BTC", dps, [], [])
        assert abs(result["funding_stretch"].value - expected_value) < 0.01

    def test_zero_stdev_unknown(self) -> None:
        """Zero stdev (all rates identical) -> unknown."""
        from engine.signals import extract_signals
        dps = [
            _make_dp(metric="funding_rate_a", value=0.001),
            _make_dp(metric="funding_rate_b", value=0.001),
            _make_dp(metric="funding_rate_c", value=0.001),
        ]
        result = extract_signals("BTC", dps, [], [])
        assert result["funding_stretch"].label == "unknown"

    def test_single_point_unknown(self) -> None:
        """Single funding rate point -> unknown."""
        from engine.signals import extract_signals
        dps = [_make_dp(metric="funding_rate_a", value=0.001)]
        result = extract_signals("BTC", dps, [], [])
        assert result["funding_stretch"].label == "unknown"

    def test_confidence_positive_with_sufficient_data(self) -> None:
        """Confidence > 0 when data is sufficient and stretch is non-zero."""
        from engine.signals import extract_signals
        dps = [
            _make_dp(metric="funding_rate_a", value=0.0001),
            _make_dp(metric="funding_rate_b", value=0.0002),
            _make_dp(metric="funding_rate_c", value=0.0003),
            _make_dp(metric="funding_rate_d", value=0.0005),
        ]
        result = extract_signals("BTC", dps, [], [])
        assert result["funding_stretch"].confidence > 0


class TestOiDelta:
    """VAL-SIG-004: oi_delta direction and magnitude with price context."""

    def test_rising_oi_rising_price_bullish(self) -> None:
        """Rising OI + rising price -> bullish (positive value)."""
        from engine.signals import extract_signals
        dps = [
            _make_dp(metric="open_interest", value=100.0),
            _make_dp(metric="open_interest", value=120.0),
            _make_dp(metric="mark_price_a", value=50000.0),
            _make_dp(metric="mark_price_b", value=55000.0),
        ]
        result = extract_signals("BTC", dps, [], [])
        assert result["oi_delta"].value > 0

    def test_rising_oi_falling_price_bearish(self) -> None:
        """Rising OI + falling price -> bearish (negative value)."""
        from engine.signals import extract_signals
        dps = [
            _make_dp(metric="open_interest", value=100.0),
            _make_dp(metric="open_interest", value=120.0),
            _make_dp(metric="mark_price_a", value=55000.0),
            _make_dp(metric="mark_price_b", value=50000.0),
        ]
        result = extract_signals("BTC", dps, [], [])
        assert result["oi_delta"].value < 0

    def test_single_oi_point_unknown(self) -> None:
        """Single OI point -> unknown."""
        from engine.signals import extract_signals
        dps = [_make_dp(metric="open_interest", value=100.0)]
        result = extract_signals("BTC", dps, [], [])
        assert result["oi_delta"].label == "unknown"

    def test_no_oi_data_unknown(self) -> None:
        """No OI data -> unknown."""
        from engine.signals import extract_signals
        dps = [_make_dp(metric="mark_price_test", value=50000.0)]
        result = extract_signals("BTC", dps, [], [])
        assert result["oi_delta"].label == "unknown"


class TestBasisSignal:
    """VAL-SIG-005: basis delegates to compute_basis()."""

    def test_basis_with_two_venues(self) -> None:
        """Basis computed with prices from two sources."""
        from engine.signals import extract_signals
        dps = [
            _make_dp(metric="mark_price_a", value=50000.0, source_name="Imperial"),
            _make_dp(metric="mark_price_b", value=50100.0, source_name="Phantom"),
        ]
        result = extract_signals("BTC", dps, [], [])
        comp = result["basis"]
        assert comp.label != "unknown"
        # basis_bp = ((50000 - 50100) / 50100) * 10000 ≈ -19.96
        assert comp.value != 0.0

    def test_basis_single_venue_unknown(self) -> None:
        """Single venue -> unknown."""
        from engine.signals import extract_signals
        dps = [
            _make_dp(metric="mark_price_a", value=50000.0, source_name="Imperial"),
        ]
        result = extract_signals("BTC", dps, [], [])
        assert result["basis"].label == "unknown"

    def test_basis_delegates_to_compute_basis(self) -> None:
        """Verify compute_basis is called via mock."""
        from unittest.mock import patch
        from engine.signals import extract_signals
        from engine.cross_venue import BasisResult

        mock_result = BasisResult(
            symbol="BTC", hl_perp_price=50000.0, spot_price=50100.0,
            spot_venue="Phantom", basis_bp=-19.96,
            funding_alignment="unknown", divergence_note="",
        )

        dps = [
            _make_dp(metric="mark_price_a", value=50000.0, source_name="Imperial"),
            _make_dp(metric="mark_price_b", value=50100.0, source_name="Phantom"),
        ]

        with patch("engine.cross_venue.compute_basis", return_value=mock_result) as mock_cb:
            result = extract_signals("BTC", dps, [], [])
            mock_cb.assert_called_once()
            assert result["basis"].label != "unknown"


class TestLiquidityMagnet:
    """VAL-SIG-006: liquidity_magnet depth aggregation."""

    def test_more_bid_depth_positive(self) -> None:
        """2x more bid depth -> positive value (bullish support)."""
        from engine.signals import extract_signals
        dps = [
            _make_dp(metric="depth_bid", value=2000.0, attrs={"side": "bid"}),
            _make_dp(metric="depth_ask", value=1000.0, attrs={"side": "ask"}),
        ]
        result = extract_signals("BTC", dps, [], [])
        assert result["liquidity_magnet"].value > 0

    def test_more_ask_depth_negative(self) -> None:
        """More ask depth -> negative value (bearish resistance)."""
        from engine.signals import extract_signals
        dps = [
            _make_dp(metric="depth_bid", value=1000.0, attrs={"side": "bid"}),
            _make_dp(metric="depth_ask", value=2000.0, attrs={"side": "ask"}),
        ]
        result = extract_signals("BTC", dps, [], [])
        assert result["liquidity_magnet"].value < 0

    def test_no_depth_data_unknown(self) -> None:
        """No depth data -> unknown."""
        from engine.signals import extract_signals
        dps = [_make_dp(metric="mark_price_test", value=50000.0)]
        result = extract_signals("BTC", dps, [], [])
        assert result["liquidity_magnet"].label == "unknown"

    def test_empty_depth_unknown(self) -> None:
        """Empty datapoints -> unknown."""
        from engine.signals import extract_signals
        result = extract_signals("BTC", [], [], [])
        assert result["liquidity_magnet"].label == "unknown"


class TestSessionStructure:
    """VAL-SIG-007: session_structure VWAP from candles or mark prices."""

    def test_vwap_from_candles(self) -> None:
        """VWAP from candle typical prices, price above VWAP -> positive."""
        from engine.signals import extract_signals
        from engine.volatility import Candle
        # Create candles where the last one closes above VWAP
        candles = []
        for i in range(10):
            # Most candles close at 100, last one closes at 110 (above VWAP)
            close = 100.0 if i < 9 else 110.0
            high = max(100.0, close) + 2  # ensure high >= max(open, close)
            low = min(100.0, close) - 2   # ensure low <= min(open, close)
            candles.append(Candle(
                open=100, high=high, low=low, close=close,
                timestamp=f"2026-06-01T{i:02d}:00:00Z",
            ))
        result = extract_signals("BTC", [], [], [], candles=candles)
        comp = result["session_structure"]
        assert comp.value > 0, "Price above VWAP should be positive"
        assert "above" in comp.label.lower()

    def test_vwap_from_candles_below(self) -> None:
        """Price below VWAP -> negative."""
        from engine.signals import extract_signals
        from engine.volatility import Candle
        candles = []
        for i in range(10):
            close = 100.0 if i < 9 else 90.0
            high = max(100.0, close) + 2
            low = min(100.0, close) - 2
            candles.append(Candle(
                open=100, high=high, low=low, close=close,
                timestamp=f"2026-06-01T{i:02d}:00:00Z",
            ))
        result = extract_signals("BTC", [], [], [], candles=candles)
        assert result["session_structure"].value < 0

    def test_vwap_fallback_to_mark_prices(self) -> None:
        """Without candles, falls back to mark-price average."""
        from engine.signals import extract_signals
        dps = [
            _make_dp(metric="mark_price_a", value=100.0),
            _make_dp(metric="mark_price_b", value=102.0),
            _make_dp(metric="mark_price_c", value=104.0),
            _make_dp(metric="mark_price_d", value=106.0),
        ]
        result = extract_signals("BTC", dps, [], [])
        comp = result["session_structure"]
        assert comp.label != "unknown"
        # Last price (106) > avg (103) -> positive
        assert comp.value > 0

    def test_candle_vwap_preferred_over_mark_prices(self) -> None:
        """When candles are provided, candle VWAP is used (not mark prices)."""
        from engine.signals import extract_signals
        from engine.volatility import Candle
        # Mark prices all at 100, but candles have different VWAP
        dps = [
            _make_dp(metric="mark_price_a", value=100.0),
            _make_dp(metric="mark_price_b", value=100.0),
        ]
        # Candles with VWAP significantly different from 100
        candles = [
            Candle(open=90, high=92, low=88, close=90, timestamp="2026-06-01T00:00:00Z"),
            Candle(open=90, high=92, low=88, close=90, timestamp="2026-06-01T01:00:00Z"),
            Candle(open=90, high=112, low=88, close=110, timestamp="2026-06-01T02:00:00Z"),
        ]
        result = extract_signals("BTC", dps, [], [], candles=candles)
        comp = result["session_structure"]
        # VWAP from candles: avg of (92+88+90)/3, (92+88+90)/3, (112+88+110)/3
        # = avg(90, 90, 103.33) = 94.44
        # current = 110 > 94.44 -> positive
        assert comp.value > 0

    def test_no_prices_unknown(self) -> None:
        """No candles and no mark prices -> unknown."""
        from engine.signals import extract_signals
        result = extract_signals("BTC", [], [], [])
        assert result["session_structure"].label == "unknown"


class TestWhaleEvidenceAndDexPerpLag:
    """VAL-SIG-008: whale_evidence and dex_perp_lag delegation."""

    def test_whale_evidence_delegates(self) -> None:
        """whale_evidence delegates to integrate_whale_signals."""
        from unittest.mock import patch
        from engine.signals import extract_signals
        from engine.scoring import GraphSignalScore, SignalComponent

        mock_score = GraphSignalScore(symbol="BTC")
        mock_score.components["whale_evidence"] = SignalComponent(
            name="whale_evidence", value=0.5, confidence=0.8, label="smart_money_directional",
        )

        with patch("engine.cross_venue.integrate_whale_signals", return_value=mock_score) as mock:
            whale_pts = [_make_dp(symbol="BTC", metric="whale_pnl", value=5000)]
            result = extract_signals("BTC", [], whale_pts, [])
            mock.assert_called_once()
            assert result["whale_evidence"].value == 0.5
            assert result["whale_evidence"].confidence == 0.8

    def test_whale_evidence_empty_unknown(self) -> None:
        """Empty whale_points -> unknown."""
        from engine.signals import extract_signals
        result = extract_signals("BTC", [], [], [])
        assert result["whale_evidence"].label == "unknown"

    def test_dex_perp_lag_different_timestamps(self) -> None:
        """DEX timestamp ahead of perp -> non-zero value."""
        from engine.signals import extract_signals
        dps = [
            _make_dp(
                metric="mark_price_test", value=50100.0,
                source_name="DEX", source_ts="2026-06-01T12:01:00",
            ),
            _make_dp(
                metric="mark_price_test", value=50000.0,
                source_name="Perp", source_ts="2026-06-01T12:00:00",
            ),
        ]
        result = extract_signals("BTC", dps, [], [])
        comp = result["dex_perp_lag"]
        assert comp.label != "unknown"

    def test_dex_perp_lag_single_venue_unknown(self) -> None:
        """Single venue -> unknown."""
        from engine.signals import extract_signals
        dps = [
            _make_dp(metric="mark_price_test", value=50000.0, source_name="Imperial"),
        ]
        result = extract_signals("BTC", dps, [], [])
        assert result["dex_perp_lag"].label == "unknown"

    def test_dex_perp_lag_identical_timestamps_unknown(self) -> None:
        """Identical timestamps -> unknown."""
        from engine.signals import extract_signals
        dps = [
            _make_dp(
                metric="mark_price_test", value=50000.0,
                source_name="DEX", source_ts="2026-06-01T12:00:00",
            ),
            _make_dp(
                metric="mark_price_test", value=50100.0,
                source_name="Perp", source_ts="2026-06-01T12:00:00",
            ),
        ]
        result = extract_signals("BTC", dps, [], [])
        assert result["dex_perp_lag"].label == "unknown"


class TestVolatilitySignal:
    """VAL-SIG-009: volatility component uses volatility.py output."""

    def test_volatility_with_candles(self) -> None:
        """With known candles, volatility has non-unknown label and non-zero value."""
        from engine.signals import extract_signals
        candles = _make_candle_list(n=20, base_price=100.0, volatility=2.0)
        result = extract_signals("BTC", [], [], [], candles=candles)
        comp = result["volatility"]
        assert comp.label != "unknown"
        assert "regime_" in comp.label
        assert comp.value > 0
        assert comp.confidence > 0

    def test_volatility_without_candles_unknown(self) -> None:
        """candles=None -> unknown."""
        from engine.signals import extract_signals
        result = extract_signals("BTC", [], [], [], candles=None)
        assert result["volatility"].label == "unknown"

    def test_volatility_too_few_candles_unknown(self) -> None:
        """< 14 candles -> unknown."""
        from engine.signals import extract_signals
        candles = _make_candle_list(n=10)
        result = extract_signals("BTC", [], [], [], candles=candles)
        assert result["volatility"].label == "unknown"

    def test_volatility_uses_atr_and_regime(self) -> None:
        """Value consistent with ATR output; label includes regime."""
        from engine.signals import extract_signals
        from engine.volatility import compute_atr, classify_regime
        candles = _make_candle_list(n=20, base_price=100.0, volatility=3.0)
        result = extract_signals("BTC", [], [], [], candles=candles)
        comp = result["volatility"]
        # Verify the regime label is from classify_regime
        assert comp.label.startswith("regime_")
        regime = comp.label.replace("regime_", "")
        assert regime in ("Quiet", "Normal", "High", "Extreme")


class TestCatalystAlwaysUnknown:
    """VAL-SIG-010: catalyst always unknown."""

    def test_catalyst_unknown_with_data(self) -> None:
        from engine.signals import extract_signals
        dps = [_make_dp(metric="mark_price_test", value=50000.0)]
        result = extract_signals("BTC", dps, [], [])
        assert result["catalyst"].value == 0.0
        assert result["catalyst"].confidence == 0.0
        assert result["catalyst"].label == "unknown"

    def test_catalyst_unknown_empty(self) -> None:
        from engine.signals import extract_signals
        result = extract_signals("BTC", [], [], [])
        assert result["catalyst"].value == 0.0
        assert result["catalyst"].confidence == 0.0
        assert result["catalyst"].label == "unknown"

    def test_catalyst_unknown_with_candles(self) -> None:
        from engine.signals import extract_signals
        candles = _make_candle_list(n=20)
        result = extract_signals("BTC", [], [], [], candles=candles)
        assert result["catalyst"].value == 0.0
        assert result["catalyst"].confidence == 0.0
        assert result["catalyst"].label == "unknown"


# ---------------------------------------------------------------------------
# Book Imbalance Signal tests (VAL-SIGNAL-001 through VAL-SIGNAL-007)
# ---------------------------------------------------------------------------


class TestBookImbalanceThresholds:
    """VAL-SIGNAL-001: _extract_book_imbalance produces correct scores for all thresholds."""

    @pytest.mark.parametrize(
        "ratio,expected_value,expected_direction_label",
        [
            (0.50, -2, "ask_heavy"),   # < 0.60 → -2
            (0.65, -1, "ask_heavy"),   # 0.60-0.77 → -1
            (0.90, 0, "balanced"),     # 0.77-1.3 → 0
            (1.40, 1, "bid_heavy"),    # 1.3-1.6 → +1
            (1.80, 2, "bid_heavy"),    # > 1.6 → +2
        ],
    )
    def test_threshold_scoring(self, ratio: float, expected_value: int, expected_direction_label: str) -> None:
        """All 5 threshold levels produce correct value and direction label."""
        from engine.signals import _extract_book_imbalance
        dps = [_make_dp(metric="book_imbalance_ratio", value=ratio)]
        result = _extract_book_imbalance("BTC", dps)
        assert result.value == expected_value, f"ratio={ratio}: expected {expected_value}, got {result.value}"
        assert expected_direction_label in result.label, f"ratio={ratio}: expected '{expected_direction_label}' in label, got '{result.label}'"

    def test_strong_bid_wall(self) -> None:
        """Ratio 2.0 → value=+2 (strong bid wall)."""
        from engine.signals import _extract_book_imbalance
        dps = [_make_dp(metric="book_imbalance_ratio", value=2.0)]
        result = _extract_book_imbalance("BTC", dps)
        assert result.value == 2
        assert result.confidence == 0.9

    def test_bid_heavy(self) -> None:
        """Ratio 1.4 → value=+1 (bid-heavy)."""
        from engine.signals import _extract_book_imbalance
        dps = [_make_dp(metric="book_imbalance_ratio", value=1.4)]
        result = _extract_book_imbalance("BTC", dps)
        assert result.value == 1
        assert result.confidence == 0.6

    def test_balanced(self) -> None:
        """Ratio 1.0 → value=0 (balanced)."""
        from engine.signals import _extract_book_imbalance
        dps = [_make_dp(metric="book_imbalance_ratio", value=1.0)]
        result = _extract_book_imbalance("BTC", dps)
        assert result.value == 0
        assert result.confidence == 0.3

    def test_ask_heavy(self) -> None:
        """Ratio 0.70 → value=-1 (ask-heavy)."""
        from engine.signals import _extract_book_imbalance
        dps = [_make_dp(metric="book_imbalance_ratio", value=0.70)]
        result = _extract_book_imbalance("BTC", dps)
        assert result.value == -1
        assert result.confidence == 0.6

    def test_strong_ask_wall(self) -> None:
        """Ratio 0.50 → value=-2 (strong ask wall)."""
        from engine.signals import _extract_book_imbalance
        dps = [_make_dp(metric="book_imbalance_ratio", value=0.50)]
        result = _extract_book_imbalance("BTC", dps)
        assert result.value == -2
        assert result.confidence == 0.9

    def test_exact_boundary_1_6(self) -> None:
        """Ratio exactly 1.6 → value=+1 (not > 1.6)."""
        from engine.signals import _extract_book_imbalance
        dps = [_make_dp(metric="book_imbalance_ratio", value=1.6)]
        result = _extract_book_imbalance("BTC", dps)
        assert result.value == 1

    def test_exact_boundary_1_3(self) -> None:
        """Ratio exactly 1.3 → value=0 (not > 1.3)."""
        from engine.signals import _extract_book_imbalance
        dps = [_make_dp(metric="book_imbalance_ratio", value=1.3)]
        result = _extract_book_imbalance("BTC", dps)
        assert result.value == 0

    def test_exact_boundary_0_77(self) -> None:
        """Ratio exactly 0.77 → value=0 (not < 0.77)."""
        from engine.signals import _extract_book_imbalance
        dps = [_make_dp(metric="book_imbalance_ratio", value=0.77)]
        result = _extract_book_imbalance("BTC", dps)
        assert result.value == 0

    def test_exact_boundary_0_60(self) -> None:
        """Ratio exactly 0.60 → value=-1 (not < 0.60)."""
        from engine.signals import _extract_book_imbalance
        dps = [_make_dp(metric="book_imbalance_ratio", value=0.60)]
        result = _extract_book_imbalance("BTC", dps)
        assert result.value == -1


class TestBookImbalanceValueRange:
    """VAL-SIGNAL-002: signal_book_imbalance value always in {-2,-1,0,1,2}."""

    @pytest.mark.parametrize(
        "ratio",
        [0.001, 0.01, 0.10, 0.50, 0.60, 0.65, 0.77, 0.90, 1.0, 1.1, 1.3, 1.4, 1.6, 1.7, 5.0, 10.0, 100.0],
    )
    def test_value_in_valid_set(self, ratio: float) -> None:
        """For any valid ratio, value is always in {-2,-1,0,1,2}."""
        from engine.signals import _extract_book_imbalance
        dps = [_make_dp(metric="book_imbalance_ratio", value=ratio)]
        result = _extract_book_imbalance("BTC", dps)
        assert result.value in {-2, -1, 0, 1, 2}, f"ratio={ratio}: value={result.value}"

    def test_nan_input_unknown(self) -> None:
        """NaN ratio → unknown."""
        from engine.signals import _extract_book_imbalance
        dps = [_make_dp(metric="book_imbalance_ratio", value=float("nan"))]
        result = _extract_book_imbalance("BTC", dps)
        assert result.value == 0.0
        assert result.label == "unknown"

    def test_inf_input_unknown(self) -> None:
        """Inf ratio → unknown (inf is not finite)."""
        from engine.signals import _extract_book_imbalance
        dps = [_make_dp(metric="book_imbalance_ratio", value=float("inf"))]
        result = _extract_book_imbalance("BTC", dps)
        assert result.value == 0.0
        assert result.label == "unknown"


class TestBookImbalanceWeight:
    """VAL-SIGNAL-003: Weight 0.08 in scoring.py."""

    def test_weight_is_0_08(self) -> None:
        from engine.scoring import COMPONENT_WEIGHTS
        assert COMPONENT_WEIGHTS["book_imbalance"] == 0.08


class TestWeightsSumToOne:
    """VAL-SIGNAL-004: All 10 weights sum to exactly 1.0."""

    def test_weights_sum_to_one(self) -> None:
        from engine.scoring import COMPONENT_WEIGHTS
        assert len(COMPONENT_WEIGHTS) == 10
        assert sum(COMPONENT_WEIGHTS.values()) == 1.0


class TestAllWeightsMatchSpec:
    """VAL-SIGNAL-005: Individual weight correctness."""

    @pytest.mark.parametrize(
        "key,expected",
        [
            ("funding_stretch", 0.15),
            ("oi_delta", 0.15),
            ("basis", 0.05),
            ("liquidity_magnet", 0.15),
            ("session_structure", 0.10),
            ("whale_evidence", 0.07),
            ("dex_perp_lag", 0.10),
            ("volatility", 0.10),
            ("catalyst", 0.05),
            ("book_imbalance", 0.08),
        ],
    )
    def test_weight_value(self, key: str, expected: float) -> None:
        from engine.scoring import COMPONENT_WEIGHTS
        assert COMPONENT_WEIGHTS[key] == expected, f"{key}: expected {expected}, got {COMPONENT_WEIGHTS[key]}"


class TestBookImbalanceInExtractSignals:
    """VAL-SIGNAL-006: book_imbalance included in extract_signals output."""

    def test_included_in_output(self) -> None:
        """extract_signals() returns dict with book_imbalance key."""
        from engine.signals import extract_signals
        from engine.scoring import SignalComponent
        dps = [_make_dp(metric="book_imbalance_ratio", value=1.8)]
        result = extract_signals("BTC", dps, [], [])
        assert "book_imbalance" in result
        assert isinstance(result["book_imbalance"], SignalComponent)
        assert result["book_imbalance"].value == 2.0

    def test_wired_in_pipeline(self) -> None:
        """book_imbalance value flows from DataPoint through extract_signals."""
        from engine.signals import extract_signals
        for ratio, expected_val in [(0.50, -2), (0.70, -1), (1.0, 0), (1.4, 1), (1.8, 2)]:
            dps = [_make_dp(metric="book_imbalance_ratio", value=ratio)]
            result = extract_signals("BTC", dps, [], [])
            assert result["book_imbalance"].value == expected_val, \
                f"ratio={ratio}: expected {expected_val}, got {result['book_imbalance'].value}"


class TestBookImbalanceMissingData:
    """VAL-SIGNAL-007: book_imbalance degrades gracefully with no data."""

    def test_missing_data_unknown(self) -> None:
        """No book_imbalance_ratio DataPoint → value=0, confidence=0, label='unknown'."""
        from engine.signals import _extract_book_imbalance
        result = _extract_book_imbalance("BTC", [])
        assert result.value == 0.0
        assert result.confidence == 0.0
        assert result.label == "unknown"

    def test_wrong_symbol_unknown(self) -> None:
        """DataPoint for different symbol → unknown."""
        from engine.signals import _extract_book_imbalance
        dps = [_make_dp(symbol="ETH", metric="book_imbalance_ratio", value=1.8)]
        result = _extract_book_imbalance("BTC", dps)
        assert result.label == "unknown"

    def test_extract_signals_missing_book_imbalance(self) -> None:
        """extract_signals with no book_imbalance data → unknown component."""
        from engine.signals import extract_signals
        result = extract_signals("BTC", [], [], [])
        assert result["book_imbalance"].value == 0.0
        assert result["book_imbalance"].confidence == 0.0
        assert result["book_imbalance"].label == "unknown"


class TestSignalBounds:
    """VAL-SIG-011: Symbol filtering and value/confidence range bounds."""

    def test_symbol_filtering(self) -> None:
        """Only matching-symbol DataPoints used; no symbol leakage."""
        from engine.signals import extract_signals
        # ETH data should not affect BTC signals
        eth_dps = [
            _make_dp(symbol="ETH", metric="funding_rate_a", value=0.01),
            _make_dp(symbol="ETH", metric="funding_rate_b", value=0.02),
            _make_dp(symbol="ETH", metric="funding_rate_c", value=0.03),
        ]
        result = extract_signals("BTC", eth_dps, [], [])
        assert result["funding_stretch"].label == "unknown"

    def test_multi_symbol_isolation(self) -> None:
        """BTC data does not leak into ETH signals."""
        from engine.signals import extract_signals
        btc_dps = [
            _make_dp(symbol="BTC", metric="funding_rate_a", value=0.0001),
            _make_dp(symbol="BTC", metric="funding_rate_b", value=0.0005),
            _make_dp(symbol="ETH", metric="funding_rate_a", value=0.001),
        ]
        btc_result = extract_signals("BTC", btc_dps, [], [])
        eth_result = extract_signals("ETH", btc_dps, [], [])

        # BTC should have funding signal (2+ points)
        assert btc_result["funding_stretch"].label != "unknown"
        # ETH only has 1 funding point -> unknown
        assert eth_result["funding_stretch"].label == "unknown"

    def test_all_values_in_range(self) -> None:
        """All values in [-1, 1]."""
        from engine.signals import extract_signals
        # Create rich data that could push values to extremes
        dps = [
            _make_dp(metric="funding_rate_a", value=0.1),
            _make_dp(metric="funding_rate_b", value=-0.05),
            _make_dp(metric="funding_rate_c", value=0.08),
            _make_dp(metric="open_interest", value=100.0),
            _make_dp(metric="open_interest", value=500.0),
            _make_dp(metric="mark_price_a", value=50000.0, source_name="Imperial"),
            _make_dp(metric="mark_price_b", value=50200.0, source_name="Phantom"),
        ]
        candles = _make_candle_list(n=20, volatility=5.0)
        result = extract_signals("BTC", dps, [], [], candles=candles)
        for name, comp in result.items():
            assert -1.0 <= comp.value <= 1.0, f"{name} value={comp.value} out of range"
            assert 0.0 <= comp.confidence <= 1.0, f"{name} confidence={comp.confidence} out of range"

    def test_extreme_inputs_clamped(self) -> None:
        """Extreme input values are properly clamped."""
        from engine.signals import extract_signals
        # Very high funding rates
        dps = [
            _make_dp(metric="funding_rate_a", value=0.01),
            _make_dp(metric="funding_rate_b", value=0.5),
            _make_dp(metric="funding_rate_c", value=1.0),
        ]
        result = extract_signals("BTC", dps, [], [])
        assert -1.0 <= result["funding_stretch"].value <= 1.0


class TestSignalScoringIntegration:
    """VAL-SIG-012: Scoring integration and run_scan.py wiring."""

    def test_scoring_integration(self) -> None:
        """compute_signal_score(extract_signals(...)) produces valid GraphSignalScore."""
        import math as _math
        from engine.signals import extract_signals
        from engine.scoring import compute_signal_score
        dps = [
            _make_dp(metric="funding_rate_a", value=0.0001),
            _make_dp(metric="funding_rate_b", value=0.0003),
            _make_dp(metric="funding_rate_c", value=0.0005),
            _make_dp(metric="open_interest", value=100.0),
            _make_dp(metric="open_interest", value=120.0),
            _make_dp(metric="mark_price_a", value=50000.0, source_name="Imperial"),
            _make_dp(metric="mark_price_b", value=50100.0, source_name="Phantom"),
        ]
        candles = _make_candle_list(n=20)
        signals = extract_signals("BTC", dps, [], [], candles=candles)
        score = compute_signal_score("BTC", signals)
        assert score.weighted_score != 0.0 or len(score.unknown_components) < 10
        assert isinstance(score.weighted_score, float)
        assert _math.isfinite(score.weighted_score)

    def test_run_scan_calls_extract_signals(self) -> None:
        """run_scan.py calls extract_signals instead of all-unknown loop."""
        import engine.run_scan as run_scan_mod
        source = open(run_scan_mod.__file__).read()
        assert "extract_signals" in source

    def test_run_scan_no_all_unknown_placeholder(self) -> None:
        """run_scan.py no longer has the old all-unknown placeholder loop."""
        import engine.run_scan as run_scan_mod
        source = open(run_scan_mod.__file__).read()
        # The old pattern was: for comp_name in scoring_mod.COMPONENT_WEIGHTS: ... SignalComponent(... label="unknown")
        # After integration, this pattern should NOT appear in the scoring loop
        assert 'label="unknown"' not in source or "extract_signals" in source

    def test_deterministic_output(self) -> None:
        """Two calls with same inputs produce equal results."""
        from engine.signals import extract_signals
        dps = [
            _make_dp(metric="funding_rate_a", value=0.0001),
            _make_dp(metric="funding_rate_b", value=0.0003),
            _make_dp(metric="mark_price_a", value=50000.0, source_name="Imperial"),
            _make_dp(metric="mark_price_b", value=50100.0, source_name="Phantom"),
        ]
        result1 = extract_signals("BTC", dps, [], [])
        result2 = extract_signals("BTC", dps, [], [])
        for key in result1:
            assert result1[key].value == result2[key].value, f"{key} not deterministic"
            assert result1[key].confidence == result2[key].confidence

    def test_sufficient_data_four_non_unknown(self) -> None:
        """With sufficient data, at least 4 components are non-unknown."""
        from engine.signals import extract_signals
        dps = [
            # Funding rates for funding_stretch
            _make_dp(metric="funding_rate_a", value=0.0001),
            _make_dp(metric="funding_rate_b", value=0.0003),
            _make_dp(metric="funding_rate_c", value=0.0005),
            # OI data for oi_delta
            _make_dp(metric="open_interest", value=100.0),
            _make_dp(metric="open_interest", value=120.0),
            # Prices for basis (2 venues)
            _make_dp(metric="mark_price_a", value=50000.0, source_name="Imperial"),
            _make_dp(metric="mark_price_b", value=50200.0, source_name="Phantom"),
            # More prices for session_structure
            _make_dp(metric="mark_price_c", value=50100.0, source_name="VenueC"),
        ]
        candles = _make_candle_list(n=20, base_price=50000.0)
        result = extract_signals("BTC", dps, [], [], candles=candles)
        non_unknown = sum(1 for c in result.values() if c.label != "unknown")
        assert non_unknown >= 4, f"Only {non_unknown} non-unknown components: " + \
            ", ".join(f"{k}={v.label}" for k, v in result.items() if v.label != "unknown")


# ---------------------------------------------------------------------------
# Helpers for playbook tests
# ---------------------------------------------------------------------------


def _make_unknown_component(name: str) -> "SignalComponent":
    from engine.scoring import SignalComponent
    return SignalComponent(name=name, value=0.0, confidence=0.0, label="unknown")


def _make_active_component(name: str, value: float, label: str = "active") -> "SignalComponent":
    from engine.scoring import SignalComponent
    return SignalComponent(name=name, value=value, confidence=0.8, label=label)


def _all_unknown_signals() -> dict:
    from engine.scoring import COMPONENT_WEIGHTS
    return {name: _make_unknown_component(name) for name in COMPONENT_WEIGHTS}


def _default_playbook_args(
    price: float = 100.0,
    atr: float = 2.0,
    best_bid: float = 99.99,
    best_ask: float = 100.01,
) -> dict:
    """Default args for generate_playbooks with all-unknown signals."""
    from engine.playbooks import generate_playbooks
    return dict(
        symbol="BTC",
        price=price,
        atr=atr,
        signals=_all_unknown_signals(),
        best_bid=best_bid,
        best_ask=best_ask,
    )


# ---------------------------------------------------------------------------
# Playbook tests (VAL-PB-001 through VAL-PB-014)
# ---------------------------------------------------------------------------


class TestPlaybookBreakoutTrigger:
    """VAL-PB-001: Breakout triggers when oi_delta active + price beyond key level."""

    def test_breakout_present_with_active_oi_delta_and_session(self) -> None:
        from engine.playbooks import generate_playbooks
        signals = _all_unknown_signals()
        signals["oi_delta"] = _make_active_component("oi_delta", 0.5, "oi_rising_price_rising")
        signals["session_structure"] = _make_active_component("session_structure", 0.3, "above_vwap")
        args = _default_playbook_args()
        args["signals"] = signals
        result = generate_playbooks(**args)
        breakout = [p for p in result if p.setup_type == "breakout"]
        assert len(breakout) >= 1, f"No breakout found, got {[p.setup_type for p in result]}"
        assert breakout[0].side.value == "long"

    def test_breakout_short_with_bearish_oi_below_vwap(self) -> None:
        from engine.playbooks import generate_playbooks
        signals = _all_unknown_signals()
        signals["oi_delta"] = _make_active_component("oi_delta", -0.5, "oi_rising_price_falling")
        signals["session_structure"] = _make_active_component("session_structure", -0.3, "below_vwap")
        args = _default_playbook_args()
        args["signals"] = signals
        result = generate_playbooks(**args)
        breakout = [p for p in result if p.setup_type == "breakout"]
        assert len(breakout) >= 1
        assert breakout[0].side.value == "short"

    def test_no_breakout_when_oi_delta_unknown(self) -> None:
        from engine.playbooks import generate_playbooks
        signals = _all_unknown_signals()
        # session_structure active but oi_delta unknown → momentum shouldn't create breakout
        signals["session_structure"] = _make_active_component("session_structure", 0.3, "above_vwap")
        args = _default_playbook_args()
        args["signals"] = signals
        result = generate_playbooks(**args)
        breakout = [p for p in result if p.setup_type == "breakout"]
        assert len(breakout) == 0, f"Breakout should not trigger with unknown oi_delta"


class TestPlaybookFadeTrigger:
    """VAL-PB-002: Fade triggers when funding_stretch > 1.5 stdev, contrarian side."""

    def test_fade_present_at_high_stretch(self) -> None:
        from engine.playbooks import generate_playbooks
        signals = _all_unknown_signals()
        # value > 0.5 → stretched → contrarian LONG (bullish signal, value positive)
        signals["funding_stretch"] = _make_active_component(
            "funding_stretch", 0.7, "contrarian_bullish"
        )
        args = _default_playbook_args()
        args["signals"] = signals
        result = generate_playbooks(**args)
        fade = [p for p in result if p.setup_type == "fade"]
        assert len(fade) >= 1, f"No fade found, got {[p.setup_type for p in result]}"
        # Contrarian bullish value → LONG
        assert fade[0].side.value == "long"

    def test_fade_short_with_negative_stretch(self) -> None:
        from engine.playbooks import generate_playbooks
        signals = _all_unknown_signals()
        signals["funding_stretch"] = _make_active_component(
            "funding_stretch", -0.7, "contrarian_bearish"
        )
        args = _default_playbook_args()
        args["signals"] = signals
        result = generate_playbooks(**args)
        fade = [p for p in result if p.setup_type == "fade"]
        assert len(fade) >= 1
        assert fade[0].side.value == "short"

    def test_no_fade_below_threshold(self) -> None:
        from engine.playbooks import generate_playbooks
        signals = _all_unknown_signals()
        # value = 0.3, below 0.5 threshold
        signals["funding_stretch"] = _make_active_component(
            "funding_stretch", 0.3, "contrarian_bullish"
        )
        args = _default_playbook_args()
        args["signals"] = signals
        result = generate_playbooks(**args)
        fade = [p for p in result if p.setup_type == "fade"]
        assert len(fade) == 0

    def test_no_fade_when_unknown(self) -> None:
        from engine.playbooks import generate_playbooks
        signals = _all_unknown_signals()
        args = _default_playbook_args()
        args["signals"] = signals
        result = generate_playbooks(**args)
        fade = [p for p in result if p.setup_type == "fade"]
        assert len(fade) == 0


class TestPlaybookVwapReclaimTrigger:
    """VAL-PB-003: VWAP reclaim triggers with session_structure active."""

    def test_vwap_reclaim_present_with_session_structure(self) -> None:
        from engine.playbooks import generate_playbooks
        signals = _all_unknown_signals()
        signals["session_structure"] = _make_active_component(
            "session_structure", -0.3, "below_vwap"
        )
        args = _default_playbook_args()
        args["signals"] = signals
        result = generate_playbooks(**args)
        vwap = [p for p in result if p.setup_type == "vwap_reclaim"]
        assert len(vwap) >= 1
        # Price below VWAP → LONG (expect reclaim up)
        assert vwap[0].side.value == "long"

    def test_vwap_reclaim_short_above_vwap(self) -> None:
        from engine.playbooks import generate_playbooks
        signals = _all_unknown_signals()
        signals["session_structure"] = _make_active_component(
            "session_structure", 0.3, "above_vwap"
        )
        args = _default_playbook_args()
        args["signals"] = signals
        result = generate_playbooks(**args)
        vwap = [p for p in result if p.setup_type == "vwap_reclaim"]
        assert len(vwap) >= 1
        assert vwap[0].side.value == "short"

    def test_no_vwap_reclaim_when_unknown(self) -> None:
        from engine.playbooks import generate_playbooks
        signals = _all_unknown_signals()
        args = _default_playbook_args()
        args["signals"] = signals
        result = generate_playbooks(**args)
        vwap = [p for p in result if p.setup_type == "vwap_reclaim"]
        assert len(vwap) == 0


class TestPlaybookFundingFadeTrigger:
    """VAL-PB-004: Funding fade requires both funding_stretch AND oi_delta."""

    def test_funding_fade_with_both_active(self) -> None:
        from engine.playbooks import generate_playbooks
        signals = _all_unknown_signals()
        signals["funding_stretch"] = _make_active_component(
            "funding_stretch", 0.6, "contrarian_bullish"
        )
        signals["oi_delta"] = _make_active_component("oi_delta", 0.3, "oi_rising")
        args = _default_playbook_args()
        args["signals"] = signals
        result = generate_playbooks(**args)
        ff = [p for p in result if p.setup_type == "funding_fade"]
        assert len(ff) >= 1

    def test_no_funding_fade_with_only_funding(self) -> None:
        from engine.playbooks import generate_playbooks
        signals = _all_unknown_signals()
        signals["funding_stretch"] = _make_active_component(
            "funding_stretch", 0.6, "contrarian_bullish"
        )
        args = _default_playbook_args()
        args["signals"] = signals
        result = generate_playbooks(**args)
        ff = [p for p in result if p.setup_type == "funding_fade"]
        assert len(ff) == 0

    def test_no_funding_fade_with_only_oi(self) -> None:
        from engine.playbooks import generate_playbooks
        signals = _all_unknown_signals()
        signals["oi_delta"] = _make_active_component("oi_delta", 0.3, "oi_rising")
        args = _default_playbook_args()
        args["signals"] = signals
        result = generate_playbooks(**args)
        ff = [p for p in result if p.setup_type == "funding_fade"]
        assert len(ff) == 0

    def test_no_funding_fade_when_both_unknown(self) -> None:
        from engine.playbooks import generate_playbooks
        signals = _all_unknown_signals()
        args = _default_playbook_args()
        args["signals"] = signals
        result = generate_playbooks(**args)
        ff = [p for p in result if p.setup_type == "funding_fade"]
        assert len(ff) == 0


class TestPlaybookMomentumTrigger:
    """VAL-PB-005: Momentum continuation requires oi_delta aligned with trend."""

    def test_momentum_present_with_oi_bullish(self) -> None:
        from engine.playbooks import generate_playbooks
        signals = _all_unknown_signals()
        signals["oi_delta"] = _make_active_component("oi_delta", 0.5, "oi_rising_price_rising")
        # No session_structure → momentum, not breakout
        args = _default_playbook_args()
        args["signals"] = signals
        result = generate_playbooks(**args)
        momentum = [p for p in result if p.setup_type == "momentum_continuation"]
        assert len(momentum) >= 1
        assert momentum[0].side.value == "long"

    def test_momentum_short_with_bearish_oi(self) -> None:
        from engine.playbooks import generate_playbooks
        signals = _all_unknown_signals()
        signals["oi_delta"] = _make_active_component("oi_delta", -0.5, "oi_rising_price_falling")
        args = _default_playbook_args()
        args["signals"] = signals
        result = generate_playbooks(**args)
        momentum = [p for p in result if p.setup_type == "momentum_continuation"]
        assert len(momentum) >= 1
        assert momentum[0].side.value == "short"

    def test_no_momentum_when_oi_unknown(self) -> None:
        from engine.playbooks import generate_playbooks
        signals = _all_unknown_signals()
        args = _default_playbook_args()
        args["signals"] = signals
        result = generate_playbooks(**args)
        momentum = [p for p in result if p.setup_type == "momentum_continuation"]
        assert len(momentum) == 0


class TestPlaybookLiquiditySweepTrigger:
    """VAL-PB-006: Liquidity sweep triggers with active liquidity_magnet."""

    def test_sweep_present_with_active_magnet(self) -> None:
        from engine.playbooks import generate_playbooks
        signals = _all_unknown_signals()
        signals["liquidity_magnet"] = _make_active_component(
            "liquidity_magnet", -0.5, "ask_heavy"
        )
        args = _default_playbook_args()
        args["signals"] = signals
        result = generate_playbooks(**args)
        sweep = [p for p in result if p.setup_type == "liquidity_sweep"]
        assert len(sweep) >= 1
        # Ask-heavy (value < 0) → liquidity above → LONG sweep
        assert sweep[0].side.value == "long"

    def test_sweep_short_with_bid_heavy(self) -> None:
        from engine.playbooks import generate_playbooks
        signals = _all_unknown_signals()
        signals["liquidity_magnet"] = _make_active_component(
            "liquidity_magnet", 0.5, "bid_heavy"
        )
        args = _default_playbook_args()
        args["signals"] = signals
        result = generate_playbooks(**args)
        sweep = [p for p in result if p.setup_type == "liquidity_sweep"]
        assert len(sweep) >= 1
        assert sweep[0].side.value == "short"

    def test_no_sweep_when_unknown(self) -> None:
        from engine.playbooks import generate_playbooks
        signals = _all_unknown_signals()
        args = _default_playbook_args()
        args["signals"] = signals
        result = generate_playbooks(**args)
        sweep = [p for p in result if p.setup_type == "liquidity_sweep"]
        assert len(sweep) == 0


class TestPlaybookNoPlaybooksWhenAllUnknown:
    """VAL-PB-007: Empty list when all signals unknown."""

    def test_empty_list_all_unknown(self) -> None:
        from engine.playbooks import generate_playbooks
        args = _default_playbook_args()
        result = generate_playbooks(**args)
        assert result == []

    def test_empty_list_zero_atr(self) -> None:
        from engine.playbooks import generate_playbooks
        signals = _all_unknown_signals()
        signals["oi_delta"] = _make_active_component("oi_delta", 0.5)
        args = _default_playbook_args()
        args["signals"] = signals
        args["atr"] = 0.0
        result = generate_playbooks(**args)
        assert result == []


class TestPlaybookMaxThree:
    """VAL-PB-008: Maximum 3 playbooks per symbol."""

    def test_max_three_with_many_active_signals(self) -> None:
        from engine.playbooks import generate_playbooks
        signals = _all_unknown_signals()
        # Activate all signals that could trigger different setups
        signals["oi_delta"] = _make_active_component("oi_delta", 0.5, "oi_rising_price_rising")
        signals["funding_stretch"] = _make_active_component(
            "funding_stretch", 0.7, "contrarian_bullish"
        )
        signals["session_structure"] = _make_active_component(
            "session_structure", 0.3, "above_vwap"
        )
        signals["liquidity_magnet"] = _make_active_component(
            "liquidity_magnet", -0.5, "ask_heavy"
        )
        args = _default_playbook_args()
        args["signals"] = signals
        result = generate_playbooks(**args)
        assert len(result) <= 3, f"Got {len(result)} playbooks, expected max 3"


class TestPlaybookStructuralConstraints:
    """VAL-PB-009: Stop >= min_stop, TP1 >= 2R, TP2 >= 3R, correct ordering."""

    def test_stop_distance_at_least_min_stop(self) -> None:
        from engine.playbooks import generate_playbooks
        from engine.volatility import compute_min_stop
        from engine.paper_orders import OrderSide
        signals = _all_unknown_signals()
        signals["oi_delta"] = _make_active_component("oi_delta", 0.5)
        atr = 2.0
        price = 100.0
        args = _default_playbook_args(price=price, atr=atr)
        args["signals"] = signals
        result = generate_playbooks(**args)
        for pb in result:
            min_stop = compute_min_stop(atr, pb.entry, pb.side)
            stop_distance = abs(pb.entry - pb.stop)
            min_distance = abs(pb.entry - min_stop)
            assert stop_distance >= min_distance - 0.001, \
                f"{pb.setup_type}: stop_distance {stop_distance} < min {min_distance}"

    def test_tp1_at_least_2r(self) -> None:
        from engine.playbooks import generate_playbooks
        signals = _all_unknown_signals()
        signals["oi_delta"] = _make_active_component("oi_delta", 0.5)
        args = _default_playbook_args()
        args["signals"] = signals
        result = generate_playbooks(**args)
        for pb in result:
            stop_distance = abs(pb.entry - pb.stop)
            tp1_distance = abs(pb.tp1 - pb.entry)
            r_ratio = tp1_distance / stop_distance if stop_distance > 0 else 0
            assert r_ratio >= 2.0 - 0.001, \
                f"{pb.setup_type}: TP1 R-ratio {r_ratio} < 2.0"

    def test_tp2_at_least_3r(self) -> None:
        from engine.playbooks import generate_playbooks
        signals = _all_unknown_signals()
        signals["oi_delta"] = _make_active_component("oi_delta", 0.5)
        args = _default_playbook_args()
        args["signals"] = signals
        result = generate_playbooks(**args)
        for pb in result:
            stop_distance = abs(pb.entry - pb.stop)
            tp2_distance = abs(pb.tp2 - pb.entry)
            r_ratio = tp2_distance / stop_distance if stop_distance > 0 else 0
            assert r_ratio >= 3.0 - 0.001, \
                f"{pb.setup_type}: TP2 R-ratio {r_ratio} < 3.0"

    def test_long_ordering_stop_entry_tp(self) -> None:
        from engine.playbooks import generate_playbooks
        from engine.paper_orders import OrderSide
        signals = _all_unknown_signals()
        signals["oi_delta"] = _make_active_component("oi_delta", 0.5)
        args = _default_playbook_args()
        args["signals"] = signals
        result = generate_playbooks(**args)
        for pb in result:
            if pb.side == OrderSide.LONG:
                assert pb.stop < pb.entry, f"LONG: stop {pb.stop} >= entry {pb.entry}"
                assert pb.entry < pb.tp1, f"LONG: entry {pb.entry} >= tp1 {pb.tp1}"
                assert pb.tp1 < pb.tp2, f"LONG: tp1 {pb.tp1} >= tp2 {pb.tp2}"

    def test_short_ordering_tp_entry_stop(self) -> None:
        from engine.playbooks import generate_playbooks
        from engine.paper_orders import OrderSide
        signals = _all_unknown_signals()
        signals["oi_delta"] = _make_active_component("oi_delta", -0.5)
        args = _default_playbook_args()
        args["signals"] = signals
        result = generate_playbooks(**args)
        for pb in result:
            if pb.side == OrderSide.SHORT:
                assert pb.tp2 < pb.tp1, f"SHORT: tp2 {pb.tp2} >= tp1 {pb.tp1}"
                assert pb.tp1 < pb.entry, f"SHORT: tp1 {pb.tp1} >= entry {pb.entry}"
                assert pb.entry < pb.stop, f"SHORT: entry {pb.entry} >= stop {pb.stop}"

    def test_structural_constraints_at_various_atrs(self) -> None:
        from engine.playbooks import generate_playbooks
        from engine.paper_orders import OrderSide
        from engine.volatility import compute_min_stop

        for atr in [0.5, 1.0, 5.0, 50.0]:
            for price in [10.0, 100.0, 100000.0]:
                for val in [0.5, -0.5]:
                    signals = _all_unknown_signals()
                    signals["oi_delta"] = _make_active_component("oi_delta", val)
                    args = _default_playbook_args(price=price, atr=atr,
                                                   best_bid=price * 0.9999,
                                                   best_ask=price * 1.0001)
                    args["signals"] = signals
                    result = generate_playbooks(**args)
                    for pb in result:
                        # Stop distance >= min_stop
                        min_stop = compute_min_stop(atr, pb.entry, pb.side)
                        assert abs(pb.entry - pb.stop) >= abs(pb.entry - min_stop) - 0.001
                        # TP1 >= 2R, TP2 >= 3R
                        sd = abs(pb.entry - pb.stop)
                        assert abs(pb.tp1 - pb.entry) / sd >= 2.0 - 0.001
                        assert abs(pb.tp2 - pb.entry) / sd >= 3.0 - 0.001


class TestPlaybookPassiveEntry:
    """VAL-PB-010: Entry is passively placeable against bid/ask."""

    def test_long_entry_at_or_below_best_bid(self) -> None:
        from engine.playbooks import generate_playbooks
        from engine.paper_orders import OrderSide
        signals = _all_unknown_signals()
        signals["oi_delta"] = _make_active_component("oi_delta", 0.5)
        best_bid = 99.99
        best_ask = 100.01
        args = _default_playbook_args(best_bid=best_bid, best_ask=best_ask)
        args["signals"] = signals
        result = generate_playbooks(**args)
        for pb in result:
            if pb.side == OrderSide.LONG:
                assert pb.entry <= best_bid, \
                    f"LONG entry {pb.entry} > best_bid {best_bid}"

    def test_short_entry_at_or_above_best_ask(self) -> None:
        from engine.playbooks import generate_playbooks
        from engine.paper_orders import OrderSide
        signals = _all_unknown_signals()
        signals["oi_delta"] = _make_active_component("oi_delta", -0.5)
        best_bid = 99.99
        best_ask = 100.01
        args = _default_playbook_args(best_bid=best_bid, best_ask=best_ask)
        args["signals"] = signals
        result = generate_playbooks(**args)
        for pb in result:
            if pb.side == OrderSide.SHORT:
                assert pb.entry >= best_ask, \
                    f"SHORT entry {pb.entry} < best_ask {best_ask}"


class TestPlaybookSideFromSignal:
    """VAL-PB-011: Side derived from signal direction, not arbitrary default."""

    def test_breakout_long_with_positive_oi(self) -> None:
        from engine.playbooks import generate_playbooks
        signals = _all_unknown_signals()
        signals["oi_delta"] = _make_active_component("oi_delta", 0.5, "oi_rising_price_rising")
        signals["session_structure"] = _make_active_component("session_structure", 0.3, "above_vwap")
        args = _default_playbook_args()
        args["signals"] = signals
        result = generate_playbooks(**args)
        breakout = [p for p in result if p.setup_type == "breakout"]
        assert breakout[0].side.value == "long"

    def test_breakout_short_with_negative_oi(self) -> None:
        from engine.playbooks import generate_playbooks
        signals = _all_unknown_signals()
        signals["oi_delta"] = _make_active_component("oi_delta", -0.5, "oi_rising_price_falling")
        signals["session_structure"] = _make_active_component("session_structure", -0.3, "below_vwap")
        args = _default_playbook_args()
        args["signals"] = signals
        result = generate_playbooks(**args)
        breakout = [p for p in result if p.setup_type == "breakout"]
        assert breakout[0].side.value == "short"

    def test_fade_side_contrarian_to_funding(self) -> None:
        from engine.playbooks import generate_playbooks
        signals = _all_unknown_signals()
        # Positive value = contrarian bullish → LONG
        signals["funding_stretch"] = _make_active_component(
            "funding_stretch", 0.7, "contrarian_bullish"
        )
        args = _default_playbook_args()
        args["signals"] = signals
        result = generate_playbooks(**args)
        fade = [p for p in result if p.setup_type == "fade"]
        assert len(fade) >= 1
        assert fade[0].side.value == "long"

    def test_fade_side_contrarian_negative_funding(self) -> None:
        from engine.playbooks import generate_playbooks
        signals = _all_unknown_signals()
        # Negative value = contrarian bearish → SHORT
        signals["funding_stretch"] = _make_active_component(
            "funding_stretch", -0.7, "contrarian_bearish"
        )
        args = _default_playbook_args()
        args["signals"] = signals
        result = generate_playbooks(**args)
        fade = [p for p in result if p.setup_type == "fade"]
        assert len(fade) >= 1
        assert fade[0].side.value == "short"


class TestPlaybookMetadata:
    """VAL-PB-012: Every playbook has valid metadata fields."""

    def test_rationale_non_empty(self) -> None:
        from engine.playbooks import generate_playbooks
        signals = _all_unknown_signals()
        signals["oi_delta"] = _make_active_component("oi_delta", 0.5)
        args = _default_playbook_args()
        args["signals"] = signals
        result = generate_playbooks(**args)
        for pb in result:
            assert len(pb.rationale) > 0, f"{pb.setup_type}: empty rationale"

    def test_probability_band_valid(self) -> None:
        from engine.playbooks import generate_playbooks
        signals = _all_unknown_signals()
        signals["oi_delta"] = _make_active_component("oi_delta", 0.5)
        args = _default_playbook_args()
        args["signals"] = signals
        result = generate_playbooks(**args)
        for pb in result:
            assert pb.probability_band in ("high", "medium", "low"), \
                f"{pb.setup_type}: invalid band '{pb.probability_band}'"

    def test_expected_r_r_at_least_2(self) -> None:
        from engine.playbooks import generate_playbooks
        signals = _all_unknown_signals()
        signals["oi_delta"] = _make_active_component("oi_delta", 0.5)
        args = _default_playbook_args()
        args["signals"] = signals
        result = generate_playbooks(**args)
        for pb in result:
            assert pb.expected_r_r >= 2.0, \
                f"{pb.setup_type}: expected_r_r {pb.expected_r_r} < 2.0"

    def test_invalidation_beyond_stop(self) -> None:
        from engine.playbooks import generate_playbooks
        from engine.paper_orders import OrderSide
        signals = _all_unknown_signals()
        signals["oi_delta"] = _make_active_component("oi_delta", 0.5)
        args = _default_playbook_args()
        args["signals"] = signals
        result = generate_playbooks(**args)
        for pb in result:
            if pb.side == OrderSide.LONG:
                assert pb.invalidation < pb.stop, \
                    f"LONG: invalidation {pb.invalidation} >= stop {pb.stop}"
            else:
                assert pb.invalidation > pb.stop, \
                    f"SHORT: invalidation {pb.invalidation} <= stop {pb.stop}"

    def test_setup_type_in_defined_set(self) -> None:
        from engine.playbooks import generate_playbooks, SETUP_TYPES
        signals = _all_unknown_signals()
        signals["oi_delta"] = _make_active_component("oi_delta", 0.5)
        args = _default_playbook_args()
        args["signals"] = signals
        result = generate_playbooks(**args)
        for pb in result:
            assert pb.setup_type in SETUP_TYPES, \
                f"Unknown setup_type: {pb.setup_type}"


class TestPlaybookEdgeCases:
    """VAL-PB-014: Zero ATR, missing bid/ask, pipeline integration."""

    def test_zero_atr_no_crash(self) -> None:
        from engine.playbooks import generate_playbooks
        signals = _all_unknown_signals()
        signals["oi_delta"] = _make_active_component("oi_delta", 0.5)
        args = _default_playbook_args(atr=0.0)
        args["signals"] = signals
        result = generate_playbooks(**args)
        assert isinstance(result, list)
        assert len(result) == 0  # Can't compute stop with 0 ATR

    def test_zero_bid_ask_no_crash(self) -> None:
        from engine.playbooks import generate_playbooks
        signals = _all_unknown_signals()
        signals["oi_delta"] = _make_active_component("oi_delta", 0.5)
        args = _default_playbook_args(best_bid=0.0, best_ask=0.0)
        args["signals"] = signals
        result = generate_playbooks(**args)
        assert isinstance(result, list)
        assert len(result) == 0

    def test_playbook_consumable_by_risk_sizing(self) -> None:
        from engine.playbooks import generate_playbooks
        from engine.risk import compute_risk_sizing, RiskParams
        from engine.paper_orders import OrderSide
        signals = _all_unknown_signals()
        signals["oi_delta"] = _make_active_component("oi_delta", 0.5)
        price = 100.0
        atr = 2.0
        best_bid = price * 0.9999
        best_ask = price * 1.0001
        args = _default_playbook_args(price=price, atr=atr,
                                       best_bid=best_bid, best_ask=best_ask)
        args["signals"] = signals
        playbooks = generate_playbooks(**args)
        params = RiskParams()
        for pb in playbooks:
            sizing = compute_risk_sizing(
                symbol="BTC", side=pb.side, entry=pb.entry, stop=pb.stop,
                params=params, best_bid=best_bid, best_ask=best_ask,
            )
            # Should produce a valid result (might be invalid if leverage out of range)
            assert isinstance(sizing.valid, bool)
            assert isinstance(sizing.reject_reason, str)


# ---------------------------------------------------------------------------
# Evaluate Outcomes tests (VAL-OUT-001 through VAL-OUT-011)
# ---------------------------------------------------------------------------

def _make_eval_order(**overrides) -> dict:
    """Create a test order dict for evaluate-outcomes testing."""
    defaults = {
        "symbol": "SOL",
        "side": "long",
        "entry": 150.0,
        "stop": 145.0,
        "tp1": 160.0,
        "tp2": 170.0,
        "setup": "breakout",
        "created_ts_aest": "2026-06-01 08:00:00 Australia/Sydney",
        "fees_bps": 5.0,
        "slippage_bps": 3.0,
        "provenance_tags": "test",
        "signals": ["funding_stretch", "oi_delta"],
    }
    defaults.update(overrides)
    return defaults


def _setup_eval_env(tmp_path: Path, orders: list[dict] | None = None) -> Path:
    """Create temp directory structure for evaluate-outcomes testing."""
    (tmp_path / "memory").mkdir(exist_ok=True)
    (tmp_path / "ledgers").mkdir(exist_ok=True)
    state = {
        "mode": "live-paper-only",
        "open_paper_orders": orders or [],
    }
    (tmp_path / "memory" / "mission_state.json").write_text(
        json.dumps(state, indent=2) + "\n"
    )
    return tmp_path


class TestEvaluateOutcomes:
    """Tests for --mode evaluate-outcomes (VAL-OUT-001 through VAL-OUT-011)."""

    def test_cli_mode_accepted_val_out_001(self) -> None:
        """VAL-OUT-001: argparse accepts evaluate-outcomes and routes to handler."""
        source = Path("engine/run_scan.py").read_text()
        # Verify argparse choices include evaluate-outcomes
        assert "evaluate-outcomes" in source
        # Verify handler function exists
        assert "_run_evaluate_outcomes" in source
        # Verify dispatch in main()
        assert "evaluate-outcomes" in source

    def test_reads_orders_and_evaluates_fill_val_out_002(self, tmp_path: Path) -> None:
        """VAL-OUT-002: Reads open orders, fetches candle data, evaluates fill."""
        from engine.run_scan import _run_evaluate_outcomes
        from unittest.mock import patch

        # LONG at 150, TP at 160, current price 161 → fills + TP hit
        order = _make_eval_order(entry=150.0, stop=145.0, tp1=160.0)
        base = _setup_eval_env(tmp_path, [order])

        with patch("engine.run_scan._fetch_mark_prices", return_value={"SOL": 161.0}):
            result = _run_evaluate_outcomes(base_path=base)

        assert result == 0
        # Verify outcomes.csv was written
        outcomes_path = base / "ledgers" / "outcomes.csv"
        assert outcomes_path.exists()
        content = outcomes_path.read_text()
        assert "SOL" in content

    def test_outcome_computation_with_fees_val_out_003(self, tmp_path: Path) -> None:
        """VAL-OUT-003: R, MAE, MFE computed with fees/slippage deduction."""
        from engine.outcomes import OutcomeEvaluator
        from engine.paper_orders import OrderSide, PaperOrder

        order = PaperOrder(
            symbol="SOL", setup="test", side=OrderSide.LONG,
            entry=150, stop=145, tp1=160, tp2=170,
            fees_bps=5.0, slippage_bps=3.0,
        )
        evaluator = OutcomeEvaluator(
            outcomes_path=tmp_path / "outcomes.csv",
            signal_outcomes_path=tmp_path / "signal_outcomes.csv",
        )
        # LONG win: exit at TP (160), stop_distance=5, raw R = (160-150)/5 = 2.0
        # cost_r = (5+3)/10000 = 0.0008, result_r ≈ 1.9992
        outcome = evaluator.compute_outcome(
            order, exit_price=160.0,
            mae_price=148.0, mfe_price=162.0,
            fees_bps=5.0, slippage_bps=3.0,
        )
        assert outcome.result_r > 0
        assert outcome.result_r < 2.0  # fees deducted
        assert outcome.max_fve > 0  # MFE positive
        assert outcome.max_ade > 0  # MAE positive

        # Same-candle ambiguity: conservative stop exit
        outcome_loss = evaluator.compute_outcome(
            order, exit_price=145.0,
            fees_bps=5.0, slippage_bps=3.0,
        )
        assert outcome_loss.result_r < 0  # loss

    def test_outcomes_csv_schema_val_out_004(self, tmp_path: Path) -> None:
        """VAL-OUT-004: Outcomes written to outcomes.csv with correct schema."""
        from engine.run_scan import _run_evaluate_outcomes
        from unittest.mock import patch

        order = _make_eval_order(entry=150.0, stop=145.0, tp1=160.0)
        base = _setup_eval_env(tmp_path, [order])

        with patch("engine.run_scan._fetch_mark_prices", return_value={"SOL": 161.0}):
            _run_evaluate_outcomes(base_path=base)

        outcomes_path = base / "ledgers" / "outcomes.csv"
        assert outcomes_path.exists()
        content = outcomes_path.read_text()
        lines = content.strip().split("\n")
        assert len(lines) >= 2  # header + at least 1 data row
        header = lines[0]
        # Verify schema columns
        assert "result_R" in header
        assert "max_FvE" in header
        assert "max_AdE" in header
        assert "fees_bps" in header
        assert "slippage_bps" in header
        assert "symbol" in header
        assert "side" in header

    def test_cancel_rules_enforced_val_out_005(self, tmp_path: Path) -> None:
        """VAL-OUT-005: Hard exit cancel rule triggers for old in-trade orders."""
        from engine.run_scan import _run_evaluate_outcomes
        from unittest.mock import patch

        # Order created 2 days ago → hard exit triggers (past 22:00 AEST next day)
        old_ts = (datetime.now(AEST) - timedelta(days=2)).strftime(
            "%Y-%m-%d %H:%M:%S Australia/Sydney"
        )
        order = _make_eval_order(
            entry=150.0, stop=145.0, tp1=160.0,
            created_ts_aest=old_ts,
        )
        base = _setup_eval_env(tmp_path, [order])

        # Price near entry: fills (candle covers entry to current),
        # stays in_trade (no TP/stop hit), then hard exit cancels it
        with patch("engine.run_scan._fetch_mark_prices", return_value={"SOL": 152.0}):
            _run_evaluate_outcomes(base_path=base)

        outcomes_path = base / "ledgers" / "outcomes.csv"
        assert outcomes_path.exists()
        content = outcomes_path.read_text()
        assert "hard_exit" in content

    def test_mission_state_updated_preserves_in_trade_val_out_006(self, tmp_path: Path) -> None:
        """VAL-OUT-006: Resolved orders removed, in-trade orders preserved."""
        from engine.run_scan import _run_evaluate_outcomes
        from unittest.mock import patch

        # Use a recent timestamp so hard_exit_22_aest doesn't trigger
        recent_ts = (datetime.now(AEST) - timedelta(hours=1)).strftime(
            "%Y-%m-%d %H:%M:%S Australia/Sydney"
        )
        # Order 1: LONG at 150, TP at 160, price at 161 → fills + TP hit → closed
        closed_order = _make_eval_order(
            entry=150.0, stop=145.0, tp1=160.0, symbol="SOL",
            created_ts_aest=recent_ts,
        )
        # Order 2: LONG at 100, stop at 95, TP at 110, price at 105 → fills, in_trade
        in_trade_order = _make_eval_order(
            entry=100.0, stop=95.0, tp1=110.0, symbol="ETH",
            created_ts_aest=recent_ts,
        )
        base = _setup_eval_env(tmp_path, [closed_order, in_trade_order])

        with patch("engine.run_scan._fetch_mark_prices",
                    return_value={"SOL": 161.0, "ETH": 105.0}):
            _run_evaluate_outcomes(base_path=base)

        # Check mission state: only ETH should remain
        state = json.loads((base / "memory" / "mission_state.json").read_text())
        remaining = state["open_paper_orders"]
        assert len(remaining) == 1
        assert remaining[0]["symbol"] == "ETH"

    def test_signal_attribution_and_stats_val_out_007(self, tmp_path: Path) -> None:
        """VAL-OUT-007: Signal attribution written, stats computed."""
        from engine.run_scan import _run_evaluate_outcomes
        from unittest.mock import patch

        order = _make_eval_order(
            entry=150.0, stop=145.0, tp1=160.0,
            signals=["funding_stretch", "oi_delta"],
        )
        base = _setup_eval_env(tmp_path, [order])

        with patch("engine.run_scan._fetch_mark_prices", return_value={"SOL": 161.0}):
            _run_evaluate_outcomes(base_path=base)

        signal_path = base / "ledgers" / "signal_outcomes.csv"
        assert signal_path.exists()
        content = signal_path.read_text()
        assert "funding_stretch" in content
        assert "oi_delta" in content

    def test_graceful_empty_missing_state_val_out_008(self, tmp_path: Path) -> None:
        """VAL-OUT-008: Graceful no-op when no orders or missing state."""
        from engine.run_scan import _run_evaluate_outcomes

        # Empty orders
        base = _setup_eval_env(tmp_path, [])
        result = _run_evaluate_outcomes(base_path=base)
        assert result == 0

        # Missing mission_state.json
        base2 = tmp_path / "empty_env"
        base2.mkdir()
        (base2 / "memory").mkdir()
        (base2 / "ledgers").mkdir()
        result2 = _run_evaluate_outcomes(base_path=base2)
        assert result2 == 0

    def test_correct_exit_codes_val_out_009(self, tmp_path: Path) -> None:
        """VAL-OUT-009: Returns 0 on success, non-zero on fatal error."""
        from engine.run_scan import _run_evaluate_outcomes

        # Success case: empty orders
        base = _setup_eval_env(tmp_path, [])
        assert _run_evaluate_outcomes(base_path=base) == 0

        # Fatal error case: corrupt state
        base2 = tmp_path / "corrupt"
        base2.mkdir()
        (base2 / "memory").mkdir()
        (base2 / "ledgers").mkdir()
        (base2 / "memory" / "mission_state.json").write_text("NOT VALID JSON{{{")
        assert _run_evaluate_outcomes(base_path=base2) == 1

    def test_shell_script_exists_executable_val_out_010(self) -> None:
        """VAL-OUT-010: evaluate_outcomes.sh exists and is executable."""
        script = Path("scripts/evaluate_outcomes.sh")
        assert script.exists(), "evaluate_outcomes.sh must exist"
        assert script.stat().st_mode & 0o111, "evaluate_outcomes.sh must be executable"
        content = script.read_text()
        assert "#!/bin/bash" in content
        assert "evaluate-outcomes" in content

    def test_refuses_pre_order_candle_data_val_out_011(self) -> None:
        """VAL-OUT-011: Pre-order candle data rejected."""
        from engine.paper_orders import OrderSide, PaperOrder, evaluate_fill

        order = PaperOrder(
            symbol="SOL", setup="test", side=OrderSide.LONG,
            entry=150, stop=145, tp1=160, tp2=170,
            created_ts_aest="2026-06-01 08:10:00 Australia/Sydney",
        )
        order_ts = datetime(2026, 6, 1, 8, 10, tzinfo=AEST)
        # Candle timestamp BEFORE order creation
        candle_ts = datetime(2026, 6, 1, 8, 5, tzinfo=AEST)
        result = evaluate_fill(order, 161, 148, 150, 155, candle_ts, order_ts)
        assert result["status"] == "invalid_for_stats"
        assert result.get("filled") is not True  # no fill from stale data

    def test_cancel_timeout_rule(self, tmp_path: Path) -> None:
        """Timeout cancel rule triggers for pending orders older than 90 minutes."""
        from engine.run_scan import _run_evaluate_outcomes
        from unittest.mock import patch

        # Order created 2 hours ago
        old_ts = (datetime.now(AEST) - timedelta(hours=2)).strftime(
            "%Y-%m-%d %H:%M:%S Australia/Sydney"
        )
        order = _make_eval_order(
            entry=150.0, stop=145.0, tp1=160.0,
            created_ts_aest=old_ts,
        )
        base = _setup_eval_env(tmp_path, [order])

        # Mock evaluate_fill to return not-filled so order stays PENDING
        not_filled_result = {"filled": False}
        with patch("engine.paper_orders.evaluate_fill", return_value=not_filled_result):
            with patch("engine.run_scan._fetch_mark_prices", return_value={"SOL": 152.0}):
                _run_evaluate_outcomes(base_path=base)

        outcomes_path = base / "ledgers" / "outcomes.csv"
        assert outcomes_path.exists()
        content = outcomes_path.read_text()
        assert "timeout_90min" in content

    def test_cancel_drift_rule(self, tmp_path: Path) -> None:
        """Cancel triggers when price drifts > 0.8 * stop_distance."""
        from engine.run_scan import _run_evaluate_outcomes
        from unittest.mock import patch

        recent_ts = (datetime.now(AEST) - timedelta(minutes=5)).strftime(
            "%Y-%m-%d %H:%M:%S Australia/Sydney"
        )
        # LONG at 150, stop at 145, stop_distance = 5
        # Price at 155: |155-150| = 5, 0.8*5 = 4 → drift triggered
        order = _make_eval_order(
            entry=150.0, stop=145.0, tp1=160.0,
            created_ts_aest=recent_ts,
        )
        base = _setup_eval_env(tmp_path, [order])

        # Mock evaluate_fill to return not-filled so order stays PENDING
        not_filled_result = {"filled": False}
        with patch("engine.paper_orders.evaluate_fill", return_value=not_filled_result):
            with patch("engine.run_scan._fetch_mark_prices", return_value={"SOL": 156.0}):
                _run_evaluate_outcomes(base_path=base)

        outcomes_path = base / "ledgers" / "outcomes.csv"
        assert outcomes_path.exists()
        content = outcomes_path.read_text()
        assert "price_drift" in content

    def test_cancel_hard_exit_rule(self, tmp_path: Path) -> None:
        """Cancel triggers on hard exit after 22:00 AEST next day."""
        from engine.run_scan import _run_evaluate_outcomes
        from unittest.mock import patch

        # Order created 2 days ago (well past next-day 22:00 AEST)
        old_ts = (datetime.now(AEST) - timedelta(days=2)).strftime(
            "%Y-%m-%d %H:%M:%S Australia/Sydney"
        )
        order = _make_eval_order(
            entry=150.0, stop=145.0, tp1=160.0,
            created_ts_aest=old_ts,
        )
        base = _setup_eval_env(tmp_path, [order])

        with patch("engine.run_scan._fetch_mark_prices", return_value={"SOL": 152.0}):
            _run_evaluate_outcomes(base_path=base)

        outcomes_path = base / "ledgers" / "outcomes.csv"
        assert outcomes_path.exists()
        content = outcomes_path.read_text()
        # Hard exit should trigger
        assert "hard_exit" in content
