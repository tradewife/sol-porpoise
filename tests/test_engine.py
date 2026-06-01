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
