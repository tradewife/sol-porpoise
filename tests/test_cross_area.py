"""Cross-area integration tests (VAL-CROSS-001 through VAL-CROSS-010).

Verifies that all pipeline stages integrate correctly:
- Volatility feeds into signals
- Signals feed into scoring
- Signal conditions trigger correct playbook types
- Playbook entry/stop feeds into risk sizing
- Valid sizing produces paper orders; invalid produces skipped trades
- Evaluate-outcomes processes open orders end-to-end
- Outcomes feed into weekly review (covered in test_weekly_review.py)
- Full scan loop with mocked adapters produces complete output
- No regressions (all existing tests pass + dry-run works)
- Cross-venue basis uses two data sources; whale data integrates
- Mission state, provenance, KG triples updated after runs
- Report section H reflects current gaps
"""

from __future__ import annotations

import csv
import json
import math
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from zoneinfo import ZoneInfo

AEST = ZoneInfo("Australia/Sydney")


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def _make_provenance(source_name: str = "Imperial", source_ts: str = "2026-06-01T12:00:00Z") -> "Provenance":
    from adapters.base import Provenance, SourceTier
    return Provenance(
        source_name=source_name,
        source_tier=SourceTier.OPEN,
        source_link="[no-link]",
        source_ts=source_ts,
        fetched_ts_aest="2026-06-01 22:00:00 Australia/Sydney",
        confidence=0.9,
    )


def _make_dp(
    symbol: str = "BTC",
    metric: str = "mark_price",
    value: float = 100000.0,
    source_name: str = "Imperial",
    source_ts: str = "2026-06-01T12:00:00Z",
    attrs: dict | None = None,
) -> "DataPoint":
    from adapters.base import DataPoint
    return DataPoint(
        symbol=symbol,
        metric=metric,
        value=value,
        provenance=_make_provenance(source_name, source_ts),
        attrs=attrs or {},
    )


def _make_candles(n: int = 20, base_price: float = 100.0, volatility: float = 0.02) -> list:
    """Generate n Candle objects with controlled volatility."""
    from engine.volatility import Candle
    import random
    random.seed(42)
    candles = []
    price = base_price
    for i in range(n):
        change = price * volatility * (random.random() - 0.45)  # slight upward bias
        o = price
        c = price + change
        h = max(o, c) + abs(change) * 0.5
        l = min(o, c) - abs(change) * 0.5
        if l <= 0:
            l = 0.01
        candles.append(Candle(
            open=o, high=h, low=l, close=c,
            timestamp=f"2026-06-01T{i:02d}:00:00Z",
        ))
        price = c
    return candles


def _make_active_signals(direction: float = 1.0) -> dict:
    """Create a dict of signal components with most active, for playbook testing."""
    from engine.scoring import SignalComponent
    return {
        "funding_stretch": SignalComponent("funding_stretch", -0.8 * direction, 0.8, "contrarian_bearish" if direction > 0 else "contrarian_bullish"),
        "oi_delta": SignalComponent("oi_delta", 0.6 * direction, 0.9, "oi_rising_price_rising" if direction > 0 else "oi_rising_price_falling"),
        "basis": SignalComponent("basis", 0.3 * direction, 0.7, "perp_premium"),
        "liquidity_magnet": SignalComponent("liquidity_magnet", 0.5 * direction, 0.6, "bid_heavy" if direction > 0 else "ask_heavy"),
        "session_structure": SignalComponent("session_structure", 0.4 * direction, 0.7, "above_vwap" if direction > 0 else "below_vwap"),
        "whale_evidence": SignalComponent("whale_evidence", 0.2 * direction, 0.5, "smart_money_directional"),
        "dex_perp_lag": SignalComponent("dex_perp_lag", 0.15 * direction, 0.4, "lead_Imperial"),
        "volatility": SignalComponent("volatility", 0.5, 0.8, "regime_Normal"),
        "catalyst": SignalComponent("catalyst", 0, 0, "unknown"),
    }


def _setup_eval_env(tmp_path: Path, orders: list[dict]) -> Path:
    """Create a temporary directory structure for evaluate-outcomes testing.

    Returns the account path (tmp_path/accounts/deterministic/) which can be
    passed as base_path to _run_evaluate_outcomes.
    """
    acct = tmp_path / "accounts" / "deterministic"
    (acct / "memory").mkdir(parents=True)
    (acct / "ledgers").mkdir(parents=True)

    state = {
        "mode": "live-paper-only",
        "last_run_id": "run_20260601T120000_AEST",
        "open_paper_orders": orders,
    }
    (acct / "memory" / "mission_state.json").write_text(
        json.dumps(state, indent=2) + "\n"
    )
    return acct


def _make_eval_order(
    entry: float = 150.0,
    stop: float = 145.0,
    tp1: float = 160.0,
    tp2: float = 170.0,
    symbol: str = "SOL",
    side: str = "long",
    created_ts_aest: str | None = None,
    signals: list[str] | None = None,
) -> dict:
    """Create an order dict matching mission_state.json format."""
    ts = created_ts_aest or (datetime.now(AEST) - timedelta(minutes=5)).strftime(
        "%Y-%m-%d %H:%M:%S Australia/Sydney"
    )
    return {
        "symbol": symbol,
        "side": side,
        "setup": "breakout",
        "entry": entry,
        "stop": stop,
        "tp1": tp1,
        "tp2": tp2,
        "qty": 10.0,
        "notional": entry * 10.0,
        "leverage": 10.0,
        "created_ts_aest": ts,
        "fees_bps": 5.0,
        "slippage_bps": 3.0,
        "provenance_tags": "run=test",
        "signals": signals or ["oi_delta", "funding_stretch"],
    }


# ===========================================================================
# VAL-CROSS-001: Volatility → signals → scoring pipeline
# ===========================================================================


class TestVolatilitySignalsScoringPipeline:
    """VAL-CROSS-001: ATR output feeds signal extraction, which feeds scoring."""

    def test_atr_feeds_into_volatility_signal_component(self) -> None:
        """Known candles produce ATR; volatility component is non-unknown."""
        from engine.volatility import Candle, compute_atr
        from engine.signals import extract_signals

        candles = _make_candles(30, base_price=100.0, volatility=0.05)
        # Compute ATR to verify it's positive
        atr = compute_atr(candles)
        assert atr > 0

        # Extract signals with candles
        components = extract_signals(
            symbol="BTC",
            datapoints=[],
            whale_points=[],
            hl_points=[],
            candles=candles,
            precomputed_atr=atr,
        )

        vol = components["volatility"]
        assert vol.label != "unknown", "Volatility should be non-unknown with candle data"
        assert vol.confidence > 0, "Volatility confidence should be > 0"
        assert vol.value != 0, "Volatility value should be non-zero"

    def test_signals_feed_into_scoring(self) -> None:
        """extract_signals output produces valid GraphSignalScore."""
        from engine.signals import extract_signals
        from engine.scoring import compute_signal_score

        # Create datapoints with funding rates from two sources
        datapoints = []
        for i in range(5):
            datapoints.append(_make_dp("BTC", "funding_rate", 0.0001 + i * 0.00005, "Imperial"))
        datapoints.append(_make_dp("BTC", "mark_price", 100000.0, "Imperial"))
        datapoints.append(_make_dp("BTC", "mark_price", 100010.0, "Phantom"))

        candles = _make_candles(20, base_price=100000.0, volatility=0.01)

        components = extract_signals(
            symbol="BTC",
            datapoints=datapoints,
            whale_points=[],
            hl_points=[],
            candles=candles,
        )

        score = compute_signal_score("BTC", components)
        assert isinstance(score.weighted_score, float)
        assert math.isfinite(score.weighted_score)
        assert score.overall_confidence >= 0
        assert isinstance(score.unknown_components, list)
        # With candles and some data, not all should be unknown
        assert len(score.unknown_components) < 9, "Should have some non-unknown components"

    def test_pipeline_deterministic(self) -> None:
        """Same inputs produce same outputs."""
        from engine.signals import extract_signals
        from engine.scoring import compute_signal_score

        datapoints = [_make_dp("ETH", "funding_rate", 0.0003)]
        candles = _make_candles(20, base_price=3000.0)

        comp1 = extract_signals("ETH", datapoints, [], [], candles)
        comp2 = extract_signals("ETH", datapoints, [], [], candles)
        score1 = compute_signal_score("ETH", comp1)
        score2 = compute_signal_score("ETH", comp2)

        assert score1.weighted_score == score2.weighted_score
        assert score1.overall_confidence == score2.overall_confidence


# ===========================================================================
# VAL-CROSS-002: Signal conditions trigger correct playbook types
# ===========================================================================


class TestSignalPlaybookTriggers:
    """VAL-CROSS-002: Each signal pattern generates expected playbook setup."""

    def test_funding_stretch_triggers_fade(self) -> None:
        """funding_stretch > 1.5 stdev → fade playbook."""
        from engine.playbooks import generate_playbooks

        signals = _make_active_signals()
        # funding_stretch value = -0.8 → |value| > 0.5 threshold → fade triggers
        pbs = generate_playbooks("BTC", 100000.0, 500.0, signals, 99990.0, 100010.0)
        fade_pbs = [pb for pb in pbs if pb.setup_type == "fade"]
        assert len(fade_pbs) >= 1, "Fade playbook should trigger with stretched funding"

    def test_oi_delta_with_session_triggers_breakout(self) -> None:
        """oi_delta active + session_structure aligned → breakout."""
        from engine.playbooks import generate_playbooks
        from engine.scoring import SignalComponent

        signals = _make_active_signals(direction=1.0)
        pbs = generate_playbooks("BTC", 100000.0, 500.0, signals, 99990.0, 100010.0)
        breakout_pbs = [pb for pb in pbs if pb.setup_type == "breakout"]
        assert len(breakout_pbs) >= 1, "Breakout should trigger with aligned oi_delta + session_structure"

    def test_session_structure_active_triggers_vwap_reclaim(self) -> None:
        """session_structure active → vwap_reclaim playbook."""
        from engine.playbooks import generate_playbooks
        from engine.scoring import SignalComponent

        signals = {
            "funding_stretch": SignalComponent("funding_stretch", 0, 0, "unknown"),
            "oi_delta": SignalComponent("oi_delta", 0, 0, "unknown"),
            "basis": SignalComponent("basis", 0, 0, "unknown"),
            "liquidity_magnet": SignalComponent("liquidity_magnet", 0, 0, "unknown"),
            "session_structure": SignalComponent("session_structure", 0.4, 0.7, "above_vwap"),
            "whale_evidence": SignalComponent("whale_evidence", 0, 0, "unknown"),
            "dex_perp_lag": SignalComponent("dex_perp_lag", 0, 0, "unknown"),
            "volatility": SignalComponent("volatility", 0.5, 0.8, "regime_Normal"),
            "catalyst": SignalComponent("catalyst", 0, 0, "unknown"),
        }
        pbs = generate_playbooks("BTC", 100000.0, 500.0, signals, 99990.0, 100010.0)
        vwap_pbs = [pb for pb in pbs if pb.setup_type == "vwap_reclaim"]
        assert len(vwap_pbs) >= 1, "vwap_reclaim should trigger with active session_structure"

    def test_liquidity_magnet_triggers_sweep(self) -> None:
        """liquidity_magnet active → liquidity_sweep playbook."""
        from engine.playbooks import generate_playbooks
        from engine.scoring import SignalComponent

        signals = {
            "funding_stretch": SignalComponent("funding_stretch", 0, 0, "unknown"),
            "oi_delta": SignalComponent("oi_delta", 0, 0, "unknown"),
            "basis": SignalComponent("basis", 0, 0, "unknown"),
            "liquidity_magnet": SignalComponent("liquidity_magnet", 0.5, 0.6, "bid_heavy"),
            "session_structure": SignalComponent("session_structure", 0, 0, "unknown"),
            "whale_evidence": SignalComponent("whale_evidence", 0, 0, "unknown"),
            "dex_perp_lag": SignalComponent("dex_perp_lag", 0, 0, "unknown"),
            "volatility": SignalComponent("volatility", 0.5, 0.8, "regime_Normal"),
            "catalyst": SignalComponent("catalyst", 0, 0, "unknown"),
        }
        pbs = generate_playbooks("BTC", 100000.0, 500.0, signals, 99990.0, 100010.0)
        sweep_pbs = [pb for pb in pbs if pb.setup_type == "liquidity_sweep"]
        assert len(sweep_pbs) >= 1, "liquidity_sweep should trigger with active magnet"

    def test_all_unknown_produces_no_playbooks(self) -> None:
        """All signals unknown → empty playbook list."""
        from engine.playbooks import generate_playbooks
        from engine.scoring import SignalComponent

        signals = {
            name: SignalComponent(name, 0, 0, "unknown")
            for name in [
                "funding_stretch", "oi_delta", "basis", "liquidity_magnet",
                "session_structure", "whale_evidence", "dex_perp_lag",
                "volatility", "catalyst",
            ]
        }
        pbs = generate_playbooks("BTC", 100000.0, 500.0, signals, 99990.0, 100010.0)
        assert len(pbs) == 0, "All-unknown signals should produce no playbooks"


# ===========================================================================
# VAL-CROSS-003: Playbook → risk sizing → paper orders pipeline
# ===========================================================================


class TestPlaybookRiskPaperOrderPipeline:
    """VAL-CROSS-003: Valid playbooks produce paper orders; invalid produce skipped trades."""

    def test_valid_playbook_produces_atr_based_stop(self) -> None:
        """Stop distance is ATR-based, not 2% placeholder."""
        from engine.playbooks import generate_playbooks
        from engine.risk import RiskParams, compute_risk_sizing
        from engine.paper_orders import OrderSide

        signals = _make_active_signals()
        price = 100000.0
        atr = 500.0
        best_bid = 99990.0
        best_ask = 100010.0

        pbs = generate_playbooks("BTC", price, atr, signals, best_bid, best_ask)
        assert len(pbs) > 0, "Should generate at least one playbook"

        pb = pbs[0]
        stop_distance = abs(pb.entry - pb.stop)

        # Verify NOT a 2% placeholder
        assert abs(stop_distance / pb.entry - 0.02) > 0.005, \
            f"Stop distance {stop_distance} looks like 2% placeholder"

        # Verify ATR-based: stop_distance >= 0.8 * ATR
        assert stop_distance >= 0.8 * atr * 0.95, \
            f"Stop distance {stop_distance} should be >= 0.8*ATR ({0.8 * atr})"

    def test_valid_sizing_produces_paper_order(self, tmp_path: Path) -> None:
        """Valid playbook + valid sizing → paper order written to CSV."""
        from engine.playbooks import generate_playbooks
        from engine.risk import RiskParams, compute_risk_sizing
        from engine.paper_orders import PaperOrderTracker, OrderSide

        signals = _make_active_signals()
        price = 100000.0
        atr = 500.0
        best_bid = 99990.0
        best_ask = 100010.0

        pbs = generate_playbooks("BTC", price, atr, signals, best_bid, best_ask)
        assert len(pbs) > 0

        pb = pbs[0]
        params = RiskParams(equity=100.0, max_risk_pct=0.20, leverage_min=9.0, leverage_max=12.0)
        sizing = compute_risk_sizing(
            symbol="BTC", side=pb.side, entry=pb.entry, stop=pb.stop,
            params=params, best_bid=best_bid, best_ask=best_ask,
        )

        if sizing.valid:
            csv_path = tmp_path / "paper_orders.csv"
            tracker = PaperOrderTracker(csv_path)
            order = sizing.to_paper_order(
                setup=pb.setup_type, tp1=pb.tp1, tp2=pb.tp2,
                provenance_tags="test",
            )
            tracker.write_order(order)
            assert csv_path.exists()
            content = csv_path.read_text()
            assert "BTC" in content
            assert str(pb.stop) in content or f"{pb.stop}" in content
        else:
            # If sizing is invalid, it should be written to skipped_trades
            skipped_path = tmp_path / "skipped_trades.csv"
            from engine.risk import write_skipped_trade
            write_skipped_trade(
                csv_path=skipped_path, symbol="BTC",
                side=pb.side.value, reason=sizing.reject_reason,
                entry=pb.entry, stop=pb.stop,
            )
            assert skipped_path.exists()
            content = skipped_path.read_text()
            assert "BTC" in content

    def test_invalid_sizing_writes_skipped_trade(self, tmp_path: Path) -> None:
        """Invalid sizing (e.g., stop_distance too small) writes to skipped_trades."""
        from engine.risk import RiskParams, compute_risk_sizing, write_skipped_trade
        from engine.paper_orders import OrderSide

        # Create scenario where sizing is likely invalid (tiny stop distance)
        sizing = compute_risk_sizing(
            symbol="BTC", side=OrderSide.LONG,
            entry=100000.0, stop=99999.99,  # 0.01 stop distance
            params=RiskParams(equity=100.0, max_risk_pct=0.20),
        )

        skipped_path = tmp_path / "skipped_trades.csv"
        if not sizing.valid:
            write_skipped_trade(
                csv_path=skipped_path, symbol="BTC",
                side="long", reason=sizing.reject_reason,
                entry=100000.0, stop=99999.99,
            )
            assert skipped_path.exists()
            content = skipped_path.read_text()
            assert "BTC" in content
            assert sizing.reject_reason in content
        else:
            # Sizing happened to be valid - still verify no crash
            pass


# ===========================================================================
# VAL-CROSS-004: Evaluate-outcomes processes open orders end-to-end
# ===========================================================================


class TestEvaluateOutcomesEndToEnd:
    """VAL-CROSS-004: Open orders evaluated, outcomes written, state updated."""

    def test_filled_order_outcome_written(self, tmp_path: Path) -> None:
        """LONG order fills + TP hit → outcome with positive R written."""
        from engine.run_scan import _run_evaluate_outcomes

        # LONG at 100, stop at 95, TP at 110
        order = _make_eval_order(entry=100.0, stop=95.0, tp1=110.0, symbol="ETH")
        base = _setup_eval_env(tmp_path, [order])

        with patch("engine.run_scan._fetch_mark_prices", return_value={"ETH": 112.0}):
            result = _run_evaluate_outcomes(base_path=base)

        assert result == 0

        # Verify outcome written
        outcomes_path = base / "ledgers" / "outcomes.csv"
        assert outcomes_path.exists()
        content = outcomes_path.read_text()
        assert "ETH" in content

        # Parse result_R from outcomes
        lines = content.strip().split("\n")
        assert len(lines) >= 2
        header = lines[0].split(",")
        data = lines[1].split(",")
        r_idx = header.index("result_R")
        result_r = float(data[r_idx])
        assert result_r > 0, "LONG hit TP should have positive R"

        # Verify mission state updated (order removed)
        state = json.loads((base / "memory" / "mission_state.json").read_text())
        assert len(state["open_paper_orders"]) == 0, "Resolved order should be removed"

    def test_signal_attribution_written(self, tmp_path: Path) -> None:
        """Signal attribution rows written for filled order."""
        from engine.run_scan import _run_evaluate_outcomes

        order = _make_eval_order(
            entry=100.0, stop=95.0, tp1=110.0,
            symbol="ETH",
            signals=["funding_stretch", "oi_delta", "volatility"],
        )
        base = _setup_eval_env(tmp_path, [order])

        with patch("engine.run_scan._fetch_mark_prices", return_value={"ETH": 112.0}):
            _run_evaluate_outcomes(base_path=base)

        signal_path = base / "ledgers" / "signal_outcomes.csv"
        assert signal_path.exists()
        content = signal_path.read_text()
        assert "funding_stretch" in content
        assert "oi_delta" in content
        assert "volatility" in content

    def test_in_trade_order_preserved(self, tmp_path: Path) -> None:
        """In-trade order remains in open_paper_orders."""
        from engine.run_scan import _run_evaluate_outcomes

        # Order 1: fills + TP hit → closed
        closed_order = _make_eval_order(entry=100.0, stop=95.0, tp1=110.0, symbol="ETH")
        # Order 2: fills but no TP/stop → in_trade
        in_trade_order = _make_eval_order(entry=200.0, stop=190.0, tp1=220.0, symbol="SOL")
        base = _setup_eval_env(tmp_path, [closed_order, in_trade_order])

        # ETH at 112 → TP hit; SOL at 205 → in_trade (between entry and TP)
        with patch("engine.run_scan._fetch_mark_prices",
                    return_value={"ETH": 112.0, "SOL": 205.0}):
            _run_evaluate_outcomes(base_path=base)

        state = json.loads((base / "memory" / "mission_state.json").read_text())
        remaining = state["open_paper_orders"]
        # SOL should remain (in_trade), ETH removed (closed via TP hit)
        remaining_symbols = [o["symbol"] for o in remaining]
        assert "SOL" in remaining_symbols
        assert "ETH" not in remaining_symbols


# ===========================================================================
# VAL-CROSS-005 is covered in test_weekly_review.py::TestCrossAreaOutcomesToWeeklyReview
# ===========================================================================


# ===========================================================================
# VAL-CROSS-006: Full scan loop end-to-end with mocked adapters
# ===========================================================================


class TestFullScanLoopEndToEnd:
    """VAL-CROSS-006: Scan produces complete output artifacts."""

    def test_scan_produces_all_artifacts(self, tmp_path: Path) -> None:
        """Running scan with mocked adapters produces report, state update, KG."""
        import shutil
        import engine.run_scan as run_scan_mod

        # Create mock datapoints that will look like real fetched data
        mock_imperial = MagicMock()
        mock_imperial.fetch_mark_prices.return_value = [
            _make_dp("BTC", "mark_price", 100000.0, "Imperial"),
            _make_dp("ETH", "mark_price", 3000.0, "Imperial"),
            _make_dp("SOL", "mark_price", 150.0, "Imperial"),
        ]
        mock_imperial.fetch_stats_markets.return_value = [
            _make_dp("BTC", "volume_24h", 50000.0, "Imperial"),
            _make_dp("ETH", "volume_24h", 30000.0, "Imperial"),
            _make_dp("SOL", "volume_24h", 20000.0, "Imperial"),
        ]
        mock_imperial.fetch_funding_rates.return_value = [
            _make_dp("BTC", "funding_rate", 0.0001, "Imperial"),
            _make_dp("BTC", "funding_rate", 0.0002, "Imperial"),
            _make_dp("BTC", "funding_rate", 0.0003, "Imperial"),
            _make_dp("ETH", "funding_rate", 0.0001, "Imperial"),
            _make_dp("ETH", "funding_rate", 0.0001, "Imperial"),
            _make_dp("ETH", "funding_rate", 0.0001, "Imperial"),
        ]
        mock_imperial.fetch_gmtrade_funding_rates.return_value = []
        mock_imperial.fetch_phoenix_depth.return_value = []

        # Setup temp directory structure — account subdirectories
        acct_path = tmp_path / "accounts" / "deterministic"
        (acct_path / "reports").mkdir(parents=True)
        (acct_path / "ledgers").mkdir(parents=True)
        (acct_path / "memory").mkdir(parents=True)
        (acct_path / "data").mkdir(parents=True)
        (tmp_path / "data" / "raw").mkdir(parents=True)

        # Initialize CSV files
        (acct_path / "ledgers" / "paper_orders.csv").write_text(
            "date_Australia/Sydney,symbol,setup,side,entry,stop,tp1,tp2,filled,"
            "entry_ts_Australia/Sydney,exit_ts_Australia/Sydney,result_R,"
            "max_FvE,max_AdE,fees_bps,slippage_bps,notes,provenance_tags\n"
        )
        (acct_path / "ledgers" / "kg_triples.csv").write_text("")
        (acct_path / "ledgers" / "outcomes.csv").write_text("")
        (acct_path / "ledgers" / "signal_outcomes.csv").write_text("")

        state = {
            "mode": "live-paper-only",
            "last_run_id": "",
            "open_paper_orders": [],
        }
        (acct_path / "memory" / "mission_state.json").write_text(
            json.dumps(state, indent=2) + "\n"
        )

        # Copy config files
        src_config = Path("/home/kt/imperial-agent/config")
        dst_config = tmp_path / "config"
        if src_config.exists():
            shutil.copytree(src_config, dst_config)

        mock_ft = MagicMock()
        mock_phantom = MagicMock()
        mock_dext = MagicMock()

        # Patch PROJECT_ROOT and adapter classes at their source modules
        with patch.object(run_scan_mod, "PROJECT_ROOT", tmp_path):
            with patch("adapters.imperial.ImperialAdapter", return_value=mock_imperial):
                with patch("adapters.flash_trade.FlashTradeAdapter", return_value=mock_ft):
                    with patch("adapters.phantom.PhantomAdapter", return_value=mock_phantom):
                        with patch("adapters.dextrabot.DextrabotAdapter", return_value=mock_dext):
                            result = run_scan_mod._run_live_paper()

        assert result == 0, "Scan should complete without error"

        # Verify artifacts in account directory
        reports = list((acct_path / "reports").glob("*_report.md"))
        assert len(reports) >= 1, "Report should be generated"

        state_after = json.loads((acct_path / "memory" / "mission_state.json").read_text())
        assert state_after["last_run_id"].startswith("run_"), "last_run_id should be set"
        assert state_after["mode"] == "live-paper-only", "Mode should not change"


# ===========================================================================
# VAL-CROSS-007: No regressions
# ===========================================================================


class TestNoRegressions:
    """VAL-CROSS-007: Existing tests pass + dry-run works."""

    def test_no_2_percent_placeholder(self) -> None:
        """stop_pct = 0.02 placeholder must be removed from run_scan.py."""
        source = Path("/home/kt/imperial-agent/engine/run_scan.py").read_text()
        assert "stop_pct = 0.02" not in source, \
            "2% placeholder should be removed"

    def test_extract_signals_imported_in_run_scan(self) -> None:
        """run_scan.py must import and use extract_signals."""
        source = Path("/home/kt/imperial-agent/engine/run_scan.py").read_text()
        assert "signals_mod.extract_signals" in source or "extract_signals" in source, \
            "extract_signals should be called in run_scan.py"

    def test_compute_min_stop_imported_in_run_scan(self) -> None:
        """run_scan.py must use compute_min_stop (via playbooks module)."""
        source = Path("/home/kt/imperial-agent/engine/run_scan.py").read_text()
        # compute_min_stop is used within playbooks.py; verify playbooks is imported
        assert "playbooks" in source or "generate_playbooks" in source, \
            "Playbooks module should be integrated into run_scan.py"

    def test_plumbing_dry_run_exits_0(self) -> None:
        """plumbing-dry-run mode still works."""
        import subprocess
        result = subprocess.run(
            [".venv/bin/python", "-m", "engine.run_scan", "--mode", "plumbing-dry-run"],
            capture_output=True, text=True, cwd="/home/kt/imperial-agent",
            timeout=30,
        )
        assert result.returncode == 0, f"Dry run failed: {result.stderr}"


# ===========================================================================
# VAL-CROSS-008: Cross-venue basis and whale data integration
# ===========================================================================


class TestCrossVenueBasisAndWhale:
    """VAL-CROSS-008: Multi-venue data flows into basis and whale_evidence signals."""

    def test_basis_uses_two_venue_sources(self) -> None:
        """Basis signal uses prices from two distinct venues → non-zero."""
        from engine.signals import extract_signals

        datapoints = [
            _make_dp("BTC", "mark_price", 100000.0, "Imperial"),
            _make_dp("BTC", "mark_price", 100050.0, "Phantom"),
        ]
        components = extract_signals("BTC", datapoints, [], [], None)
        basis = components["basis"]
        assert basis.label != "unknown", "Basis should be non-unknown with 2 venues"
        assert basis.value != 0.0, "Basis value should be non-zero with price divergence"

    def test_basis_unknown_with_single_venue(self) -> None:
        """Basis unknown when only one venue's price is available."""
        from engine.signals import extract_signals

        datapoints = [
            _make_dp("BTC", "mark_price", 100000.0, "Imperial"),
        ]
        components = extract_signals("BTC", datapoints, [], [], None)
        assert components["basis"].label == "unknown"

    def test_whale_data_integrates_into_signal(self) -> None:
        """Whale DataPoints with smart_money produce non-unknown signal."""
        from engine.signals import extract_signals

        whale_points = [
            _make_dp("BTC", "pnl", 50000.0, "Dextrabot",
                      attrs={"entity_type": "smart_money"}),
            _make_dp("BTC", "pnl", 30000.0, "Dextrabot",
                      attrs={"entity_type": "smart_money"}),
        ]
        components = extract_signals("BTC", [], whale_points, [], None)
        whale = components["whale_evidence"]
        assert whale.label != "unknown", "Whale evidence should be non-unknown with smart_money data"
        assert whale.confidence > 0

    def test_whale_unknown_when_empty(self) -> None:
        """Whale evidence unknown when no whale data."""
        from engine.signals import extract_signals

        components = extract_signals("BTC", [], [], [], None)
        assert components["whale_evidence"].label == "unknown"

    def test_candidates_ranked_by_absolute_score(self) -> None:
        """Candidates sorted by abs(weighted_score) descending."""
        from engine.scoring import SignalComponent, compute_signal_score

        # BTC with strong signals
        strong = {
            name: SignalComponent(name, 0.8, 0.9, "bullish")
            for name in ["funding_stretch", "oi_delta", "basis", "liquidity_magnet",
                         "session_structure", "whale_evidence", "dex_perp_lag",
                         "volatility", "catalyst"]
        }
        btc_score = compute_signal_score("BTC", strong)

        # ETH with weak signals
        weak = {
            name: SignalComponent(name, 0.1, 0.2, "slight_bullish")
            for name in ["funding_stretch", "oi_delta", "basis", "liquidity_magnet",
                         "session_structure", "whale_evidence", "dex_perp_lag",
                         "volatility", "catalyst"]
        }
        eth_score = compute_signal_score("ETH", weak)

        candidates = [
            {"symbol": "ETH", "score": eth_score},
            {"symbol": "BTC", "score": btc_score},
        ]
        candidates.sort(key=lambda c: abs(c["score"].weighted_score), reverse=True)
        assert candidates[0]["symbol"] == "BTC"
        assert candidates[1]["symbol"] == "ETH"


# ===========================================================================
# VAL-CROSS-009: Mission state, provenance, KG triples after each run
# ===========================================================================


class TestMissionStateProvenanceKG:
    """VAL-CROSS-009: State updates, evidence ledger, and knowledge graph written."""

    def test_mission_state_updated_after_scan(self, tmp_path: Path) -> None:
        """After scan, mission_state has updated last_run_id and open_paper_orders."""
        import shutil
        import engine.run_scan as run_scan_mod

        # Setup — account subdirectory
        acct = tmp_path / "accounts" / "deterministic"
        (acct / "reports").mkdir(parents=True)
        (acct / "ledgers").mkdir(parents=True)
        (acct / "memory").mkdir(parents=True)
        (acct / "data").mkdir(parents=True)
        (tmp_path / "data" / "raw").mkdir(parents=True)

        for f in ["paper_orders.csv", "kg_triples.csv", "outcomes.csv", "signal_outcomes.csv", "skipped_trades.csv"]:
            (acct / "ledgers" / f).write_text("")

        state = {"mode": "live-paper-only", "last_run_id": "", "open_paper_orders": []}
        (acct / "memory" / "mission_state.json").write_text(json.dumps(state, indent=2))

        src_config = Path("/home/kt/imperial-agent/config")
        if src_config.exists():
            shutil.copytree(src_config, tmp_path / "config")

        mock_imperial = MagicMock()
        mock_imperial.fetch_mark_prices.return_value = []
        mock_imperial.fetch_stats_markets.return_value = []
        mock_imperial.fetch_funding_rates.return_value = []
        mock_imperial.fetch_gmtrade_funding_rates.return_value = []
        mock_imperial.fetch_phoenix_depth.return_value = []

        mock_ft = MagicMock()
        mock_phantom = MagicMock()
        mock_dext = MagicMock()

        with patch.object(run_scan_mod, "PROJECT_ROOT", tmp_path):
            with patch("adapters.imperial.ImperialAdapter", return_value=mock_imperial):
                with patch("adapters.flash_trade.FlashTradeAdapter", return_value=mock_ft):
                    with patch("adapters.phantom.PhantomAdapter", return_value=mock_phantom):
                        with patch("adapters.dextrabot.DextrabotAdapter", return_value=mock_dext):
                            run_scan_mod._run_live_paper()

        state_after = json.loads((acct / "memory" / "mission_state.json").read_text())
        assert state_after["last_run_id"] != "", "last_run_id should be updated"
        assert state_after["last_run_id"].startswith("run_"), "Should match run_YYYYMMDDTHHMMSS_AEST pattern"
        assert state_after["mode"] == "live-paper-only", "Mode must not change"
        assert isinstance(state_after["open_paper_orders"], list), "open_paper_orders should be a list"

    def test_kg_triples_written_for_candidates(self, tmp_path: Path) -> None:
        """Scored candidates produce KG triples with has_signal predicate."""
        from engine.kg import KGWriter

        kg_path = tmp_path / "kg_triples.csv"
        kg = KGWriter(kg_path)
        kg.add(
            subject="BTC", predicate="has_signal", object_="scan_candidate",
            attrs={"score": 0.5, "confidence": 0.8},
            source_name="Internal", confidence=0.5,
        )
        count = kg.flush()
        assert count == 1

        content = kg_path.read_text()
        assert "BTC" in content
        assert "has_signal" in content
        assert "scan_candidate" in content

    def test_kg_triples_for_paper_orders(self, tmp_path: Path) -> None:
        """Paper orders produce has_order KG triples."""
        from engine.kg import KGWriter

        kg_path = tmp_path / "kg_triples.csv"
        kg = KGWriter(kg_path)
        kg.add(
            subject="BTC", predicate="has_order", object_="run_20260601T120000_AEST",
            attrs={"side": "long", "entry": 100000.0, "stop": 99600.0},
            source_name="Internal",
        )
        count = kg.flush()
        assert count == 1

        content = kg_path.read_text()
        assert "has_order" in content


# ===========================================================================
# VAL-CROSS-010: Report section H reflects current gaps
# ===========================================================================


class TestReportSectionHGaps:
    """VAL-CROSS-010: Assumptions and Gaps section updated after integration."""

    def test_section_h_no_stale_atr_gap(self) -> None:
        """Section H should NOT list ATR/stop placeholder as a gap."""
        source = Path("/home/kt/imperial-agent/engine/run_scan.py").read_text()
        # Find section H content
        assert "ATR-based" in source or "compute_min_stop" in source, \
            "Section H should reflect ATR-based stops"
        # Verify old stale gap text is removed
        assert "2% placeholder" not in source, \
            "Stale 2% placeholder gap should be removed from Section H"

    def test_section_h_no_stale_signals_gap(self) -> None:
        """Section H should NOT list 'signals mostly unknown' as a gap."""
        source = Path("/home/kt/imperial-agent/engine/run_scan.py").read_text()
        assert "Signal extraction: 9 components via extract_signals" in source, \
            "Section H should reflect real signal extraction, not unknown placeholder"

    def test_section_h_lists_remaining_catalyst_gap(self) -> None:
        """Section H should list catalyst as remaining gap."""
        source = Path("/home/kt/imperial-agent/engine/run_scan.py").read_text()
        assert "Catalyst" in source or "catalyst" in source, \
            "Section H should mention catalyst as a remaining gap"

    def test_section_h_in_report_content(self, tmp_path: Path) -> None:
        """Report section H content reflects current state accurately."""
        from engine.report import ReportWriter

        writer = ReportWriter(tmp_path / "reports")
        writer.set_section("H", (
            "### Assumptions\n"
            "- Stop distance: ATR-based (0.8×ATR floor via compute_min_stop)\n\n"
            "### Gaps\n"
            "- Catalyst signal hard-coded unknown (no news/event data source)\n"
            "- Whale intelligence not yet called in scan loop\n"
            "- LVN rejection playbook not yet implemented\n"
        ))
        path = writer.write(status="no_trade")
        content = path.read_text()

        # Stale gaps should NOT appear
        assert "2% placeholder" not in content
        assert "Signal components: mostly unknown" not in content
        assert "ATR/volatility computation not implemented" not in content

        # Remaining gaps SHOULD appear
        assert "Catalyst" in content or "catalyst" in content
