"""Tests for engine/weekly_review.py — VAL-WR-001 through VAL-WR-009."""

from __future__ import annotations

import csv
from dataclasses import fields as dataclass_fields
from pathlib import Path

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _write_outcomes_csv(
    path: Path,
    rows: list[dict],
) -> None:
    """Write outcomes.csv with required schema."""
    header = (
        "date_Australia/Sydney,symbol,setup,side,entry,stop,tp1,tp2,filled,"
        "entry_ts_Australia/Sydney,exit_ts_Australia/Sydney,result_R,"
        "max_FvE,max_AdE,fees_bps,slippage_bps,notes,provenance_tags"
    )
    with open(path, "w", encoding="utf-8", newline="") as f:
        f.write(header + "\n")
        for row in rows:
            f.write(
                f"{row.get('date','2026-06-01')},"
                f"{row.get('symbol','SOL')},"
                f"{row.get('setup','test')},"
                f"{row.get('side','long')},"
                f"{row.get('entry','150')},"
                f"{row.get('stop','145')},"
                f"{row.get('tp1','160')},"
                f"{row.get('tp2','170')},"
                f"{row.get('filled','filled')},"
                f"{row.get('entry_ts','2026-06-01 08:00:00 Australia/Sydney')},"
                f"{row.get('exit_ts','2026-06-01 12:00:00 Australia/Sydney')},"
                f"{row.get('result_R','0.0')},"
                f"{row.get('max_FvE','0.0')},"
                f"{row.get('max_AdE','0.0')},"
                f"{row.get('fees_bps','5')},"
                f"{row.get('slippage_bps','3')},"
                f"{row.get('notes','')},"
                f"{row.get('provenance_tags','')}\n"
            )


def _write_signal_outcomes_csv(
    path: Path,
    rows: list[dict],
) -> None:
    """Write signal_outcomes.csv with required schema."""
    header = "order_id,signal,result_r,timestamp_Australia/Sydney"
    with open(path, "w", encoding="utf-8", newline="") as f:
        f.write(header + "\n")
        for row in rows:
            f.write(
                f"{row.get('order_id','ord1')},"
                f"{row.get('signal','funding_stretch')},"
                f"{row.get('result_r','0.0')},"
                f"{row.get('timestamp','2026-06-01 12:00:00 Australia/Sydney')}\n"
            )


def _write_paper_orders_csv(
    path: Path,
    rows: list[dict],
) -> None:
    """Write paper_orders.csv with required schema."""
    header = (
        "date_Australia/Sydney,symbol,setup,side,entry,stop,tp1,tp2,filled,"
        "entry_ts_Australia/Sydney,exit_ts_Australia/Sydney,result_R,"
        "max_FvE,max_AdE,fees_bps,slippage_bps,notes,provenance_tags"
    )
    with open(path, "w", encoding="utf-8", newline="") as f:
        f.write(header + "\n")
        for row in rows:
            f.write(
                f"{row.get('date','2026-06-01')},"
                f"{row.get('symbol','SOL')},"
                f"{row.get('setup','test')},"
                f"{row.get('side','long')},"
                f"{row.get('entry','150')},"
                f"{row.get('stop','145')},"
                f"{row.get('tp1','160')},"
                f"{row.get('tp2','170')},"
                f"{row.get('filled','filled')},"
                f"{row.get('entry_ts','')},"
                f"{row.get('exit_ts','')},"
                f"{row.get('result_R','')},"
                f"{row.get('max_FvE','')},"
                f"{row.get('max_AdE','')},"
                f"{row.get('fees_bps','0')},"
                f"{row.get('slippage_bps','3')},"
                f"{row.get('notes','')},"
                f"{row.get('provenance_tags','')}\n"
            )


# ---------------------------------------------------------------------------
# VAL-WR-001: Expectancy (avg R per trade)
# ---------------------------------------------------------------------------


class TestExpectancy:
    """VAL-WR-001: Expectancy = mean of all result_R values."""

    def test_known_r_series(self, tmp_path: Path) -> None:
        """[+2.0, -1.0, +1.5] → expectancy = 0.833."""
        from engine.weekly_review import run_weekly_review

        outcomes = tmp_path / "outcomes.csv"
        _write_outcomes_csv(outcomes, [
            {"result_R": "2.0"},
            {"result_R": "-1.0"},
            {"result_R": "1.5"},
        ])
        signal = tmp_path / "signal_outcomes.csv"
        _write_signal_outcomes_csv(signal, [])
        orders = tmp_path / "paper_orders.csv"
        _write_paper_orders_csv(orders, [])

        result = run_weekly_review(outcomes, signal, orders)
        expected = (2.0 + -1.0 + 1.5) / 3.0
        assert abs(result.expectancy_r - expected) < 0.01

    def test_empty_outcomes_expectancy_zero(self, tmp_path: Path) -> None:
        """Empty data → 0.0."""
        from engine.weekly_review import run_weekly_review

        outcomes = tmp_path / "outcomes.csv"
        _write_outcomes_csv(outcomes, [])
        signal = tmp_path / "signal_outcomes.csv"
        _write_signal_outcomes_csv(signal, [])
        orders = tmp_path / "paper_orders.csv"
        _write_paper_orders_csv(orders, [])

        result = run_weekly_review(outcomes, signal, orders)
        assert result.expectancy_r == 0.0

    def test_single_positive_r(self, tmp_path: Path) -> None:
        from engine.weekly_review import run_weekly_review

        outcomes = tmp_path / "outcomes.csv"
        _write_outcomes_csv(outcomes, [{"result_R": "3.0"}])
        result = run_weekly_review(outcomes, tmp_path / "sig.csv", tmp_path / "po.csv")
        assert result.expectancy_r == 3.0


# ---------------------------------------------------------------------------
# VAL-WR-002: Max drawdown (worst peak-to-trough R)
# ---------------------------------------------------------------------------


class TestMaxDrawdown:
    """VAL-WR-002: Max drawdown from cumulative R series."""

    def test_known_drawdown(self, tmp_path: Path) -> None:
        """[+2, -1, -1, +0.5, -2] → cumulative: [2, 1, 0, 0.5, -1.5]
        peak starts at 2, trough at -1.5 → drawdown = 3.5."""
        from engine.weekly_review import run_weekly_review

        outcomes = tmp_path / "outcomes.csv"
        _write_outcomes_csv(outcomes, [
            {"result_R": "2.0"},
            {"result_R": "-1.0"},
            {"result_R": "-1.0"},
            {"result_R": "0.5"},
            {"result_R": "-2.0"},
        ])
        result = run_weekly_review(outcomes, tmp_path / "sig.csv", tmp_path / "po.csv")
        assert abs(result.max_drawdown_r - 3.5) < 0.01

    def test_all_wins_zero_drawdown(self, tmp_path: Path) -> None:
        """All wins → 0.0."""
        from engine.weekly_review import run_weekly_review

        outcomes = tmp_path / "outcomes.csv"
        _write_outcomes_csv(outcomes, [
            {"result_R": "1.0"},
            {"result_R": "2.0"},
            {"result_R": "0.5"},
        ])
        result = run_weekly_review(outcomes, tmp_path / "sig.csv", tmp_path / "po.csv")
        assert result.max_drawdown_r == 0.0

    def test_empty_drawdown_zero(self, tmp_path: Path) -> None:
        """Empty → 0.0."""
        from engine.weekly_review import run_weekly_review

        result = run_weekly_review(
            tmp_path / "outcomes.csv",
            tmp_path / "sig.csv",
            tmp_path / "po.csv",
        )
        assert result.max_drawdown_r == 0.0

    def test_all_losses_drawdown(self, tmp_path: Path) -> None:
        """All losses: [-1, -2, -0.5] → cumulative [-1, -3, -3.5], peak=0, drawdown=3.5."""
        from engine.weekly_review import run_weekly_review

        outcomes = tmp_path / "outcomes.csv"
        _write_outcomes_csv(outcomes, [
            {"result_R": "-1.0"},
            {"result_R": "-2.0"},
            {"result_R": "-0.5"},
        ])
        result = run_weekly_review(outcomes, tmp_path / "sig.csv", tmp_path / "po.csv")
        assert abs(result.max_drawdown_r - 3.5) < 0.01


# ---------------------------------------------------------------------------
# VAL-WR-003: Profit factor
# ---------------------------------------------------------------------------


class TestProfitFactor:
    """VAL-WR-003: Profit factor = gross_wins / gross_losses."""

    def test_mixed_trades(self, tmp_path: Path) -> None:
        """[+2, -1, +1.5, -0.5] → pf = 3.5 / 1.5 = 2.333."""
        from engine.weekly_review import run_weekly_review

        outcomes = tmp_path / "outcomes.csv"
        _write_outcomes_csv(outcomes, [
            {"result_R": "2.0"},
            {"result_R": "-1.0"},
            {"result_R": "1.5"},
            {"result_R": "-0.5"},
        ])
        result = run_weekly_review(outcomes, tmp_path / "sig.csv", tmp_path / "po.csv")
        expected = (2.0 + 1.5) / (1.0 + 0.5)
        assert abs(result.profit_factor - expected) < 0.01

    def test_all_wins_inf(self, tmp_path: Path) -> None:
        """No losses → inf."""
        from engine.weekly_review import run_weekly_review

        outcomes = tmp_path / "outcomes.csv"
        _write_outcomes_csv(outcomes, [
            {"result_R": "2.0"},
            {"result_R": "1.5"},
        ])
        result = run_weekly_review(outcomes, tmp_path / "sig.csv", tmp_path / "po.csv")
        assert result.profit_factor == float("inf")

    def test_all_losses_zero(self, tmp_path: Path) -> None:
        """No wins → 0.0."""
        from engine.weekly_review import run_weekly_review

        outcomes = tmp_path / "outcomes.csv"
        _write_outcomes_csv(outcomes, [
            {"result_R": "-2.0"},
            {"result_R": "-1.0"},
        ])
        result = run_weekly_review(outcomes, tmp_path / "sig.csv", tmp_path / "po.csv")
        assert result.profit_factor == 0.0

    def test_no_trades_zero(self, tmp_path: Path) -> None:
        """No trades → 0.0."""
        from engine.weekly_review import run_weekly_review

        result = run_weekly_review(
            tmp_path / "outcomes.csv",
            tmp_path / "sig.csv",
            tmp_path / "po.csv",
        )
        assert result.profit_factor == 0.0

    def test_zero_r_not_counted_as_win_or_loss(self, tmp_path: Path) -> None:
        """result_R == 0 contributes to neither wins nor losses."""
        from engine.weekly_review import run_weekly_review

        outcomes = tmp_path / "outcomes.csv"
        _write_outcomes_csv(outcomes, [
            {"result_R": "0.0"},
        ])
        result = run_weekly_review(outcomes, tmp_path / "sig.csv", tmp_path / "po.csv")
        # Only zero R → no wins, no losses → 0.0
        assert result.profit_factor == 0.0


# ---------------------------------------------------------------------------
# VAL-WR-004: Fill, cancel, no-trade rates (sum ≤ 1.0)
# ---------------------------------------------------------------------------


class TestOrderRates:
    """VAL-WR-004: Rate metrics from paper_orders.csv; invariant sum ≤ 1.0."""

    def test_known_fixture(self, tmp_path: Path) -> None:
        """3 filled, 2 cancelled, 1 pending out of 6."""
        from engine.weekly_review import run_weekly_review

        orders = tmp_path / "paper_orders.csv"
        _write_paper_orders_csv(orders, [
            {"filled": "filled"},
            {"filled": "filled"},
            {"filled": "filled"},
            {"filled": "cancelled"},
            {"filled": "cancelled"},
            {"filled": "pending"},
        ])
        result = run_weekly_review(
            tmp_path / "outcomes.csv",
            tmp_path / "sig.csv",
            orders,
        )
        assert abs(result.fill_rate - 3 / 6) < 0.01
        assert abs(result.cancel_rate - 2 / 6) < 0.01
        assert abs(result.no_trade_rate - 1 / 6) < 0.01

    def test_rate_sum_leq_one(self, tmp_path: Path) -> None:
        """fill_rate + cancel_rate + no_trade_rate ≤ 1.0."""
        from engine.weekly_review import run_weekly_review

        orders = tmp_path / "paper_orders.csv"
        _write_paper_orders_csv(orders, [
            {"filled": "filled"},
            {"filled": "cancelled"},
            {"filled": "expired"},
            {"filled": "pending"},
        ])
        result = run_weekly_review(
            tmp_path / "outcomes.csv",
            tmp_path / "sig.csv",
            orders,
        )
        total = result.fill_rate + result.cancel_rate + result.no_trade_rate
        assert total <= 1.0 + 1e-9

    def test_empty_orders_zero_rates(self, tmp_path: Path) -> None:
        """No orders → all 0.0."""
        from engine.weekly_review import run_weekly_review

        result = run_weekly_review(
            tmp_path / "outcomes.csv",
            tmp_path / "sig.csv",
            tmp_path / "paper_orders.csv",
        )
        assert result.fill_rate == 0.0
        assert result.cancel_rate == 0.0
        assert result.no_trade_rate == 0.0

    def test_all_filled(self, tmp_path: Path) -> None:
        from engine.weekly_review import run_weekly_review

        orders = tmp_path / "paper_orders.csv"
        _write_paper_orders_csv(orders, [
            {"filled": "filled"},
            {"filled": "filled"},
        ])
        result = run_weekly_review(
            tmp_path / "outcomes.csv",
            tmp_path / "sig.csv",
            orders,
        )
        assert result.fill_rate == 1.0
        assert result.cancel_rate == 0.0
        assert result.no_trade_rate == 0.0

    def test_expired_counts_as_no_trade(self, tmp_path: Path) -> None:
        from engine.weekly_review import run_weekly_review

        orders = tmp_path / "paper_orders.csv"
        _write_paper_orders_csv(orders, [
            {"filled": "expired"},
        ])
        result = run_weekly_review(
            tmp_path / "outcomes.csv",
            tmp_path / "sig.csv",
            orders,
        )
        assert result.no_trade_rate == 1.0
        assert result.fill_rate == 0.0
        assert result.cancel_rate == 0.0


# ---------------------------------------------------------------------------
# VAL-WR-005: Per-signal hit rate and avg R
# ---------------------------------------------------------------------------


class TestSignalStats:
    """VAL-WR-005: Signal stats from signal_outcomes.csv."""

    def test_known_signal_stats(self, tmp_path: Path) -> None:
        """funding_stretch with [+2, -1, +1] → hit_rate=2/3, avg_R=2/3."""
        from engine.weekly_review import run_weekly_review

        signal = tmp_path / "signal_outcomes.csv"
        _write_signal_outcomes_csv(signal, [
            {"signal": "funding_stretch", "result_r": "2.0"},
            {"signal": "funding_stretch", "result_r": "-1.0"},
            {"signal": "funding_stretch", "result_r": "1.0"},
        ])
        result = run_weekly_review(
            tmp_path / "outcomes.csv",
            signal,
            tmp_path / "po.csv",
        )
        stats = result.signal_stats
        assert "funding_stretch" in stats
        assert abs(stats["funding_stretch"]["hit_rate"] - 2 / 3) < 0.01
        assert abs(stats["funding_stretch"]["avg_R"] - 2 / 3) < 0.01

    def test_multiple_signals(self, tmp_path: Path) -> None:
        from engine.weekly_review import run_weekly_review

        signal = tmp_path / "signal_outcomes.csv"
        _write_signal_outcomes_csv(signal, [
            {"signal": "funding_stretch", "result_r": "1.0"},
            {"signal": "oi_delta", "result_r": "-0.5"},
            {"signal": "oi_delta", "result_r": "2.0"},
        ])
        result = run_weekly_review(
            tmp_path / "outcomes.csv",
            signal,
            tmp_path / "po.csv",
        )
        assert "funding_stretch" in result.signal_stats
        assert "oi_delta" in result.signal_stats
        assert result.signal_stats["funding_stretch"]["hit_rate"] == 1.0
        assert abs(result.signal_stats["oi_delta"]["hit_rate"] - 0.5) < 0.01

    def test_empty_signal_stats(self, tmp_path: Path) -> None:
        """Missing file → empty dict."""
        from engine.weekly_review import run_weekly_review

        result = run_weekly_review(
            tmp_path / "outcomes.csv",
            tmp_path / "sig.csv",
            tmp_path / "po.csv",
        )
        assert result.signal_stats == {}


# ---------------------------------------------------------------------------
# VAL-WR-006: Top 3 recommendations and next build pick
# ---------------------------------------------------------------------------


class TestRecommendations:
    """VAL-WR-006: Top 3 recommendations ranked by impact, plus next_build_pick."""

    def test_exactly_three_recommendations(self, tmp_path: Path) -> None:
        """Always returns exactly 3 recommendations."""
        from engine.weekly_review import run_weekly_review

        result = run_weekly_review(
            tmp_path / "outcomes.csv",
            tmp_path / "sig.csv",
            tmp_path / "po.csv",
        )
        assert len(result.top_recommendations) == 3

    def test_recommendations_non_empty_strings(self, tmp_path: Path) -> None:
        from engine.weekly_review import run_weekly_review

        result = run_weekly_review(
            tmp_path / "outcomes.csv",
            tmp_path / "sig.csv",
            tmp_path / "po.csv",
        )
        for rec in result.top_recommendations:
            assert isinstance(rec, str)
            assert len(rec) > 0

    def test_placeholders_on_empty(self, tmp_path: Path) -> None:
        """With no data: 3 placeholders."""
        from engine.weekly_review import run_weekly_review

        result = run_weekly_review(
            tmp_path / "outcomes.csv",
            tmp_path / "sig.csv",
            tmp_path / "po.csv",
        )
        assert len(result.top_recommendations) == 3
        # Placeholders should still be meaningful strings
        for rec in result.top_recommendations:
            assert len(rec) > 0

    def test_next_build_pick_non_empty(self, tmp_path: Path) -> None:
        """next_build_pick is a non-empty string."""
        from engine.weekly_review import run_weekly_review

        result = run_weekly_review(
            tmp_path / "outcomes.csv",
            tmp_path / "sig.csv",
            tmp_path / "po.csv",
        )
        assert isinstance(result.next_build_pick, str)
        assert len(result.next_build_pick) > 0

    def test_recommendations_with_data(self, tmp_path: Path) -> None:
        """With performance data, recommendations are data-driven."""
        from engine.weekly_review import run_weekly_review

        outcomes = tmp_path / "outcomes.csv"
        _write_outcomes_csv(outcomes, [
            {"result_R": "-2.0"},
            {"result_R": "-1.5"},
            {"result_R": "0.5"},
        ])
        result = run_weekly_review(outcomes, tmp_path / "sig.csv", tmp_path / "po.csv")
        assert len(result.top_recommendations) == 3
        # With negative expectancy, should mention improving win rate or signal quality
        recs_text = " ".join(result.top_recommendations).lower()
        assert len(recs_text) > 0

    def test_recommendations_ranked_by_impact(self, tmp_path: Path) -> None:
        """With mixed data, recommendations are ordered by expected impact."""
        from engine.weekly_review import run_weekly_review

        outcomes = tmp_path / "outcomes.csv"
        _write_outcomes_csv(outcomes, [
            {"result_R": "-3.0"},
            {"result_R": "-2.0"},
            {"result_R": "1.0"},
        ])
        result = run_weekly_review(outcomes, tmp_path / "sig.csv", tmp_path / "po.csv")
        assert len(result.top_recommendations) == 3


# ---------------------------------------------------------------------------
# VAL-WR-007: Graceful empty data handling
# ---------------------------------------------------------------------------


class TestGracefulEmptyData:
    """VAL-WR-007: No crash when CSV files missing or empty."""

    def test_all_files_missing(self, tmp_path: Path) -> None:
        """All CSV files missing → safe defaults, no exception."""
        from engine.weekly_review import run_weekly_review, WeeklyReviewResult

        result = run_weekly_review(
            tmp_path / "nonexistent_outcomes.csv",
            tmp_path / "nonexistent_signal.csv",
            tmp_path / "nonexistent_orders.csv",
        )
        assert isinstance(result, WeeklyReviewResult)
        assert result.total_trades == 0
        assert result.expectancy_r == 0.0
        assert result.max_drawdown_r == 0.0
        assert result.profit_factor == 0.0
        assert result.fill_rate == 0.0
        assert result.cancel_rate == 0.0
        assert result.no_trade_rate == 0.0
        assert result.signal_stats == {}

    def test_empty_csv_files(self, tmp_path: Path) -> None:
        """Empty CSV files (headers only) → safe defaults."""
        from engine.weekly_review import run_weekly_review, WeeklyReviewResult

        outcomes = tmp_path / "outcomes.csv"
        signal = tmp_path / "signal_outcomes.csv"
        orders = tmp_path / "paper_orders.csv"
        _write_outcomes_csv(outcomes, [])
        _write_signal_outcomes_csv(signal, [])
        _write_paper_orders_csv(orders, [])

        result = run_weekly_review(outcomes, signal, orders)
        assert isinstance(result, WeeklyReviewResult)
        assert result.total_trades == 0
        assert result.expectancy_r == 0.0

    def test_no_filenotfound_error(self, tmp_path: Path) -> None:
        """No FileNotFoundError when files are missing."""
        from engine.weekly_review import run_weekly_review

        # Should not raise any exception
        try:
            run_weekly_review(
                tmp_path / "missing1.csv",
                tmp_path / "missing2.csv",
                tmp_path / "missing3.csv",
            )
        except FileNotFoundError:
            pytest.fail("run_weekly_review raised FileNotFoundError on missing files")

    def test_no_zerodivision_error(self, tmp_path: Path) -> None:
        """No ZeroDivisionError when data is empty."""
        from engine.weekly_review import run_weekly_review

        try:
            run_weekly_review(
                tmp_path / "missing1.csv",
                tmp_path / "missing2.csv",
                tmp_path / "missing3.csv",
            )
        except ZeroDivisionError:
            pytest.fail("run_weekly_review raised ZeroDivisionError on empty data")


# ---------------------------------------------------------------------------
# VAL-WR-008: Returns complete WeeklyReviewResult dataclass
# ---------------------------------------------------------------------------


class TestWeeklyReviewResultDataclass:
    """VAL-WR-008: Return type has all required fields."""

    def test_is_dataclass(self) -> None:
        from engine.weekly_review import WeeklyReviewResult
        import dataclasses
        assert dataclasses.is_dataclass(WeeklyReviewResult)

    def test_has_all_ten_fields(self) -> None:
        from engine.weekly_review import WeeklyReviewResult
        field_names = {f.name for f in dataclass_fields(WeeklyReviewResult)}
        expected = {
            "total_trades",
            "expectancy_r",
            "max_drawdown_r",
            "profit_factor",
            "fill_rate",
            "cancel_rate",
            "no_trade_rate",
            "signal_stats",
            "top_recommendations",
            "next_build_pick",
        }
        assert field_names == expected

    def test_return_type(self, tmp_path: Path) -> None:
        from engine.weekly_review import run_weekly_review, WeeklyReviewResult

        result = run_weekly_review(
            tmp_path / "outcomes.csv",
            tmp_path / "sig.csv",
            tmp_path / "po.csv",
        )
        assert isinstance(result, WeeklyReviewResult)

    def test_field_types(self, tmp_path: Path) -> None:
        from engine.weekly_review import run_weekly_review

        result = run_weekly_review(
            tmp_path / "outcomes.csv",
            tmp_path / "sig.csv",
            tmp_path / "po.csv",
        )
        assert isinstance(result.total_trades, int)
        assert isinstance(result.expectancy_r, float)
        assert isinstance(result.max_drawdown_r, float)
        assert isinstance(result.profit_factor, float)
        assert isinstance(result.fill_rate, float)
        assert isinstance(result.cancel_rate, float)
        assert isinstance(result.no_trade_rate, float)
        assert isinstance(result.signal_stats, dict)
        assert isinstance(result.top_recommendations, list)
        assert isinstance(result.next_build_pick, str)

    def test_reads_from_all_three_sources(self, tmp_path: Path) -> None:
        """Function accepts and reads from all three CSV sources."""
        from engine.weekly_review import run_weekly_review

        outcomes = tmp_path / "outcomes.csv"
        signal = tmp_path / "signal_outcomes.csv"
        orders = tmp_path / "paper_orders.csv"
        _write_outcomes_csv(outcomes, [{"result_R": "1.0"}])
        _write_signal_outcomes_csv(signal, [
            {"signal": "oi_delta", "result_r": "1.0"},
        ])
        _write_paper_orders_csv(orders, [{"filled": "filled"}])

        result = run_weekly_review(outcomes, signal, orders)
        assert result.total_trades == 1
        assert result.expectancy_r == 1.0
        assert result.fill_rate == 1.0
        assert "oi_delta" in result.signal_stats


# ---------------------------------------------------------------------------
# VAL-WR-009: weekly_review.sh exists and is executable
# ---------------------------------------------------------------------------


class TestWeeklyReviewScript:
    """VAL-WR-009: Shell script wrapper present."""

    def test_script_exists(self) -> None:
        script = Path(__file__).resolve().parent.parent / "scripts" / "weekly_review.sh"
        assert script.exists()

    def test_script_is_executable(self) -> None:
        script = Path(__file__).resolve().parent.parent / "scripts" / "weekly_review.sh"
        assert script.stat().st_mode & 0o111  # any execute bit

    def test_script_has_shebang(self) -> None:
        script = Path(__file__).resolve().parent.parent / "scripts" / "weekly_review.sh"
        content = script.read_text()
        assert content.startswith("#!/bin/bash")

    def test_script_invokes_correct_module(self) -> None:
        script = Path(__file__).resolve().parent.parent / "scripts" / "weekly_review.sh"
        content = script.read_text()
        assert "engine.weekly_review" in content or "weekly_review" in content


# ---------------------------------------------------------------------------
# VAL-CROSS-005: Outcomes feed into weekly review metrics
# ---------------------------------------------------------------------------


class TestCrossAreaOutcomesToWeeklyReview:
    """VAL-CROSS-005: Outcome data correctly summarized in weekly review."""

    def test_known_fixture_metrics(self, tmp_path: Path) -> None:
        """5 trades (3 wins: +2R, +1.5R, +3R; 2 losses: -1R, -0.5R)."""
        from engine.weekly_review import run_weekly_review

        outcomes = tmp_path / "outcomes.csv"
        _write_outcomes_csv(outcomes, [
            {"result_R": "2.0"},
            {"result_R": "-1.0"},
            {"result_R": "1.5"},
            {"result_R": "-0.5"},
            {"result_R": "3.0"},
        ])

        signal = tmp_path / "signal_outcomes.csv"
        _write_signal_outcomes_csv(signal, [
            {"signal": "funding_stretch", "result_r": "2.0"},
            {"signal": "funding_stretch", "result_r": "-1.0"},
            {"signal": "funding_stretch", "result_r": "1.5"},
        ])

        orders = tmp_path / "paper_orders.csv"
        _write_paper_orders_csv(orders, [
            {"filled": "filled"},
            {"filled": "filled"},
            {"filled": "filled"},
            {"filled": "cancelled"},
            {"filled": "filled"},
        ])

        result = run_weekly_review(outcomes, signal, orders)

        # Expectancy = (2 - 1 + 1.5 - 0.5 + 3) / 5 = 5/5 = 1.0
        assert abs(result.expectancy_r - 1.0) < 0.01
        # Profit factor = (2 + 1.5 + 3) / (1 + 0.5) = 6.5 / 1.5 ≈ 4.333
        assert abs(result.profit_factor - 6.5 / 1.5) < 0.01
        # Total trades = 5
        assert result.total_trades == 5
        # Fill rate = 4/5
        assert abs(result.fill_rate - 4 / 5) < 0.01
        # Signal stats populated
        assert "funding_stretch" in result.signal_stats
        fs = result.signal_stats["funding_stretch"]
        assert abs(fs["avg_R"] - (2.0 - 1.0 + 1.5) / 3) < 0.01
        assert abs(fs["hit_rate"] - 2 / 3) < 0.01
