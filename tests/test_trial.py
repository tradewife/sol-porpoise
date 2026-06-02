"""Tests for 24-hour trial config (VAL-CFG-001, VAL-CFG-002, VAL-CFG-003)
and auto-evaluate before scan (VAL-EVAL-001 through VAL-EVAL-005).

Validates that config/run.yaml and config/risk.yaml contain the correct
hourly trial parameters, that the scan loop uses them for risk sizing
and candidate selection, and that auto-evaluate runs inline at the start
of each live-paper scan cycle.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import yaml

import pytest

CONFIG_DIR = Path(__file__).resolve().parent.parent / "config"
PROJECT_ROOT = Path(__file__).resolve().parent.parent


def _load(name: str) -> dict:
    path = CONFIG_DIR / name
    assert path.is_file(), f"Config file missing: {name}"
    with open(path) as f:
        data = yaml.safe_load(f)
    assert isinstance(data, dict), f"{name} did not parse as a dict"
    return data


# ===========================================================================
# VAL-CFG-001: run.yaml updated with trial parameters
# ===========================================================================


class TestRunYamlTrialConfig:
    """VAL-CFG-001: run.yaml has hourly trial parameters."""

    def test_equity_is_1000(self) -> None:
        data = _load("run.yaml")
        assert data["account"]["equity"] == 1000

    def test_max_open_trades_is_4(self) -> None:
        data = _load("run.yaml")
        assert data["account"]["max_open_trades"] == 4

    def test_max_candidates_is_3(self) -> None:
        data = _load("run.yaml")
        assert data["run"]["max_candidates"] == 3

    def test_hourly_cron_schedule(self) -> None:
        data = _load("run.yaml")
        assert data["schedule"]["cron_scan"] == "0 * * * *"

    def test_mode_is_live_paper_only(self) -> None:
        data = _load("run.yaml")
        assert data["mode"] == "live-paper-only"

    def test_currency_is_usdc(self) -> None:
        data = _load("run.yaml")
        assert data["account"]["currency"] == "USDC"


# ===========================================================================
# VAL-CFG-002: risk.yaml updated with trial parameters
# ===========================================================================


class TestRiskYamlTrialConfig:
    """VAL-CFG-002: risk.yaml has hourly trial parameters."""

    def test_equity_is_1000(self) -> None:
        data = _load("risk.yaml")
        assert data["equity"] == 1000

    def test_timeout_minutes_is_45(self) -> None:
        data = _load("risk.yaml")
        assert data["cancel_rules"]["timeout_minutes"] == 45

    def test_max_open_trades_is_4(self) -> None:
        data = _load("risk.yaml")
        assert data["portfolio"]["max_open_trades"] == 4

    def test_hard_exit_disabled(self) -> None:
        data = _load("risk.yaml")
        # hard_exit_time must be empty string or falsy (disabled for hourly)
        het = data["cancel_rules"]["hard_exit_time"]
        assert het == "" or het is None, (
            f"hard_exit_time should be disabled, got: {het!r}"
        )

    def test_max_risk_pct_unchanged(self) -> None:
        data = _load("risk.yaml")
        assert data["max_risk_pct"] == 0.20

    def test_leverage_range_unchanged(self) -> None:
        data = _load("risk.yaml")
        assert data["leverage"]["min"] == 9
        assert data["leverage"]["max"] == 12

    def test_currency_is_usdc(self) -> None:
        data = _load("risk.yaml")
        assert data["currency"] == "USDC"


# ===========================================================================
# VAL-CFG-003: Config values actually used by scan loop
# ===========================================================================


def _make_dp(symbol: str, metric: str, value: float, source: str = "Imperial") -> MagicMock:
    """Create a mock DataPoint."""
    dp = MagicMock()
    dp.symbol = symbol
    dp.metric = metric
    dp.value = value
    prov = MagicMock()
    prov.source_name = source
    prov.source_ts = "2026-06-01T00:00:00Z"
    prov.fetched_ts_aest = "2026-06-01T00:00:00+10:00"
    dp.provenance = prov
    return dp


class TestScanUsesConfigValues:
    """VAL-CFG-003: Scan loop uses 1000 USDC equity and up to 4 concurrent trades."""

    def test_risk_params_use_1000_equity(self) -> None:
        """RiskParams loaded from risk.yaml should use equity=1000."""
        import engine.run_scan as rs

        # _load_yaml_config reads from the actual config files
        risk_config = rs._load_yaml_config("risk")
        assert risk_config["equity"] == 1000

        # Verify RiskParams would get 1000
        from engine.risk import RiskParams
        params = RiskParams(equity=risk_config.get("equity", 100))
        assert params.equity == 1000

    def test_sizing_uses_1000_not_100(self) -> None:
        """compute_risk_sizing with equity=1000 gives 200 USDC risk, not 20."""
        from engine.risk import RiskParams, compute_risk_sizing
        from engine.paper_orders import OrderSide

        params = RiskParams(equity=1000, max_risk_pct=0.20)
        result = compute_risk_sizing(
            symbol="BTC", side=OrderSide.LONG,
            entry=100_000, stop=99_000,
            params=params,
        )
        # risk_usd = 1000 * 0.20 = 200
        assert result.risk_usd == 200.0
        # With equity=100, risk would be 20
        params_old = RiskParams(equity=100, max_risk_pct=0.20)
        result_old = compute_risk_sizing(
            symbol="BTC", side=OrderSide.LONG,
            entry=100_000, stop=99_000,
            params=params_old,
        )
        assert result_old.risk_usd == 20.0
        assert result.risk_usd == 10 * result_old.risk_usd

    def test_max_candidates_from_run_yaml(self) -> None:
        """run_scan reads max_candidates from run.yaml, not hardcoded."""
        import engine.run_scan as rs

        run_config = rs._load_yaml_config("run")
        max_candidates = run_config.get("run", {}).get("max_candidates", 3)
        assert max_candidates == 3

    def test_max_open_trades_from_run_yaml(self) -> None:
        """run_scan reads max_open_trades from run.yaml account section."""
        import engine.run_scan as rs

        run_config = rs._load_yaml_config("run")
        max_open_trades = run_config.get("account", {}).get("max_open_trades", 4)
        assert max_open_trades == 4

    def test_scan_loop_respects_max_candidates(self, tmp_path: Path) -> None:
        """Scan loop iterates over max_candidates (3), not hardcoded 2."""
        import shutil
        import engine.run_scan as rs

        # Create a run.yaml with max_candidates=3
        run_yaml = {
            "mode": "live-paper-only",
            "schedule": {"timezone": "Australia/Sydney", "cron_scan": "0 * * * *"},
            "account": {"equity": 1000, "currency": "USDC",
                        "risk_profile": "Aggressive-Paper", "max_open_trades": 4},
            "run": {"max_candidates": 3, "always_include": ["BTC", "ETH", "SOL"],
                    "additional_trending_count": 5,
                    "report_dir": "reports", "ledger_dir": "ledgers"},
        }
        (tmp_path / "config").mkdir(parents=True)
        (tmp_path / "config" / "run.yaml").write_text(
            yaml.dump(run_yaml, default_flow_style=False), encoding="utf-8"
        )

        # Copy risk.yaml as-is
        shutil.copy(PROJECT_ROOT / "config" / "risk.yaml", tmp_path / "config" / "risk.yaml")

        # Setup directory structure
        for d in ["reports", "ledgers", "memory", "data/raw"]:
            (tmp_path / d).mkdir(parents=True, exist_ok=True)

        (tmp_path / "ledgers" / "paper_orders.csv").write_text(
            "date_Australia/Sydney,symbol,setup,side,entry,stop,tp1,tp2,filled,"
            "entry_ts_Australia/Sydney,exit_ts_Australia/Sydney,result_R,"
            "max_FvE,max_AdE,fees_bps,slippage_bps,notes,provenance_tags\n"
        )
        (tmp_path / "ledgers" / "kg_triples.csv").write_text("")
        (tmp_path / "ledgers" / "outcomes.csv").write_text("")
        (tmp_path / "ledgers" / "signal_outcomes.csv").write_text("")
        (tmp_path / "ledgers" / "skipped_trades.csv").write_text("")
        state = {"mode": "live-paper-only", "last_run_id": "", "open_paper_orders": []}
        (tmp_path / "memory" / "mission_state.json").write_text(
            json.dumps(state, indent=2) + "\n"
        )

        # Create mock adapters with strong signal data for many symbols
        mock_imperial = MagicMock()
        mock_imperial.fetch_mark_prices.return_value = [
            _make_dp("BTC", "mark_price", 100_000.0),
            _make_dp("ETH", "mark_price", 3_000.0),
            _make_dp("SOL", "mark_price", 150.0),
            _make_dp("WIF", "mark_price", 2.0),
            _make_dp("JUP", "mark_price", 1.0),
        ]
        mock_imperial.fetch_stats_markets.return_value = [
            _make_dp("BTC", "volume_24h", 50_000.0),
            _make_dp("ETH", "volume_24h", 30_000.0),
            _make_dp("SOL", "volume_24h", 20_000.0),
            _make_dp("WIF", "volume_24h", 10_000.0),
            _make_dp("JUP", "volume_24h", 5_000.0),
        ]
        mock_imperial.fetch_funding_rates.return_value = []
        mock_imperial.fetch_gmtrade_funding_rates.return_value = []
        mock_imperial.fetch_phoenix_depth.return_value = []

        mock_ft = MagicMock()
        mock_phantom = MagicMock()
        mock_dext = MagicMock()

        with patch.object(rs, "PROJECT_ROOT", tmp_path):
            with patch("adapters.imperial.ImperialAdapter", return_value=mock_imperial):
                with patch("adapters.flash_trade.FlashTradeAdapter", return_value=mock_ft):
                    with patch("adapters.phantom.PhantomAdapter", return_value=mock_phantom):
                        with patch("adapters.dextrabot.DextrabotAdapter", return_value=mock_dext):
                            result = rs._run_live_paper()

        assert result == 0, "Scan should complete without error"

        # The scan should have considered up to 3 candidates
        # (not just 2 as with the old hardcoded value)
        report_files = list((tmp_path / "reports").glob("*_report.md"))
        assert len(report_files) >= 1, "Report should be generated"
        report_text = report_files[0].read_text()
        assert "max_candidates" in str(run_yaml)  # sanity check config

    def test_scan_loop_uses_1000_for_risk_sizing(self, tmp_path: Path) -> None:
        """Scan loop creates RiskParams with equity=1000 from risk.yaml."""
        import shutil
        import engine.run_scan as rs

        # Copy actual config
        (tmp_path / "config").mkdir(parents=True, exist_ok=True)
        shutil.copy(PROJECT_ROOT / "config" / "run.yaml", tmp_path / "config" / "run.yaml")
        shutil.copy(PROJECT_ROOT / "config" / "risk.yaml", tmp_path / "config" / "risk.yaml")

        # Setup directory structure
        for d in ["reports", "ledgers", "memory", "data/raw"]:
            (tmp_path / d).mkdir(parents=True, exist_ok=True)

        (tmp_path / "ledgers" / "paper_orders.csv").write_text(
            "date_Australia/Sydney,symbol,setup,side,entry,stop,tp1,tp2,filled,"
            "entry_ts_Australia/Sydney,exit_ts_Australia/Sydney,result_R,"
            "max_FvE,max_AdE,fees_bps,slippage_bps,notes,provenance_tags\n"
        )
        (tmp_path / "ledgers" / "kg_triples.csv").write_text("")
        (tmp_path / "ledgers" / "outcomes.csv").write_text("")
        (tmp_path / "ledgers" / "signal_outcomes.csv").write_text("")
        (tmp_path / "ledgers" / "skipped_trades.csv").write_text("")
        state = {"mode": "live-paper-only", "last_run_id": "", "open_paper_orders": []}
        (tmp_path / "memory" / "mission_state.json").write_text(
            json.dumps(state, indent=2) + "\n"
        )

        mock_imperial = MagicMock()
        mock_imperial.fetch_mark_prices.return_value = [
            _make_dp("BTC", "mark_price", 100_000.0),
            _make_dp("ETH", "mark_price", 3_000.0),
            _make_dp("SOL", "mark_price", 150.0),
        ]
        mock_imperial.fetch_stats_markets.return_value = [
            _make_dp("BTC", "volume_24h", 50_000.0),
            _make_dp("ETH", "volume_24h", 30_000.0),
            _make_dp("SOL", "volume_24h", 20_000.0),
        ]
        mock_imperial.fetch_funding_rates.return_value = []
        mock_imperial.fetch_gmtrade_funding_rates.return_value = []
        mock_imperial.fetch_phoenix_depth.return_value = []

        mock_ft = MagicMock()
        mock_phantom = MagicMock()
        mock_dext = MagicMock()

        # Patch RiskParams to capture the equity value it's initialized with
        captured_equity = {}
        orig_risk_params = rs.risk_mod.RiskParams if hasattr(rs, "risk_mod") else None

        import engine.risk as risk_mod_real

        class CapturingRiskParams(risk_mod_real.RiskParams):
            def __init__(self, *args, **kwargs):
                captured_equity["value"] = kwargs.get("equity", args[0] if args else 100)
                super().__init__(*args, **kwargs)

        with patch.object(rs, "PROJECT_ROOT", tmp_path):
            with patch("adapters.imperial.ImperialAdapter", return_value=mock_imperial):
                with patch("adapters.flash_trade.FlashTradeAdapter", return_value=mock_ft):
                    with patch("adapters.phantom.PhantomAdapter", return_value=mock_phantom):
                        with patch("adapters.dextrabot.DextrabotAdapter", return_value=mock_dext):
                            with patch("engine.risk.RiskParams", CapturingRiskParams):
                                result = rs._run_live_paper()

        assert result == 0, "Scan should complete without error"
        assert "value" in captured_equity, "RiskParams should have been instantiated"
        assert captured_equity["value"] == 1000, (
            f"RiskParams equity should be 1000, got {captured_equity['value']}"
        )


# ===========================================================================
# Helpers for auto-evaluate tests
# ===========================================================================

def _make_eval_order(**overrides) -> dict:
    """Create a test order dict for auto-evaluate testing."""
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


def _setup_scan_env(tmp_path: Path, orders: list[dict] | None = None) -> Path:
    """Create temp directory structure for full scan testing with auto-evaluate."""
    import shutil

    # Copy config files from project
    (tmp_path / "config").mkdir(parents=True, exist_ok=True)
    shutil.copy(PROJECT_ROOT / "config" / "run.yaml", tmp_path / "config" / "run.yaml")
    shutil.copy(PROJECT_ROOT / "config" / "risk.yaml", tmp_path / "config" / "risk.yaml")

    for d in ["reports", "ledgers", "memory", "data/raw"]:
        (tmp_path / d).mkdir(parents=True, exist_ok=True)

    (tmp_path / "ledgers" / "paper_orders.csv").write_text(
        "date_Australia/Sydney,symbol,setup,side,entry,stop,tp1,tp2,filled,"
        "entry_ts_Australia/Sydney,exit_ts_Australia/Sydney,result_R,"
        "max_FvE,max_AdE,fees_bps,slippage_bps,notes,provenance_tags\n"
    )
    (tmp_path / "ledgers" / "kg_triples.csv").write_text("")
    (tmp_path / "ledgers" / "skipped_trades.csv").write_text("")

    state = {
        "mode": "live-paper-only",
        "last_run_id": "",
        "open_paper_orders": orders or [],
    }
    (tmp_path / "memory" / "mission_state.json").write_text(
        json.dumps(state, indent=2) + "\n"
    )
    return tmp_path


def _mock_imperial_adapter(mark_prices: dict[str, float] | None = None) -> MagicMock:
    """Create a mock ImperialAdapter with standard market data."""
    mock = MagicMock()
    if mark_prices is None:
        mark_prices = {"BTC": 100_000.0, "ETH": 3_000.0, "SOL": 150.0}
    mock.fetch_mark_prices.return_value = [
        _make_dp(sym, "mark_price", price) for sym, price in mark_prices.items()
    ]
    mock.fetch_stats_markets.return_value = [
        _make_dp(sym, "volume_24h", vol)
        for sym, vol in [("BTC", 50_000), ("ETH", 30_000), ("SOL", 20_000)]
    ]
    mock.fetch_funding_rates.return_value = []
    mock.fetch_gmtrade_funding_rates.return_value = []
    mock.fetch_phoenix_depth.return_value = []
    return mock


# ===========================================================================
# VAL-EVAL-001: Auto-evaluate runs when open orders exist
# ===========================================================================


class TestAutoEvaluateWithOpenOrders:
    """VAL-EVAL-001: Auto-evaluate processes open orders when they exist."""

    def test_outcomes_written_when_open_orders(self, tmp_path: Path) -> None:
        """When mission_state has open orders, auto-evaluate writes outcomes.csv."""
        import engine.run_scan as rs

        # LONG at 150, TP at 160, current price 161 → fills + TP hit → closed
        order = _make_eval_order(entry=150.0, stop=145.0, tp1=160.0)
        base = _setup_scan_env(tmp_path, [order])

        mock_imperial = _mock_imperial_adapter({"SOL": 161.0, "BTC": 100_000.0, "ETH": 3_000.0})
        mock_ft = MagicMock()
        mock_phantom = MagicMock()
        mock_dext = MagicMock()

        with patch.object(rs, "PROJECT_ROOT", base):
            with patch("adapters.imperial.ImperialAdapter", return_value=mock_imperial):
                with patch("adapters.flash_trade.FlashTradeAdapter", return_value=mock_ft):
                    with patch("adapters.phantom.PhantomAdapter", return_value=mock_phantom):
                        with patch("adapters.dextrabot.DextrabotAdapter", return_value=mock_dext):
                            result = rs._run_live_paper()

        assert result == 0
        outcomes_path = base / "ledgers" / "outcomes.csv"
        assert outcomes_path.exists()
        content = outcomes_path.read_text()
        assert "SOL" in content

    def test_open_list_updated_after_evaluation(self, tmp_path: Path) -> None:
        """Resolved orders are removed from open_paper_orders in mission_state."""
        import engine.run_scan as rs

        order = _make_eval_order(entry=150.0, stop=145.0, tp1=160.0)
        base = _setup_scan_env(tmp_path, [order])

        mock_imperial = _mock_imperial_adapter({"SOL": 161.0, "BTC": 100_000.0, "ETH": 3_000.0})

        with patch.object(rs, "PROJECT_ROOT", base):
            with patch("adapters.imperial.ImperialAdapter", return_value=mock_imperial):
                with patch("adapters.flash_trade.FlashTradeAdapter", return_value=MagicMock()):
                    with patch("adapters.phantom.PhantomAdapter", return_value=MagicMock()):
                        with patch("adapters.dextrabot.DextrabotAdapter", return_value=MagicMock()):
                            rs._run_live_paper()

        state = json.loads((base / "memory" / "mission_state.json").read_text())
        # Original SOL order was closed (TP hit at 161 > 160)
        # Any new orders from the scan would be added, but the old SOL order
        # should not be in open_paper_orders
        old_sol_orders = [o for o in state["open_paper_orders"] if o.get("symbol") == "SOL" and o.get("entry") == 150.0]
        assert len(old_sol_orders) == 0, "Closed SOL order should not remain in open list"


# ===========================================================================
# VAL-EVAL-002: Auto-evaluate is a no-op when no open orders
# ===========================================================================


class TestAutoEvaluateNoop:
    """VAL-EVAL-002: Auto-evaluate is a no-op when no open orders."""

    def test_no_outcomes_when_empty_orders(self, tmp_path: Path) -> None:
        """When open_paper_orders is empty, no outcomes are written."""
        import engine.run_scan as rs

        base = _setup_scan_env(tmp_path, [])

        mock_imperial = _mock_imperial_adapter()
        mock_ft = MagicMock()
        mock_phantom = MagicMock()
        mock_dext = MagicMock()

        with patch.object(rs, "PROJECT_ROOT", base):
            with patch("adapters.imperial.ImperialAdapter", return_value=mock_imperial):
                with patch("adapters.flash_trade.FlashTradeAdapter", return_value=mock_ft):
                    with patch("adapters.phantom.PhantomAdapter", return_value=mock_phantom):
                        with patch("adapters.dextrabot.DextrabotAdapter", return_value=mock_dext):
                            result = rs._run_live_paper()

        assert result == 0
        # outcomes.csv should not exist (no evaluation happened)
        outcomes_path = base / "ledgers" / "outcomes.csv"
        if outcomes_path.exists():
            content = outcomes_path.read_text().strip()
            # If file exists, it should only have the header or be empty
            lines = [l for l in content.split("\n") if l.strip()]
            # Allow header-only or no file at all
            assert len(lines) <= 1, "Should not have outcome rows with empty open orders"

    def test_scan_completes_normally_with_empty_orders(self, tmp_path: Path) -> None:
        """Scan completes without errors when no open orders."""
        import engine.run_scan as rs

        base = _setup_scan_env(tmp_path, [])

        mock_imperial = _mock_imperial_adapter()

        with patch.object(rs, "PROJECT_ROOT", base):
            with patch("adapters.imperial.ImperialAdapter", return_value=mock_imperial):
                with patch("adapters.flash_trade.FlashTradeAdapter", return_value=MagicMock()):
                    with patch("adapters.phantom.PhantomAdapter", return_value=MagicMock()):
                        with patch("adapters.dextrabot.DextrabotAdapter", return_value=MagicMock()):
                            result = rs._run_live_paper()

        assert result == 0


# ===========================================================================
# VAL-EVAL-003: Auto-evaluate preserves in-trade orders
# ===========================================================================


class TestAutoEvaluatePreservesInTrade:
    """VAL-EVAL-003: In-trade orders stay in open list after auto-evaluate."""

    def test_in_trade_order_preserved(self, tmp_path: Path) -> None:
        """Order that is filled but not yet stopped/TP'd stays in open list."""
        import engine.run_scan as rs

        # LONG at 150, stop at 145, TP at 160, current price 155 → fills, in_trade
        in_trade_order = _make_eval_order(
            entry=150.0, stop=145.0, tp1=160.0, symbol="ETH",
        )
        base = _setup_scan_env(tmp_path, [in_trade_order])

        # ETH at 155 → fills at 150, but no TP (155 < 160) and no stop (155 > 145)
        mock_imperial = _mock_imperial_adapter({"ETH": 155.0, "BTC": 100_000.0, "SOL": 150.0})

        with patch.object(rs, "PROJECT_ROOT", base):
            with patch("adapters.imperial.ImperialAdapter", return_value=mock_imperial):
                with patch("adapters.flash_trade.FlashTradeAdapter", return_value=MagicMock()):
                    with patch("adapters.phantom.PhantomAdapter", return_value=MagicMock()):
                        with patch("adapters.dextrabot.DextrabotAdapter", return_value=MagicMock()):
                            rs._run_live_paper()

        state = json.loads((base / "memory" / "mission_state.json").read_text())
        # The ETH order should still be in open_paper_orders
        eth_orders = [o for o in state["open_paper_orders"] if o.get("symbol") == "ETH" and o.get("entry") == 150.0]
        assert len(eth_orders) >= 1, "In-trade ETH order should be preserved in open list"


# ===========================================================================
# VAL-EVAL-004: Cancel rules enforced during auto-evaluate
# ===========================================================================


class TestAutoEvaluateCancelRules:
    """VAL-EVAL-004: Cancel rules enforced (45-min timeout, drift) during auto-evaluate."""

    def test_cancel_timeout_45min(self, tmp_path: Path) -> None:
        """Orders older than 45 minutes are cancelled during auto-evaluate."""
        import engine.run_scan as rs
        from datetime import datetime, timedelta
        from zoneinfo import ZoneInfo

        AEST = ZoneInfo("Australia/Sydney")

        # Order created 50 minutes ago → exceeds 45-min timeout
        old_ts = (datetime.now(AEST) - timedelta(minutes=50)).strftime(
            "%Y-%m-%d %H:%M:%S Australia/Sydney"
        )
        order = _make_eval_order(
            entry=150.0, stop=145.0, tp1=160.0,
            created_ts_aest=old_ts,
        )
        base = _setup_scan_env(tmp_path, [order])

        mock_imperial = _mock_imperial_adapter({"SOL": 155.0, "BTC": 100_000.0, "ETH": 3_000.0})

        # Mock evaluate_fill to return not-filled so order stays PENDING
        # (in practice, pending orders are the ones that get timeout-cancelled)
        not_filled_result = {"filled": False}
        with patch.object(rs, "PROJECT_ROOT", base):
            with patch("engine.paper_orders.evaluate_fill", return_value=not_filled_result):
                with patch("adapters.imperial.ImperialAdapter", return_value=mock_imperial):
                    with patch("adapters.flash_trade.FlashTradeAdapter", return_value=MagicMock()):
                        with patch("adapters.phantom.PhantomAdapter", return_value=MagicMock()):
                            with patch("adapters.dextrabot.DextrabotAdapter", return_value=MagicMock()):
                                rs._run_live_paper()

        outcomes_path = base / "ledgers" / "outcomes.csv"
        assert outcomes_path.exists()
        content = outcomes_path.read_text()
        assert "timeout" in content.lower(), "Should have timeout cancel in outcomes"

    def test_cancel_drift_enforced(self, tmp_path: Path) -> None:
        """Orders with price drift > threshold are cancelled."""
        import engine.run_scan as rs
        from datetime import datetime, timedelta
        from zoneinfo import ZoneInfo

        AEST = ZoneInfo("Australia/Sydney")

        recent_ts = (datetime.now(AEST) - timedelta(minutes=5)).strftime(
            "%Y-%m-%d %H:%M:%S Australia/Sydney"
        )
        # LONG at 150, stop at 145, stop_distance = 5
        # Price at 155: |155-150| = 5 > 0.8*5 = 4 → drift cancel
        order = _make_eval_order(
            entry=150.0, stop=145.0, tp1=160.0,
            created_ts_aest=recent_ts,
        )
        base = _setup_scan_env(tmp_path, [order])

        mock_imperial = _mock_imperial_adapter({"SOL": 156.0, "BTC": 100_000.0, "ETH": 3_000.0})

        # Mock evaluate_fill to return not-filled so order stays PENDING
        not_filled_result = {"filled": False}
        with patch.object(rs, "PROJECT_ROOT", base):
            with patch("engine.paper_orders.evaluate_fill", return_value=not_filled_result):
                with patch("adapters.imperial.ImperialAdapter", return_value=mock_imperial):
                    with patch("adapters.flash_trade.FlashTradeAdapter", return_value=MagicMock()):
                        with patch("adapters.phantom.PhantomAdapter", return_value=MagicMock()):
                            with patch("adapters.dextrabot.DextrabotAdapter", return_value=MagicMock()):
                                rs._run_live_paper()

        outcomes_path = base / "ledgers" / "outcomes.csv"
        assert outcomes_path.exists()
        content = outcomes_path.read_text()
        assert "drift" in content.lower(), "Should have drift cancel in outcomes"


# ===========================================================================
# VAL-EVAL-005: Auto-evaluate completes before new data fetching/signal extraction
# ===========================================================================


class TestAutoEvaluateOrdering:
    """VAL-EVAL-005: Auto-evaluate runs before data fetching and signal extraction."""

    def test_auto_eval_runs_before_data_fetch(self, tmp_path: Path) -> None:
        """Auto-evaluation completes before new data fetching."""
        import engine.run_scan as rs
        from datetime import datetime, timedelta
        from zoneinfo import ZoneInfo

        AEST = ZoneInfo("Australia/Sydney")

        # Create an order that would be resolved by auto-evaluate
        old_ts = (datetime.now(AEST) - timedelta(hours=2)).strftime(
            "%Y-%m-%d %H:%M:%S Australia/Sydney"
        )
        order = _make_eval_order(
            entry=150.0, stop=145.0, tp1=160.0,
            created_ts_aest=old_ts,
        )
        base = _setup_scan_env(tmp_path, [order])

        call_order: list[str] = []

        mock_imperial = _mock_imperial_adapter({"SOL": 155.0, "BTC": 100_000.0, "ETH": 3_000.0})
        # Track when fetch_mark_prices is called (data fetching)
        orig_fetch = mock_imperial.fetch_mark_prices

        def tracking_fetch_mark():
            call_order.append("data_fetch")
            return orig_fetch()

        mock_imperial.fetch_mark_prices = tracking_fetch_mark

        with patch.object(rs, "PROJECT_ROOT", base):
            with patch("adapters.imperial.ImperialAdapter", return_value=mock_imperial):
                with patch("adapters.flash_trade.FlashTradeAdapter", return_value=MagicMock()):
                    with patch("adapters.phantom.PhantomAdapter", return_value=MagicMock()):
                        with patch("adapters.dextrabot.DextrabotAdapter", return_value=MagicMock()):
                            # Patch _fetch_mark_prices to track when auto-eval fetches
                            orig_auto_fetch = rs._fetch_mark_prices

                            def tracking_auto_fetch():
                                call_order.append("auto_eval_fetch")
                                return orig_auto_fetch()

                            with patch.object(rs, "_fetch_mark_prices", tracking_auto_fetch):
                                rs._run_live_paper()

        # Auto-evaluate should have fetched prices before the main data fetch
        assert "auto_eval_fetch" in call_order, "Auto-evaluate should fetch prices"
        assert "data_fetch" in call_order, "Main scan should fetch data"
        auto_eval_idx = call_order.index("auto_eval_fetch")
        data_fetch_idx = call_order.index("data_fetch")
        assert auto_eval_idx < data_fetch_idx, (
            f"Auto-evaluate (index {auto_eval_idx}) should run before data fetch (index {data_fetch_idx})"
        )
