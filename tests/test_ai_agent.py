"""Tests for engine/ai_agent.py — AI agent trade decision module."""

from __future__ import annotations

import pytest

from engine.ai_agent import (
    AITradeCandidate,
    build_ai_report_section,
    candidates_to_signal_components,
    parse_ai_response,
    validate_candidate,
)
from engine.paper_orders import OrderSide


# ---------------------------------------------------------------------------
# parse_ai_response
# ---------------------------------------------------------------------------


class TestParseAIResponse:
    def test_clean_json_array(self):
        raw = '[{"symbol":"BTC","side":"long","setup_type":"breakout","entry":100000,"stop":99000,"tp1":102000,"tp2":103000,"probability_band":"medium","rationale":"Test"}]'
        result = parse_ai_response(raw)
        assert len(result) == 1
        assert result[0].symbol == "BTC"
        assert result[0].side == OrderSide.LONG
        assert result[0].entry == 100000.0
        assert result[0].stop == 99000.0
        assert result[0].setup_type == "breakout"

    def test_markdown_fenced_json(self):
        raw = '```json\n[{"symbol":"ETH","side":"short","setup_type":"fade","entry":3000,"stop":3050,"tp1":2900,"tp2":2850,"probability_band":"high","rationale":"Funding stretched"}]\n```'
        result = parse_ai_response(raw)
        assert len(result) == 1
        assert result[0].symbol == "ETH"
        assert result[0].side == OrderSide.SHORT

    def test_empty_array(self):
        assert parse_ai_response("[]") == []

    def test_no_trades_text(self):
        assert parse_ai_response("No trades") == []
        assert parse_ai_response("none") == []

    def test_empty_string(self):
        assert parse_ai_response("") == []

    def test_multiple_candidates(self):
        raw = '[{"symbol":"BTC","side":"long","setup_type":"breakout","entry":100000,"stop":99000,"tp1":102000,"tp2":103000,"probability_band":"medium","rationale":"A"},{"symbol":"SOL","side":"short","setup_type":"fade","entry":150,"stop":152,"tp1":146,"tp2":144,"probability_band":"low","rationale":"B"}]'
        result = parse_ai_response(raw)
        assert len(result) == 2
        assert result[0].symbol == "BTC"
        assert result[1].symbol == "SOL"

    def test_missing_symbol_skipped(self):
        raw = '[{"side":"long","entry":100,"stop":99,"tp1":102,"tp2":103}]'
        result = parse_ai_response(raw)
        assert len(result) == 0

    def test_invalid_side_skipped(self):
        raw = '[{"symbol":"BTC","side":"sideways","entry":100,"stop":99,"tp1":102,"tp2":103}]'
        result = parse_ai_response(raw)
        assert len(result) == 0

    def test_zero_entry_skipped(self):
        raw = '[{"symbol":"BTC","side":"long","entry":0,"stop":99,"tp1":102,"tp2":103}]'
        result = parse_ai_response(raw)
        assert len(result) == 0

    def test_default_probability_band(self):
        raw = '[{"symbol":"BTC","side":"long","setup_type":"custom","entry":100,"stop":99,"tp1":102,"tp2":103,"rationale":"Test"}]'
        result = parse_ai_response(raw)
        assert len(result) == 1
        assert result[0].probability_band == "low"

    def test_single_object_not_array(self):
        raw = '{"symbol":"BTC","side":"long","setup_type":"breakout","entry":100000,"stop":99000,"tp1":102000,"tp2":103000,"probability_band":"medium","rationale":"Test"}'
        result = parse_ai_response(raw)
        assert len(result) == 1
        assert result[0].symbol == "BTC"

    def test_text_before_json(self):
        raw = 'Based on my analysis, here are the trades:\n[{"symbol":"BTC","side":"long","setup_type":"breakout","entry":100000,"stop":99000,"tp1":102000,"tp2":103000,"probability_band":"medium","rationale":"Test"}]'
        result = parse_ai_response(raw)
        assert len(result) == 1


# ---------------------------------------------------------------------------
# validate_candidate
# ---------------------------------------------------------------------------


class TestValidateCandidate:
    def test_valid_long(self):
        c = AITradeCandidate(
            symbol="BTC", side=OrderSide.LONG, setup_type="breakout",
            entry=100000, stop=98500, tp1=103000, tp2=104500,
            probability_band="medium", rationale="Test",
        )
        result = validate_candidate(c, atr=1500)
        assert result.valid is True
        assert result.reject_reason == ""

    def test_valid_short(self):
        c = AITradeCandidate(
            symbol="ETH", side=OrderSide.SHORT, setup_type="fade",
            entry=3000, stop=3060, tp1=2880, tp2=2820,
            probability_band="high", rationale="Test",
        )
        result = validate_candidate(c, atr=60)
        assert result.valid is True

    def test_long_stop_above_entry(self):
        c = AITradeCandidate(
            symbol="BTC", side=OrderSide.LONG, setup_type="breakout",
            entry=100000, stop=101000, tp1=102000, tp2=103000,
            probability_band="medium", rationale="Test",
        )
        result = validate_candidate(c)
        assert result.valid is False
        assert "stop" in result.reject_reason.lower() or "entry" in result.reject_reason.lower()

    def test_short_stop_below_entry(self):
        c = AITradeCandidate(
            symbol="ETH", side=OrderSide.SHORT, setup_type="fade",
            entry=3000, stop=2950, tp1=2900, tp2=2850,
            probability_band="medium", rationale="Test",
        )
        result = validate_candidate(c)
        assert result.valid is False

    def test_long_tp1_below_entry(self):
        c = AITradeCandidate(
            symbol="BTC", side=OrderSide.LONG, setup_type="breakout",
            entry=100000, stop=99000, tp1=98000, tp2=97000,
            probability_band="medium", rationale="Test",
        )
        result = validate_candidate(c)
        assert result.valid is False
        assert "tp1" in result.reject_reason.lower()

    def test_short_tp1_above_entry(self):
        c = AITradeCandidate(
            symbol="ETH", side=OrderSide.SHORT, setup_type="fade",
            entry=3000, stop=3060, tp1=3100, tp2=3150,
            probability_band="medium", rationale="Test",
        )
        result = validate_candidate(c)
        assert result.valid is False

    def test_stop_too_close_atr(self):
        c = AITradeCandidate(
            symbol="BTC", side=OrderSide.LONG, setup_type="breakout",
            entry=100000, stop=99999, tp1=100002, tp2=100003,
            probability_band="medium", rationale="Test",
        )
        result = validate_candidate(c, atr=1500)
        assert result.valid is False
        assert "ATR" in result.reject_reason

    def test_no_atr_check_passes(self):
        c = AITradeCandidate(
            symbol="BTC", side=OrderSide.LONG, setup_type="breakout",
            entry=100000, stop=99999, tp1=100002, tp2=100003,
            probability_band="medium", rationale="Test",
        )
        result = validate_candidate(c, atr=None)
        assert result.valid is True  # No ATR check

    def test_rr_below_2_rejected(self):
        c = AITradeCandidate(
            symbol="BTC", side=OrderSide.LONG, setup_type="breakout",
            entry=100000, stop=99000, tp1=100500, tp2=101000,
            probability_band="medium", rationale="Test",
        )
        # stop_distance=1000, tp1 only 500 above entry → 0.5R
        result = validate_candidate(c)
        assert result.valid is False
        assert "R:R" in result.reject_reason


# ---------------------------------------------------------------------------
# candidates_to_signal_components
# ---------------------------------------------------------------------------


class TestCandidatesToSignalComponents:
    def test_converts_candidates(self):
        candidates = [
            AITradeCandidate(
                symbol="BTC", side=OrderSide.LONG, setup_type="breakout",
                entry=100000, stop=99000, tp1=102000, tp2=103000,
                probability_band="high", rationale="Test",
            ),
            AITradeCandidate(
                symbol="ETH", side=OrderSide.SHORT, setup_type="fade",
                entry=3000, stop=3060, tp1=2880, tp2=2820,
                probability_band="medium", rationale="Test",
            ),
        ]
        result = candidates_to_signal_components(candidates)
        assert "BTC" in result
        assert "ETH" in result
        assert result["BTC"]["ai_conviction"].value > 0  # LONG
        assert result["ETH"]["ai_conviction"].value < 0  # SHORT
        assert result["BTC"]["ai_setup"].label == "breakout"

    def test_empty_list(self):
        result = candidates_to_signal_components([])
        assert result == {}


# ---------------------------------------------------------------------------
# build_ai_report_section
# ---------------------------------------------------------------------------


class TestBuildAIReportSection:
    def test_no_candidates(self):
        result = build_ai_report_section([])
        assert "No trade candidates" in result

    def test_with_candidates(self):
        candidates = [
            AITradeCandidate(
                symbol="BTC", side=OrderSide.LONG, setup_type="breakout",
                entry=100000, stop=99000, tp1=102000, tp2=103000,
                probability_band="medium", rationale="Strong momentum",
            ),
        ]
        result = build_ai_report_section(candidates)
        assert "BTC" in result
        assert "LONG" in result
        assert "breakout" in result
        assert "Strong momentum" in result

    def test_with_rejected_candidate(self):
        candidates = [
            AITradeCandidate(
                symbol="BTC", side=OrderSide.LONG, setup_type="breakout",
                entry=100000, stop=101000, tp1=102000, tp2=103000,
                probability_band="medium", rationale="Bad stop",
                valid=False, reject_reason="stop above entry",
            ),
        ]
        result = build_ai_report_section(candidates)
        assert "REJECTED" in result


# ---------------------------------------------------------------------------
# Integration: parse → validate → report
# ---------------------------------------------------------------------------


class TestAIEndToEnd:
    def test_full_pipeline(self):
        raw = '[{"symbol":"SOL","side":"long","setup_type":"vwap_reclaim","entry":80.0,"stop":78.0,"tp1":84.0,"tp2":86.0,"probability_band":"medium","rationale":"Below VWAP, expecting reclaim"}]'
        candidates = parse_ai_response(raw)
        assert len(candidates) == 1

        validated = validate_candidate(candidates[0], atr=1.5)
        assert validated.valid is True

        report = build_ai_report_section([validated])
        assert "SOL" in report
        assert "VALID" in report

    def test_multiple_some_invalid(self):
        raw = '[{"symbol":"BTC","side":"long","setup_type":"breakout","entry":100000,"stop":101000,"tp1":102000,"tp2":103000,"probability_band":"medium","rationale":"Bad"},{"symbol":"ETH","side":"short","setup_type":"fade","entry":3000,"stop":3060,"tp1":2880,"tp2":2820,"probability_band":"high","rationale":"Good"}]'
        candidates = parse_ai_response(raw)
        assert len(candidates) == 2

        for c in candidates:
            validate_candidate(c, atr=1500 if c.symbol == "BTC" else 60)

        # BTC should be invalid (stop > entry for LONG), ETH should be valid
        assert candidates[0].valid is False
        assert candidates[1].valid is True
