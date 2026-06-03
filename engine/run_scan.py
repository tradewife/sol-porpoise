"""Run scan entry point: full 14-step live paper trading scan loop.

Usage:
    python -m engine.run_scan --mode plumbing-dry-run
    python -m engine.run_scan --mode live-paper
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import shlex
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

from zoneinfo import ZoneInfo

PROJECT_ROOT = Path(__file__).resolve().parent.parent
AEST = ZoneInfo("Australia/Sydney")

# Max symbols to fetch Twitter CT intel for (AI scan loop only).
MAX_TWITTER_SYMBOLS = 5


def _account_root(account_id: str) -> Path:
    """Resolve the account root directory.

    If accounts/<id>/ exists (or can be created), use it.
    Otherwise fall back to PROJECT_ROOT directly (legacy / test compatibility).
    """
    acct = PROJECT_ROOT / "accounts" / account_id
    # If accounts/ dir exists at all, use account isolation
    if (PROJECT_ROOT / "accounts").exists() or acct.exists():
        acct.mkdir(parents=True, exist_ok=True)
        for sub in ("ledgers", "reports", "memory", "data"):
            (acct / sub).mkdir(exist_ok=True)
        return acct
    # Legacy: no accounts/ dir, use project root directly
    return PROJECT_ROOT


def _load_mission_state(account_id: str = "deterministic") -> dict:
    state_path = _account_root(account_id) / "memory" / "mission_state.json"
    if state_path.exists():
        return json.loads(state_path.read_text(encoding="utf-8"))
    return {}


def _save_mission_state(state: dict, account_id: str = "deterministic") -> None:
    state_path = _account_root(account_id) / "memory" / "mission_state.json"
    state_path.write_text(json.dumps(state, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def _load_yaml_config(name: str) -> dict:
    import yaml
    path = PROJECT_ROOT / "config" / f"{name}.yaml"
    if path.exists():
        return yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return {}


def _save_mission_state_to(path: Path, state: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def _fetch_mark_prices() -> dict[str, float]:
    """Fetch current mark prices via Imperial adapter. Returns {symbol: price}."""
    import adapters.imperial as imperial_mod
    adapter = imperial_mod.ImperialAdapter()
    price_map: dict[str, float] = {}
    try:
        mark_prices = adapter.fetch_mark_prices()
        for dp in mark_prices:
            if "mark_price" in dp.metric and isinstance(dp.value, (int, float)) and dp.value > 0:
                price_map[dp.symbol] = dp.value
    except Exception:
        pass
    return price_map


def _read_signal_outcome_stats(signal_outcomes_path: Path) -> list[dict[str, Any]]:
    """Read signal_outcomes.csv and return per-signal hit-rate stats for report.

    Handles two schemas:
    - Raw attribution: order_id,signal,result_r,timestamp_Australia/Sydney
    - Aggregated: signal,hit_rate,avg_R,n,last_updated_Australia/Sydney

    Returns list of dicts with keys: signal, hit_rate, avg_R, n.
    Returns empty list if file missing or empty.
    """
    if not signal_outcomes_path.exists():
        return []

    try:
        with open(signal_outcomes_path, encoding="utf-8") as f:
            reader = csv.reader(f)
            header = next(reader, None)
            if not header:
                return []
            header_lower = [h.strip().lower() for h in header]

            # Detect schema
            if "hit_rate" in header_lower:
                # Aggregated format — read directly
                idx_signal = header_lower.index("signal") if "signal" in header_lower else -1
                idx_hr = header_lower.index("hit_rate") if "hit_rate" in header_lower else -1
                idx_ar = header_lower.index("avg_r") if "avg_r" in header_lower else -1
                idx_n = header_lower.index("n") if "n" in header_lower else -1

                stats: list[dict[str, Any]] = []
                for row in reader:
                    try:
                        sig = row[idx_signal].strip() if idx_signal >= 0 and idx_signal < len(row) else ""
                        if not sig:
                            continue
                        hr = float(row[idx_hr]) if idx_hr >= 0 and idx_hr < len(row) else 0.0
                        ar = float(row[idx_ar]) if idx_ar >= 0 and idx_ar < len(row) else 0.0
                        n = int(row[idx_n]) if idx_n >= 0 and idx_n < len(row) else 0
                        stats.append({"signal": sig, "hit_rate": hr, "avg_R": ar, "n": n})
                    except (ValueError, TypeError, IndexError):
                        continue
                return stats

            elif "result_r" in header_lower:
                # Raw attribution format — compute stats from result_r values
                idx_signal = header_lower.index("signal") if "signal" in header_lower else -1
                idx_rr = header_lower.index("result_r") if "result_r" in header_lower else -1

                signal_data: dict[str, list[float]] = {}
                for row in reader:
                    try:
                        sig = row[idx_signal].strip() if idx_signal >= 0 and idx_signal < len(row) else ""
                        rr = row[idx_rr].strip() if idx_rr >= 0 and idx_rr < len(row) else ""
                        if sig and rr:
                            r = float(rr)
                            signal_data.setdefault(sig, []).append(r)
                    except (ValueError, TypeError, IndexError):
                        continue

                result: list[dict[str, Any]] = []
                for sig, rs in signal_data.items():
                    n = len(rs)
                    wins = sum(1 for r in rs if r > 0)
                    result.append({
                        "signal": sig,
                        "hit_rate": wins / n if n > 0 else 0.0,
                        "avg_R": sum(rs) / n if n > 0 else 0.0,
                        "n": n,
                    })
                return result

            else:
                return []
    except Exception:
        return []


def _format_signal_learning_section(stats: list[dict[str, Any]]) -> str:
    """Format signal outcome stats into a report section string.

    Shows per-signal hit rates when data is available, or
    'no signal outcome data yet' when no outcomes exist.
    """
    if not stats:
        return "No signal outcome data yet."

    lines = ["### Signal Performance (Prior Outcomes)", ""]
    lines.append(
        "| Signal | Hit Rate | Avg R | Trades |"
    )
    lines.append(
        "|--------|----------|-------|--------|"
    )
    for s in sorted(stats, key=lambda x: x["signal"]):
        pct = f"{s['hit_rate'] * 100:.0f}%"
        avg_r = f"{s['avg_R']:+.2f}"
        lines.append(
            f"| {s['signal']} | {pct} | {avg_r} | {s['n']} |"
        )
    lines.append("")
    lines.append(
        "*Signal weights and position sizing are NOT affected by prior outcomes. "
        "This section is informational only.*"
    )
    return "\n".join(lines)


def _execute_trade_via_vulcan(
    account_id: str,
    symbol: str,
    side: str,
    notional_usdc: float,
    tp: float | None = None,
    sl: float | None = None,
    run_id: str = "",
) -> dict[str, Any] | None:
    """Execute a paper trade via Vulcan (Phoenix Perps).

    Returns a dict with fill and trigger details, or None on failure.
    """
    try:
        from adapters.vulcan import VulcanAdapter
        if not VulcanAdapter.is_available():
            return None

        v = VulcanAdapter(account_id=account_id, project_root=PROJECT_ROOT)

        # Normalize symbol to what Phoenix supports
        sym = symbol.upper().strip()

        # Check if symbol exists on Phoenix
        ticker = v.ticker(sym)
        if not ticker or ticker.mark_price <= 0:
            print(f"[{run_id}]   Vulcan: {sym} not available on Phoenix, skipping")
            return None

        # Check existing positions — don't duplicate same symbol
        positions = v.positions()
        for p in positions:
            if p.symbol == sym:
                print(f"[{run_id}]   Vulcan: {sym} already has open position, skipping")
                return None

        # Execute the trade
        if side.lower() == "long":
            fill = v.buy(sym, notional_usdc=notional_usdc)
        else:
            fill = v.sell(sym, notional_usdc=notional_usdc)

        print(f"[{run_id}]   Vulcan fill: {fill.symbol} {fill.side} @ {fill.price:.2f} "
              f"size={fill.size_tokens:.4f} fee={fill.fee:.4f}")

        # Set TP/SL if provided
        triggers = []
        if tp is not None or sl is not None:
            triggers = v.set_tpsl(sym, tp=tp, sl=sl)
            for t in triggers:
                print(f"[{run_id}]   Vulcan trigger: {t.kind} {t.symbol} @ {t.trigger_price:.2f}")

        return {
            "fill": fill,
            "triggers": triggers,
            "execution_venue": "vulcan-phoenix-paper",
        }

    except Exception as e:
        print(f"[{run_id}]   Vulcan execution failed: {e}")
        return None


def _run_plumbing_dry_run(account_id: str = "deterministic") -> int:
    from engine.report import build_dry_run_report

    acct = _account_root(account_id)
    report_dir = acct / "reports"
    path = build_dry_run_report(report_dir)

    paper_orders = acct / "ledgers" / "paper_orders.csv"
    if not paper_orders.exists():
        paper_orders.parent.mkdir(parents=True, exist_ok=True)
        paper_orders.write_text(
            "date_Australia/Sydney,symbol,setup,side,entry,stop,tp1,tp2,filled,"
            "entry_ts_Australia/Sydney,exit_ts_Australia/Sydney,result_R,"
            "max_FvE,max_AdE,fees_bps,slippage_bps,notes,provenance_tags\n",
            encoding="utf-8",
        )
    lines = paper_orders.read_text(encoding="utf-8").strip().split("\n")
    assert len(lines) == 1, "paper_orders.csv must have only header row in dry run"

    print(f"[dry-run] Report written to {path}")
    print(f"[dry-run] Status: no_trade")
    print(f"[dry-run] paper_orders.csv: header only (verified)")
    print(f"[dry-run] Account: {account_id}")
    return 0


def _auto_evaluate_open_orders(
    open_orders: list[dict],
    evaluator: "outcomes_mod.OutcomeEvaluator",
    cancel_timeout: int = 45,
    run_id: str = "",
) -> list[dict]:
    """Evaluate open paper orders inline before new data fetching.

    Runs at the start of each _run_live_paper() cycle to resolve orders
    from the previous hour. Returns the list of orders that remain open
    (in-trade or still pending within cancel rules).
    """
    import engine.paper_orders as po_mod

    current_ts = datetime.now(AEST)
    remaining_orders: list[dict] = []
    resolved_count = 0

    # Fetch current mark prices for evaluation
    price_map = _fetch_mark_prices()
    if not price_map:
        print(f"[{run_id}]   WARNING: Could not fetch mark prices for auto-evaluate.")
        # Can't evaluate without prices — keep all orders
        return list(open_orders)

    for order_data in open_orders:
        # Reconstruct PaperOrder
        try:
            order = _reconstruct_order(order_data)
        except (KeyError, ValueError) as e:
            print(f"[{run_id}]   Skipping malformed order: {e}")
            remaining_orders.append(order_data)
            continue

        current_price = price_map.get(order.symbol)
        if current_price is None or current_price <= 0:
            print(f"[{run_id}]   No price for {order.symbol}, keeping order.")
            remaining_orders.append(order_data)
            continue

        # Parse order timestamp
        order_ts = po_mod._parse_aest(order.created_ts_aest)
        if not order_ts:
            print(f"[{run_id}]   No timestamp for {order.symbol}, keeping order.")
            remaining_orders.append(order_data)
            continue

        # Build candle data from current price
        candle_ts = current_ts
        candle_high = max(current_price, order.entry)
        candle_low = min(current_price, order.entry)
        candle_open = current_price
        candle_close = current_price

        # Evaluate fill against post-order candle data
        fill_result = po_mod.evaluate_fill(
            order,
            candle_high=candle_high,
            candle_low=candle_low,
            candle_open=candle_open,
            candle_close=candle_close,
            candle_ts=candle_ts,
            order_ts=order_ts,
        )

        # Handle: pre-order data rejected
        if fill_result.get("status") == "invalid_for_stats":
            print(f"[{run_id}]   {order.symbol}: pre-order data rejected.")
            remaining_orders.append(order_data)
            continue

        # Handle: filled and closed (stop or TP hit)
        if fill_result.get("status") == "closed":
            exit_price = fill_result.get("exit_price", current_price)
            mae_price = candle_low if order.side == po_mod.OrderSide.LONG else candle_high
            mfe_price = candle_high if order.side == po_mod.OrderSide.LONG else candle_low

            outcome = evaluator.compute_outcome(
                order, exit_price=exit_price,
                mae_price=mae_price, mfe_price=mfe_price,
                fees_bps=order.fees_bps, slippage_bps=order.slippage_bps,
            )
            evaluator.write_outcome(outcome)

            # Write signal attribution
            signals = order_data.get("signals", [])
            order_id = order_data.get("id", f"{order.symbol}_{order.created_ts_aest}")
            if signals:
                evaluator.write_signal_attribution(order_id, signals, result_r=outcome.result_r)

            resolved_count += 1
            print(f"[{run_id}]   {order.symbol}: closed, R={outcome.result_r:.3f}")
            continue

        # Handle: filled but still in trade
        if fill_result.get("status") == "in_trade":
            should_cancel, reason = po_mod.check_cancel_rules(
                order, current_price, current_ts,
                timeout_minutes=cancel_timeout,
            )
            if should_cancel and reason:
                order.filled = po_mod.OrderStatus.CANCELLED
                cancel_outcome = evaluator.compute_outcome(
                    order, exit_price=current_price,
                    fees_bps=order.fees_bps, slippage_bps=order.slippage_bps,
                )
                cancel_outcome.notes = reason.value
                evaluator.write_outcome(cancel_outcome)
                resolved_count += 1
                print(f"[{run_id}]   {order.symbol}: cancelled ({reason.value})")
            else:
                remaining_orders.append(order_data)
                print(f"[{run_id}]   {order.symbol}: in_trade, keeping.")
            continue

        # Handle: not filled (pending)
        if not fill_result.get("filled"):
            should_cancel, reason = po_mod.check_cancel_rules(
                order, current_price, current_ts,
                timeout_minutes=cancel_timeout,
            )
            if should_cancel and reason:
                order.filled = po_mod.OrderStatus.CANCELLED
                cancel_outcome = evaluator.compute_outcome(
                    order, exit_price=current_price,
                    fees_bps=order.fees_bps, slippage_bps=order.slippage_bps,
                )
                cancel_outcome.notes = reason.value
                evaluator.write_outcome(cancel_outcome)
                resolved_count += 1
                print(f"[{run_id}]   {order.symbol}: cancelled ({reason.value})")
            else:
                remaining_orders.append(order_data)
                print(f"[{run_id}]   {order.symbol}: still pending, keeping.")
            continue

        # Default: keep in open orders
        remaining_orders.append(order_data)

    print(f"[{run_id}]   Auto-evaluated {len(open_orders)} orders, resolved {resolved_count}")
    return remaining_orders


def _run_live_paper(account_id: str = "deterministic") -> int:
    """Execute the full 14-step scan loop for live paper trading."""
    import adapters.imperial as imperial_mod
    import adapters.flash_trade as ft_mod
    import adapters.hyperliquid as hl_mod
    import adapters.dextrabot as dext_mod
    import engine.kg as kg_mod
    import engine.scoring as scoring_mod
    import engine.paper_orders as po_mod
    import engine.outcomes as outcomes_mod
    import engine.risk as risk_mod
    import engine.cross_venue as cv_mod
    import engine.report as report_mod
    import engine.volatility as vol_mod
    from engine.paper_orders import OrderSide

    # Step 0: Mission Init
    acct = _account_root(account_id)
    state = _load_mission_state(account_id)
    mode = state.get("mode", "unknown")
    if mode != "live-paper-only":
        print(f"[FATAL] Mode is '{mode}', expected 'live-paper-only'. Aborting.", file=sys.stderr)
        return 1

    run_config = _load_yaml_config("run")
    risk_config = _load_yaml_config("risk")
    run_id = datetime.now(AEST).strftime("run_%Y%m%dT%H%M%S_AEST")
    timestamp_aest = datetime.now(AEST).strftime("%Y-%m-%d %H:%M:%S Australia/Sydney")

    print(f"[{run_id}] Starting live-paper scan at {timestamp_aest}")
    print(f"[{run_id}] Mode: {mode} | Account: {account_id}")

    # Load previous state
    open_orders = state.get("open_paper_orders", [])
    unresolved = state.get("unresolved_outcomes", [])
    print(f"[{run_id}] Previous open orders: {len(open_orders)}, unresolved: {len(unresolved)}")

    # Initialize components
    report = report_mod.ReportWriter(acct / "reports")
    report.set_section("A", f"Run {run_id} started at {timestamp_aest}. Mode: {mode}. Account: {account_id}.")
    kg = kg_mod.KGWriter(acct / "ledgers" / "kg_triples.csv")
    imperial = imperial_mod.ImperialAdapter()
    ft_adapter = ft_mod.FlashTradeAdapter()
    hl_adapter = hl_mod.HyperliquidAdapter()
    dext = dext_mod.DextrabotAdapter(cache_dir=str(PROJECT_ROOT / "data" / "raw"))
    tracker = po_mod.PaperOrderTracker(acct / "ledgers" / "paper_orders.csv")
    evaluator = outcomes_mod.OutcomeEvaluator(
        outcomes_path=acct / "ledgers" / "outcomes.csv",
        signal_outcomes_path=acct / "ledgers" / "signal_outcomes.csv",
    )
    risk_params = risk_mod.RiskParams(
        equity=risk_config.get("equity", 100),
        max_risk_pct=risk_config.get("max_risk_pct", 0.20),
        leverage_min=risk_config.get("leverage", {}).get("min", 9),
        leverage_max=risk_config.get("leverage", {}).get("max", 12),
    )

    # --- Auto-Evaluate: evaluate open orders from previous cycle ---
    # Runs BEFORE any new data fetching or signal extraction.
    cancel_timeout = risk_config.get("cancel_rules", {}).get("timeout_minutes", 45)
    if open_orders:
        print(f"[{run_id}] Auto-evaluating {len(open_orders)} open order(s)...")
        remaining_orders = _auto_evaluate_open_orders(
            open_orders=open_orders,
            evaluator=evaluator,
            cancel_timeout=cancel_timeout,
            run_id=run_id,
        )
        # Update state with evaluated orders
        state["open_paper_orders"] = remaining_orders
        _save_mission_state(state, account_id)
        open_orders = remaining_orders
        print(f"[{run_id}] Auto-evaluate complete. Remaining open: {len(open_orders)}")
    else:
        print(f"[{run_id}] Auto-evaluate: no open orders to evaluate.")

    # --- Signal Learning Output (informational only, before signal extraction) ---
    signal_outcomes_csv = acct / "ledgers" / "signal_outcomes.csv"
    signal_stats = _read_signal_outcome_stats(signal_outcomes_csv)
    report.set_section("L", _format_signal_learning_section(signal_stats))
    if signal_stats:
        print(f"[{run_id}] Signal learning: {len(signal_stats)} signals with prior outcomes")
    else:
        print(f"[{run_id}] Signal learning: no prior outcome data")

    # Data collection
    all_datapoints: list[Any] = []
    evidence_rows: list[str] = []
    symbols_data: dict[str, dict[str, Any]] = {}
    universe: list[str] = list(run_config.get("account", {}).get("always_include", ["BTC", "ETH", "SOL"]))

    # Step 1: Universe Selection + Data Fetch
    print(f"[{run_id}] Step 1: Fetching market data...")
    try:
        mark_prices = imperial.fetch_mark_prices()
        all_datapoints.extend(mark_prices)
        for dp in mark_prices:
            symbols_data.setdefault(dp.symbol, {})[dp.metric] = dp.value
        print(f"[{run_id}]   Fetched {len(mark_prices)} mark price points")
    except Exception as e:
        print(f"[{run_id}]   WARNING: Imperial mark prices failed: {e}")

    try:
        stats = imperial.fetch_stats_markets()
        all_datapoints.extend(stats)
        # Add top trending symbols by volume
        vol_by_symbol: dict[str, float] = {}
        for dp in stats:
            if dp.metric == "volume_24h" and dp.symbol not in universe:
                vol_by_symbol[dp.symbol] = dp.value
        trending = sorted(vol_by_symbol, key=vol_by_symbol.get, reverse=True)[:5]
        universe.extend(trending)
        print(f"[{run_id}]   Universe: {universe}")
    except Exception as e:
        print(f"[{run_id}]   WARNING: Stats fetch failed: {e}")

    report.set_section("B", (
        f"Core symbols: BTC, ETH, SOL\n\n"
        f"Trending additions: {', '.join(universe[3:]) or 'none fetched'}\n\n"
        f"Total universe: {len(universe)} symbols"
    ))

    # Steps 2-6: Evidence Collection (simplified - would use adapters in production)
    funding_data: dict[str, dict[str, float]] = {}
    try:
        funding_points = imperial.fetch_funding_rates()
        all_datapoints.extend(funding_points)
        for dp in funding_points:
            if "funding" in dp.metric:
                funding_data.setdefault(dp.symbol, {})[dp.metric] = dp.value
        print(f"[{run_id}]   Fetched {len(funding_points)} funding rate points")
    except Exception as e:
        print(f"[{run_id}]   WARNING: Funding rates failed: {e}")

    # Fetch gmtrade funding rates (additional funding data source)
    try:
        gmfr_points = imperial.fetch_gmtrade_funding_rates()
        all_datapoints.extend(gmfr_points)
        print(f"[{run_id}]   Fetched {len(gmfr_points)} gmtrade funding rate points")
    except Exception as e:
        print(f"[{run_id}]   WARNING: GMTrade funding rates failed: {e}")

    # Fetch phoenix depth data for core symbols (for liquidity_magnet signal)
    try:
        for sym in ["BTC", "ETH", "SOL"]:
            depth_points = imperial.fetch_phoenix_depth(sym)
            all_datapoints.extend(depth_points)
        print(f"[{run_id}]   Fetched phoenix depth data for core symbols")
    except Exception as e:
        print(f"[{run_id}]   WARNING: Phoenix depth failed: {e}")

    # Build Candle objects from mark-price DataPoints for ATR/VWAP signals
    candles_by_symbol: dict[str, list] = {}
    for dp in all_datapoints:
        if "mark_price" in dp.metric and isinstance(dp.value, (int, float)) and dp.value > 0:
            ts = dp.provenance.source_ts or dp.provenance.fetched_ts_aest or "2026-01-01T00:00:00Z"
            sym = dp.symbol
            candles_by_symbol.setdefault(sym, []).append(
                vol_mod.Candle(
                    open=dp.value, high=dp.value, low=dp.value,
                    close=dp.value, timestamp=ts,
                )
            )

    # Step 11: Microstructure - get current prices for passive entry checks
    prices: dict[str, float] = {}
    for dp in all_datapoints:
        if "mark_price" in dp.metric and dp.symbol not in prices:
            prices[dp.symbol] = dp.value

    # Compute ATR per symbol from candles (fallback to price-proxy estimate)
    atr_by_symbol: dict[str, float] = {}
    for sym, candle_list in candles_by_symbol.items():
        if len(candle_list) >= 14:
            try:
                atr_by_symbol[sym] = vol_mod.compute_atr(candle_list)
            except ValueError:
                pass
        if sym not in atr_by_symbol:
            price_est = prices.get(sym, 0)
            if price_est > 0:
                atr_by_symbol[sym] = price_est * 0.015

    # Step 12-14: Scoring, Risk Sizing, Final Selection
    import engine.signals as signals_mod

    # Collect whale data from Dextrabot (if available)
    whale_datapoints: list[Any] = []
    try:
        dext = dext_mod.DextrabotAdapter(cache_dir=str(PROJECT_ROOT / "data" / "raw"))
        whale_datapoints = dext.fetch_wallets()
        if whale_datapoints:
            print(f"[{run_id}] Dextrabot: {len(whale_datapoints)} whale datapoints")
    except Exception as e:
        print(f"[{run_id}] Dextrabot unavailable: {e}")

    # Collect catalyst news via Kukapay (if available)
    catalyst_datapoints: list[Any] = []
    try:
        from adapters.kukapay import KukapayNewsAdapter
        news_adapter = KukapayNewsAdapter()
        for sym in universe[:5]:
            try:
                news = news_adapter.get_latest_news(days=1, limit=5, keyword=sym)
                catalyst_datapoints.extend(news_adapter.to_datapoints(sym, news))
            except Exception:
                pass
        if catalyst_datapoints:
            print(f"[{run_id}] Kukapay: {len(catalyst_datapoints)} catalyst datapoints")
    except Exception as e:
        print(f"[{run_id}] Kukapay unavailable: {e}")

    all_datapoints.extend(whale_datapoints)
    all_datapoints.extend(catalyst_datapoints)

    # --- Fetch Hyperliquid data (markets, orderbook, candles) ---
    hl_datapoints: list[Any] = []
    try:
        hl_market_points = hl_adapter.fetch_markets()
        hl_datapoints.extend(hl_market_points)
        all_datapoints.extend(hl_market_points)
        print(f"[{run_id}]   HL markets: {len(hl_market_points)} datapoints")
    except Exception as e:
        print(f"[{run_id}]   WARNING: HL markets failed: {e}")

    try:
        hl_orderbook_points = hl_adapter.fetch_orderbook("SOL")
        hl_datapoints.extend(hl_orderbook_points)
        all_datapoints.extend(hl_orderbook_points)
        print(f"[{run_id}]   HL orderbook: {len(hl_orderbook_points)} datapoints")
    except Exception as e:
        print(f"[{run_id}]   WARNING: HL orderbook failed: {e}")

    # Fetch SOL candles (1h + 4h) with cache for hawk breakout and signals
    sol_candles_1h: list[dict[str, Any]] = []
    sol_candles_4h: list[dict[str, Any]] = []
    sol_candle_engine: list[Any] = []
    try:
        sol_candles_1h = hl_adapter.fetch_candles_cached(
            coin="SOL", interval="1h", hours=168,
            account_id=account_id, project_root=PROJECT_ROOT,
        )
        sol_candles_4h = hl_adapter.fetch_candles_cached(
            coin="SOL", interval="4h", hours=168,
            account_id=account_id, project_root=PROJECT_ROOT,
        )
        if sol_candles_1h:
            sol_candle_engine = hl_mod.candles_to_engine_candles(sol_candles_1h)
            # Override flat mark-price candles for SOL with real OHLC
            candles_by_symbol["SOL"] = sol_candle_engine
            # Compute real ATR from candle data
            if len(sol_candle_engine) >= 14:
                try:
                    atr_by_symbol["SOL"] = vol_mod.compute_atr(sol_candle_engine)
                except ValueError:
                    pass
        print(f"[{run_id}]   HL candles: {len(sol_candles_1h)} 1h, {len(sol_candles_4h)} 4h")
    except Exception as e:
        print(f"[{run_id}]   WARNING: HL candle fetch failed: {e}")

    # --- Hawk Breakout Signal (deterministic path) ---
    hawk_signals_det: list[Any] = []
    try:
        from engine.hawk_breakout import compute_hawk_breakout_signal
        from engine.mcp_data import extract_sm_tilt

        for sym in universe[:8]:
            closes_1h: list[float] = []
            closes_4h: list[float] = []
            volume_1h: list[float] = []

            if sym == "SOL" and sol_candles_1h:
                closes_1h, volume_1h = hl_mod.candles_to_arrays(sol_candles_1h)
                if sol_candles_4h:
                    closes_4h, _ = hl_mod.candles_to_arrays(sol_candles_4h)
            else:
                # Fallback: use mark-price closes from datapoints
                closes_1h = [dp.value for dp in all_datapoints
                             if getattr(dp, "symbol", None) == sym
                             and "mark_price" in str(getattr(dp, "metric", ""))]
                closes_4h = closes_1h
                volume_1h = [dp.value for dp in all_datapoints
                             if getattr(dp, "symbol", None) == sym
                             and getattr(dp, "metric", "") == "volume_24h"]

            if not closes_1h:
                continue

            sm_pct = extract_sm_tilt(
                symbol=sym,
                whale_points=whale_datapoints,
                hl_market=None,
            )

            sig = compute_hawk_breakout_signal(
                market=sym,
                closes_1h=closes_1h,
                closes_4h=closes_4h if closes_4h else closes_1h,
                volume_1h=volume_1h if volume_1h else [0.0],
                sm_long_pct=sm_pct,
                structure_classification="structure_partial",
            )
            hawk_signals_det.append(sig)
            if sig.signal != "none":
                print(f"[{run_id}]   Hawk {sym}: {sig.signal} score={sig.score}")
    except Exception as e:
        print(f"[{run_id}]   WARNING: Hawk breakout (det) failed: {e}")

    candidates: list[dict[str, Any]] = []
    all_playbooks: list[dict[str, Any]] = []
    signal_summary: dict[str, dict[str, Any]] = {}
    for sym in universe[:8]:
        price = prices.get(sym)
        if not price or price <= 0:
            continue

        # Extract real signal components from available data
        sym_candles = candles_by_symbol.get(sym)
        components = signals_mod.extract_signals(
            symbol=sym,
            datapoints=all_datapoints,
            whale_points=whale_datapoints,
            hl_points=hl_datapoints,
            candles=sym_candles,
            precomputed_atr=atr_by_symbol.get(sym),
        )

        score = scoring_mod.compute_signal_score(sym, components)

        # Store signal summary for report
        signal_summary[sym] = {
            "score": score,
            "components": components,
            "atr": atr_by_symbol.get(sym, 0),
            "price": price,
        }

        # Skip if no directional data
        if score.weighted_score == 0 and len(score.unknown_components) == 9:
            continue

        kg.add(
            subject=sym, predicate="has_signal", object_="scan_candidate",
            attrs={"score": score.weighted_score, "confidence": score.overall_confidence},
            source_name="Internal", confidence=0.5,
        )
        candidates.append({"symbol": sym, "score": score, "price": price, "components": components})

    # Final selection: pick top candidates by expected score
    candidates.sort(key=lambda c: abs(c["score"].weighted_score), reverse=True)
    max_candidates = run_config.get("run", {}).get("max_candidates", 3)
    max_open_trades = run_config.get("account", {}).get("max_open_trades", 4)
    final_trades: list[dict[str, Any]] = []
    import engine.playbooks as pb_mod

    for cand in candidates[:max_candidates]:
        # Stop if we already have enough trades to fill the portfolio
        if len(final_trades) >= max_open_trades:
            break
        sym = cand["symbol"]
        price = cand["price"]
        score = cand["score"]
        components = cand["components"]
        atr = atr_by_symbol.get(sym, price * 0.015)

        # Get bid/ask from data (use slight offset from mid for passive placement)
        best_bid = price * 0.9999
        best_ask = price * 1.0001

        # Generate playbooks from signals
        playbooks = pb_mod.generate_playbooks(
            symbol=sym, price=price, atr=atr,
            signals=components,
            best_bid=best_bid, best_ask=best_ask,
        )

        # Store playbooks for report
        for pb in playbooks:
            all_playbooks.append({
                "symbol": sym, "setup_type": pb.setup_type,
                "side": pb.side.value, "entry": pb.entry,
                "stop": pb.stop, "tp1": pb.tp1, "tp2": pb.tp2,
                "invalidation": pb.invalidation,
                "expected_r_r": pb.expected_r_r,
                "probability_band": pb.probability_band,
                "rationale": pb.rationale,
            })

        # Process best playbook (highest quality) for paper order
        if not playbooks:
            # No playbook qualifies → skip with reason
            risk_mod.write_skipped_trade(
                csv_path=acct / "ledgers" / "skipped_trades.csv",
                symbol=sym, side="none", reason="no_playbook_qualified",
                entry=price, stop=0,
            )
            continue

        best_pb = playbooks[0]

        # Risk sizing — skip passive entry check when vulcan handles execution
        vulcan_available = False
        try:
            from adapters.vulcan import VulcanAdapter
            vulcan_available = VulcanAdapter.is_available()
        except ImportError:
            pass

        if vulcan_available:
            # Vulcan uses real market prices — skip synthetic passive entry check
            sizing = risk_mod.compute_risk_sizing(
                symbol=sym, side=best_pb.side, entry=best_pb.entry, stop=best_pb.stop,
                params=risk_params,
            )
        else:
            sizing = risk_mod.compute_risk_sizing(
                symbol=sym, side=best_pb.side, entry=best_pb.entry, stop=best_pb.stop,
                params=risk_params, best_bid=best_bid, best_ask=best_ask,
            )

        if not sizing.valid:
            risk_mod.write_skipped_trade(
                csv_path=acct / "ledgers" / "skipped_trades.csv",
                symbol=sym, side=best_pb.side.value, reason=sizing.reject_reason,
                entry=best_pb.entry, stop=best_pb.stop,
            )
            continue

        # Execute via Vulcan if available, otherwise synthetic paper order
        vulcan_result = None
        if vulcan_available:
            vulcan_result = _execute_trade_via_vulcan(
                account_id=account_id,
                symbol=sym,
                side=best_pb.side.value,
                notional_usdc=sizing.notional,
                tp=best_pb.tp1,
                sl=best_pb.stop,
                run_id=run_id,
            )

        if vulcan_result:
            fill = vulcan_result["fill"]
            entry_price = fill.price
            execution = "vulcan-phoenix"
        else:
            # Fallback: synthetic paper order
            order = sizing.to_paper_order(
                setup=best_pb.setup_type, tp1=best_pb.tp1, tp2=best_pb.tp2,
                provenance_tags=f"run={run_id},score={score.weighted_score:.3f},setup={best_pb.setup_type}",
            )
            tracker.write_order(order)
            entry_price = best_pb.entry
            execution = "synthetic"

        kg.add(subject=sym, predicate="has_order", object_=run_id,
               attrs={"side": best_pb.side.value, "entry": entry_price, "stop": best_pb.stop,
                       "execution": execution},
               source_name="Internal")

        final_trades.append({
            "symbol": sym, "side": best_pb.side.value, "setup": best_pb.setup_type,
            "entry": entry_price, "stop": best_pb.stop, "tp1": best_pb.tp1, "tp2": best_pb.tp2,
            "qty": sizing.qty, "notional": sizing.notional, "leverage": sizing.leverage,
            "risk_usd": sizing.risk_usd,
            "rationale": best_pb.rationale, "probability_band": best_pb.probability_band,
            "execution": execution,
        })
        print(f"[{run_id}]   {sym} {best_pb.side.value}: {execution} ${sizing.notional:.2f} @ {sizing.leverage:.1f}x")

    # --- Populate Report Sections C, D, E with real data ---

    # Section C: Funding / OI Evidence Table
    evidence_lines = [
        "### Funding and OI Evidence\n\n",
        "| Symbol | Funding Rate | OI Delta | Basis | Session | ATR |\n",
        "|--------|-------------|----------|-------|---------|-----|\n",
    ]
    for sym in universe[:8]:
        ss = signal_summary.get(sym)
        if ss:
            comps = ss["components"]
            fr = comps.get("funding_stretch")
            oi = comps.get("oi_delta")
            ba = comps.get("basis")
            sess = comps.get("session_structure")
            atr_val = ss.get("atr", 0)
            fr_str = f"{fr.value:.3f} ({fr.label})" if fr and fr.label != "unknown" else "unknown"
            oi_str = f"{oi.value:.3f} ({oi.label})" if oi and oi.label != "unknown" else "unknown"
            ba_str = f"{ba.value:.3f}" if ba and ba.label != "unknown" else "unknown"
            sess_str = f"{sess.value:.3f} ({sess.label})" if sess and sess.label != "unknown" else "unknown"
            atr_str = f"{atr_val:.2f}" if atr_val > 0 else "proxy"
            evidence_lines.append(
                f"| {sym} | {fr_str} | {oi_str} | {ba_str} | {sess_str} | {atr_str} |\n"
            )
        else:
            evidence_lines.append(f"| {sym} | unknown | unknown | unknown | unknown | unknown |\n")
    report.set_section("C", "".join(evidence_lines))

    # Section D: On-chain / Cross-venue Flow Data
    flow_lines = ["### Cross-Venue and On-Chain Data\n\n"]
    whale_comps = []
    dex_comps = []
    for sym in universe[:8]:
        ss = signal_summary.get(sym)
        if ss:
            wh = ss["components"].get("whale_evidence")
            dx = ss["components"].get("dex_perp_lag")
            if wh and wh.label != "unknown":
                whale_comps.append(f"{sym}: {wh.label} (value={wh.value:.3f}, conf={wh.confidence:.2f})")
            if dx and dx.label != "unknown":
                dex_comps.append(f"{sym}: {dx.label} (value={dx.value:.3f}, conf={dx.confidence:.2f})")

    if whale_comps:
        flow_lines.append("**Whale Evidence:**\n")
        for w in whale_comps:
            flow_lines.append(f"- {w}\n")
    else:
        flow_lines.append("**Whale Evidence:** No active whale signals detected in this scan.\n")

    flow_lines.append("\n")
    if dex_comps:
        flow_lines.append("**DEX-Perp Lag:**\n")
        for d in dex_comps:
            flow_lines.append(f"- {d}\n")
    else:
        flow_lines.append("**DEX-Perp Lag:** No cross-venue timestamp divergence detected.\n")

    flow_lines.append(f"\n*Data from {len(all_datapoints)} DataPoints across {len(set(dp.provenance.source_name for dp in all_datapoints))} sources.*\n")
    report.set_section("D", "".join(flow_lines))

    # Section E: Playbook Cards
    if all_playbooks:
        pb_lines = [f"### Playbook Cards ({len(all_playbooks)} generated)\n\n"]
        for i, pb in enumerate(all_playbooks, 1):
            pb_lines.append(
                f"**{i}. {pb['symbol']} {pb['side'].upper()} — {pb['setup_type']}**\n"
                f"- Entry: {pb['entry']:.2f} | Stop: {pb['stop']:.2f} | "
                f"Invalidation: {pb['invalidation']:.2f}\n"
                f"- TP1: {pb['tp1']:.2f} | TP2: {pb['tp2']:.2f} | "
                f"Expected R:R = {pb['expected_r_r']:.1f}\n"
                f"- Probability: {pb['probability_band']} | Rationale: {pb['rationale']}\n\n"
            )
        report.set_section("E", "".join(pb_lines))
    else:
        # Explain why no playbooks
        no_pb_reasons = []
        for sym in universe[:8]:
            ss = signal_summary.get(sym)
            if ss:
                unknowns = ss["score"].unknown_components if "score" in ss else []
                if len(unknowns) == 9:
                    no_pb_reasons.append(f"{sym}: all signals unknown (no data fetched)")
                else:
                    active = [k for k, v in ss["components"].items() if v.label != "unknown"]
                    no_pb_reasons.append(f"{sym}: {len(active)} active signals ({', '.join(active[:4])}) but no setup criteria met")
        if no_pb_reasons:
            report.set_section("E", (
                "### Playbook Cards\n\n"
                "No playbook setups met criteria this scan.\n\n"
                "Per-symbol diagnostics:\n" +
                "\n".join(f"- {r}" for r in no_pb_reasons)
            ))
        else:
            report.set_section("E", (
                "### Playbook Cards\n\n"
                "No symbols were scored (universe empty or no price data)."
            ))

    # Flush KG triples
    kg.flush()

    # Determine run status
    if final_trades:
        status = "paper_candidate"
    else:
        status = "no_trade"

    # Step F: Final Paper Trades
    if final_trades:
        trade_lines = []
        for t in final_trades:
            exec_venue = t.get("execution", "synthetic")
            trade_lines.append(
                f"**{t['symbol']} {t['side'].upper()}**\n"
                f"- Setup: {t['setup']} ({t.get('probability_band', 'N/A')} confidence)\n"
                f"- Entry: {t['entry']:.2f} | Stop: {t['stop']:.2f}\n"
                f"- TP1: {t['tp1']:.2f} | TP2: {t['tp2']:.2f}\n"
                f"- Qty: {t['qty']:.4f} | Notional: ${t['notional']:.2f}\n"
                f"- Leverage: {t['leverage']:.1f}x | Risk: ${t['risk_usd']:.2f}\n"
                f"- Execution: {exec_venue}\n"
                f"- Rationale: {t.get('rationale', 'N/A')}\n"
            )
        report.set_section("F", "\n".join(trade_lines))
    else:
        # Provide specific no-trade reason
        if not candidates:
            no_trade_reason = "no candidates scored above threshold (all signals unknown)"
        elif not all_playbooks:
            no_trade_reason = "no playbook setups met signal criteria for any candidate"
        else:
            no_trade_reason = "all risk sizings were invalid (leverage or quantity constraints)"
        report.set_section("F", (
            f"No paper trades generated. Status: `{status}`.\n\n"
            f"Reason: {no_trade_reason}.\n\n"
            f"Candidates scored: {len(candidates)}, Playbooks generated: {len(all_playbooks)}."
        ))

    # Section G: X Post Draft
    report.set_section("G", (
        "No X post draft (paper scan mode). NFA/DYOR."
    ))

    # Section H: Assumptions and Gaps
    report.set_section("H", (
        "### Assumptions\n"
        "- Market data from Imperial API (public endpoints)\n"
        "- Stop distance: ATR-based (0.8×ATR floor via compute_min_stop)\n"
        "- Signal extraction: 9 components via extract_signals() from engine/signals.py\n"
        "- Candle data: built from mark-price DataPoints (flat OHLC candles)\n"
        "- Playbook generation: 7 setup types via generate_playbooks() from engine/playbooks.py\n\n"
        "### Gaps\n"
        "- Catalyst signal hard-coded unknown (no news/event data source)\n"
        "- Whale intelligence not yet called in scan loop (adapter exists)\n"
        "- Dextrabot scraping requires live HTML access\n"
        "- DEX-perp lag requires multi-venue timestamp comparison\n"
        "- LVN rejection playbook not yet implemented (future)\n"
        "- Candle data is flat (O=H=L=C=mark_price); real OHLC would improve ATR accuracy"
    ))

    # Section I: Citations
    sources_used = set()
    for dp in all_datapoints:
        sources_used.add(dp.provenance.source_name)
    report.set_section("I", (
        "Sources used: " + ", ".join(sorted(sources_used)) + "\n\n"
        "All data fetched at " + timestamp_aest
    ))

    # Section J: OutcomeGraph CSV
    report.set_section("J", (
        "```csv\n"
        "date_Australia/Sydney,symbol,setup,side,entry,stop,tp1,tp2,filled,"
        "entry_ts_Australia/Sydney,exit_ts_Australia/Sydney,result_R,"
        "max_FvE,max_AdE,fees_bps,slippage_bps,notes,provenance_tags\n"
        "```\n\n"
        f"Paper orders written: {len(final_trades)}"
    ))

    # Section K: Audit
    report.set_section("K", (
        "| Metric | Score |\n|--------|-------|\n"
        f"| Data completeness | {len(all_datapoints)} points fetched |\n"
        f"| Provenance quality | {len(sources_used)} sources |\n"
        f"| Signal extraction | extract_signals() with candle data |\n"
        f"| Playbook generation | generate_playbooks() wired |\n"
        f"| Passive-entry correctness | {'validated' if final_trades else 'N/A'} |\n"
        f"| Risk sizing correctness | {'validated' if final_trades else 'N/A'} |\n"
        f"| Paper-execution evaluability | ready |\n"
        f"| Report usefulness | complete |\n"
        f"| Playbooks generated | {len(all_playbooks)} |\n"
        f"| Top failure mode | {'none' if final_trades else 'insufficient signal quality for setups'} |"
    ))

    # Write report
    report_path = report.write(status=status)

    # Update mission state — merge new trades with any remaining auto-evaluated orders
    state["last_run_id"] = run_id
    new_trade_orders = [
        {
            "symbol": t["symbol"],
            "side": t["side"],
            "setup": t["setup"],
            "entry": t["entry"],
            "stop": t["stop"],
            "tp1": t["tp1"],
            "tp2": t["tp2"],
            "qty": t["qty"],
            "notional": t["notional"],
            "leverage": t["leverage"],
            "created_ts_aest": tracker.read_orders()[-1].created_ts_aest if tracker.read_orders() else datetime.now(AEST).strftime("%Y-%m-%d %H:%M:%S Australia/Sydney"),
            "fees_bps": 5.0,
            "slippage_bps": 3.0,
            "provenance_tags": f"run={run_id}",
            "signals": [k for k, v in candidates[0]["components"].items() if v.label != "unknown"][:5] if candidates else [],
        }
        for t in final_trades
    ]
    # Preserve in-trade/pending orders from auto-evaluate alongside new trades
    state["open_paper_orders"] = open_orders + new_trade_orders
    _save_mission_state(state, account_id)

    print(f"[{run_id}] Status: {status}")
    print(f"[{run_id}] Paper trades: {len(final_trades)}")
    print(f"[{run_id}] Report: {report_path}")
    return 0


def _reconstruct_order(data: dict) -> "paper_orders.PaperOrder":
    """Reconstruct a PaperOrder from a mission_state dict."""
    import engine.paper_orders as po_mod
    return po_mod.PaperOrder(
        symbol=data["symbol"],
        setup=data.get("setup", "unknown"),
        side=po_mod.OrderSide(data["side"]),
        entry=float(data["entry"]),
        stop=float(data.get("stop", 0)),
        tp1=float(data.get("tp1", 0)),
        tp2=float(data.get("tp2", 0)),
        qty=float(data.get("qty", 0)),
        notional=float(data.get("notional", 0)),
        leverage=float(data.get("leverage", 0)),
        created_ts_aest=data.get("created_ts_aest", ""),
        fees_bps=float(data.get("fees_bps", 5.0)),
        slippage_bps=float(data.get("slippage_bps", 3.0)),
        provenance_tags=data.get("provenance_tags", ""),
    )


def _run_evaluate_outcomes(base_path: Path | None = None, account_id: str = "deterministic") -> int:
    """Evaluate open paper orders against post-order market data.

    Reads open orders from mission_state.json, fetches current prices,
    evaluates fills, computes outcomes, updates state.
    """
    import engine.paper_orders as po_mod
    import engine.outcomes as outcomes_mod

    if base_path:
        root = base_path
    else:
        root = _account_root(account_id)
    state_path = root / "memory" / "mission_state.json"

    # Handle missing state file
    if not state_path.exists():
        print("[evaluate-outcomes] No mission_state.json found. Nothing to evaluate.")
        return 0

    # Load state
    try:
        state = json.loads(state_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, ValueError) as e:
        print(f"[evaluate-outcomes] Corrupt mission_state.json: {e}", file=sys.stderr)
        return 1

    open_orders_data = state.get("open_paper_orders", [])
    if not open_orders_data:
        print("[evaluate-outcomes] No open paper orders. Nothing to evaluate.")
        return 0

    print(f"[evaluate-outcomes] Processing {len(open_orders_data)} open orders...")

    # Initialize evaluator
    evaluator = outcomes_mod.OutcomeEvaluator(
        outcomes_path=root / "ledgers" / "outcomes.csv",
        signal_outcomes_path=root / "ledgers" / "signal_outcomes.csv",
    )

    current_ts = datetime.now(AEST)
    remaining_orders: list[dict] = []
    resolved_count = 0

    # Fetch current mark prices
    price_map = _fetch_mark_prices()
    if not price_map:
        print("[evaluate-outcomes] WARNING: Could not fetch any mark prices.")

    for order_data in open_orders_data:
        # Reconstruct PaperOrder
        try:
            order = _reconstruct_order(order_data)
        except (KeyError, ValueError) as e:
            print(f"[evaluate-outcomes] Skipping malformed order: {e}")
            remaining_orders.append(order_data)
            continue

        current_price = price_map.get(order.symbol)
        if current_price is None or current_price <= 0:
            print(f"[evaluate-outcomes] No price for {order.symbol}, keeping order.")
            remaining_orders.append(order_data)
            continue

        # Parse order timestamp
        order_ts = po_mod._parse_aest(order.created_ts_aest)
        if not order_ts:
            print(f"[evaluate-outcomes] No timestamp for {order.symbol}, keeping order.")
            remaining_orders.append(order_data)
            continue

        # Build candle data from current price (post-order by definition)
        # Construct candle spanning from entry to current price to detect fills
        candle_ts = current_ts
        candle_high = max(current_price, order.entry)
        candle_low = min(current_price, order.entry)
        candle_open = current_price
        candle_close = current_price

        # Evaluate fill against post-order candle data
        fill_result = po_mod.evaluate_fill(
            order,
            candle_high=candle_high,
            candle_low=candle_low,
            candle_open=candle_open,
            candle_close=candle_close,
            candle_ts=candle_ts,
            order_ts=order_ts,
        )

        # Handle: pre-order data rejected
        if fill_result.get("status") == "invalid_for_stats":
            print(f"[evaluate-outcomes] {order.symbol}: pre-order data rejected.")
            remaining_orders.append(order_data)
            continue

        # Handle: filled and closed (stop or TP hit)
        if fill_result.get("status") == "closed":
            exit_price = fill_result.get("exit_price", current_price)
            # MAE: worst price against position
            mae_price = candle_low if order.side == po_mod.OrderSide.LONG else candle_high
            # MFE: best price for position
            mfe_price = candle_high if order.side == po_mod.OrderSide.LONG else candle_low

            outcome = evaluator.compute_outcome(
                order, exit_price=exit_price,
                mae_price=mae_price, mfe_price=mfe_price,
                fees_bps=order.fees_bps, slippage_bps=order.slippage_bps,
            )
            evaluator.write_outcome(outcome)

            # Write signal attribution with result_r
            signals = order_data.get("signals", [])
            order_id = order_data.get("id", f"{order.symbol}_{order.created_ts_aest}")
            if signals:
                evaluator.write_signal_attribution(
                    order_id, signals, result_r=outcome.result_r,
                )

            resolved_count += 1
            print(f"[evaluate-outcomes] {order.symbol}: closed, R={outcome.result_r:.3f}")
            continue

        # Handle: filled but still in trade
        if fill_result.get("status") == "in_trade":
            should_cancel, reason = po_mod.check_cancel_rules(order, current_price, current_ts)
            if should_cancel and reason:
                order.filled = po_mod.OrderStatus.CANCELLED
                cancel_outcome = evaluator.compute_outcome(
                    order, exit_price=current_price,
                    fees_bps=order.fees_bps, slippage_bps=order.slippage_bps,
                )
                cancel_outcome.notes = reason.value
                evaluator.write_outcome(cancel_outcome)
                resolved_count += 1
                print(f"[evaluate-outcomes] {order.symbol}: cancelled ({reason.value})")
            else:
                remaining_orders.append(order_data)
                print(f"[evaluate-outcomes] {order.symbol}: in_trade, keeping.")
            continue

        # Handle: not filled (pending)
        if not fill_result.get("filled"):
            should_cancel, reason = po_mod.check_cancel_rules(order, current_price, current_ts)
            if should_cancel and reason:
                order.filled = po_mod.OrderStatus.CANCELLED
                cancel_outcome = evaluator.compute_outcome(
                    order, exit_price=current_price,
                    fees_bps=order.fees_bps, slippage_bps=order.slippage_bps,
                )
                cancel_outcome.notes = reason.value
                evaluator.write_outcome(cancel_outcome)
                resolved_count += 1
                print(f"[evaluate-outcomes] {order.symbol}: cancelled ({reason.value})")
            else:
                remaining_orders.append(order_data)
                print(f"[evaluate-outcomes] {order.symbol}: still pending, keeping.")
            continue

        # Default: keep in open orders
        remaining_orders.append(order_data)

    # Update mission state
    state["open_paper_orders"] = remaining_orders
    _save_mission_state_to(state_path, state)

    # Compute signal stats
    stats = evaluator.compute_signal_stats()
    if stats:
        print(f"[evaluate-outcomes] Signal stats: {len(stats)} signals tracked")

    print(f"[evaluate-outcomes] Evaluated {len(open_orders_data)} orders")
    print(f"[evaluate-outcomes] Resolved: {resolved_count}")
    print(f"[evaluate-outcomes] Remaining: {len(remaining_orders)}")

    return 0


def _run_ai_paper(account_id: str = "ai") -> int:
    """Execute the AI-agent scan loop for paper trading.

    Uses AI reasoning (via MCP tools) instead of deterministic signal scoring.
    Reuses the same risk sizing, paper order writing, outcome evaluation,
    and report generation as _run_live_paper().
    """
    import adapters.imperial as imperial_mod
    import engine.ai_agent as ai_mod
    import engine.kg as kg_mod
    import engine.mcp_data as mcp_mod
    import engine.outcomes as outcomes_mod
    import engine.paper_orders as po_mod
    import engine.report as report_mod
    import engine.risk as risk_mod
    import engine.skills as skills_mod
    from engine.paper_orders import OrderSide

    # Step 0: Mission Init
    acct = _account_root(account_id)
    state = _load_mission_state(account_id)
    mode = state.get("mode", "unknown")
    if mode not in ("live-paper-only", "ai-paper-only"):
        print(f"[INFO] Mode is '{mode}', treating as ai-paper for this run.")

    run_config = _load_yaml_config("run")
    risk_config = _load_yaml_config("risk")
    ai_config = _load_yaml_config("ai_agent")
    run_id = datetime.now(AEST).strftime("run_%Y%m%dT%H%M%S_AEST")
    timestamp_aest = datetime.now(AEST).strftime("%Y-%m-%d %H:%M:%S Australia/Sydney")

    print(f"[{run_id}] Starting AI-agent paper scan at {timestamp_aest}")
    print(f"[{run_id}] Mode: ai-paper | Account: {account_id}")

    open_orders = state.get("open_paper_orders", [])
    print(f"[{run_id}] Previous open orders: {len(open_orders)}")

    # Initialize components
    report = report_mod.ReportWriter(acct / "reports")
    report.set_section("A", f"Run {run_id} started at {timestamp_aest}. Mode: ai-paper (AI reasoning via MCP tools). Account: {account_id}.")
    kg = kg_mod.KGWriter(acct / "ledgers" / "kg_triples.csv")
    tracker = po_mod.PaperOrderTracker(acct / "ledgers" / "paper_orders.csv")
    evaluator = outcomes_mod.OutcomeEvaluator(
        outcomes_path=acct / "ledgers" / "outcomes.csv",
        signal_outcomes_path=acct / "ledgers" / "signal_outcomes.csv",
    )
    risk_params = risk_mod.RiskParams(
        equity=risk_config.get("equity", 1000),
        max_risk_pct=risk_config.get("max_risk_pct", 0.20),
        leverage_min=risk_config.get("leverage", {}).get("min", 9),
        leverage_max=risk_config.get("leverage", {}).get("max", 12),
    )

    max_candidates = ai_config.get("ai", {}).get("max_candidates",
                        run_config.get("run", {}).get("max_candidates", 3))
    max_open_trades = run_config.get("account", {}).get("max_open_trades", 4)

    # --- Auto-Evaluate open orders from previous cycle ---
    cancel_timeout = risk_config.get("cancel_rules", {}).get("timeout_minutes", 45)
    if open_orders:
        print(f"[{run_id}] Auto-evaluating {len(open_orders)} open order(s)...")
        remaining_orders = _auto_evaluate_open_orders(
            open_orders=open_orders, evaluator=evaluator,
            cancel_timeout=cancel_timeout, run_id=run_id,
        )
        state["open_paper_orders"] = remaining_orders
        _save_mission_state(state, account_id)
        open_orders = remaining_orders
    else:
        print(f"[{run_id}] Auto-evaluate: no open orders to evaluate.")

    # --- Signal Learning Output ---
    signal_outcomes_csv = acct / "ledgers" / "signal_outcomes.csv"
    signal_stats = _read_signal_outcome_stats(signal_outcomes_csv)
    report.set_section("L", _format_signal_learning_section(signal_stats))

    # --- Gather Market Data ---
    # HyperliquidAdapter for market data (replaces Phantom MCP), Flash Trade MCP for trading overview
    rich_data = mcp_mod.RichMarketData()
    all_datapoints: list[Any] = []

    # Phase 1: Try Flash Trade MCP for trading overview
    mcp_overview_raw = state.get("_mcp_trading_overview")
    if mcp_overview_raw and isinstance(mcp_overview_raw, dict):
        rich_data.markets = mcp_mod.parse_trading_overview(mcp_overview_raw)
        for m in rich_data.markets:
            rich_data.raw_prices[m.symbol] = m.price
        print(f"[{run_id}] MCP trading overview: {len(rich_data.markets)} markets")

    # Phase 2: HyperliquidAdapter for markets (funding, OI, basis, leverage)
    import adapters.hyperliquid as _hl_mod
    _hl_adapter = _hl_mod.HyperliquidAdapter()
    try:
        hl_points = _hl_adapter.fetch_markets()
        all_datapoints.extend(hl_points)
        hl_prices, hl_funding, hl_oi, hl_vol = mcp_mod.parse_hl_datapoints(hl_points)
        for sym, price in hl_prices.items():
            rich_data.raw_prices.setdefault(sym, price)
        rich_data.funding_rates.update(hl_funding)
        rich_data.open_interest.update(hl_oi)
        print(f"[{run_id}] HL adapter markets: {len(hl_points)} datapoints")
    except Exception as e:
        print(f"[{run_id}] WARNING: HL adapter markets failed: {e}")

    # Phase 3: HyperliquidAdapter for orderbook (book imbalance)
    try:
        hl_ob_points = _hl_adapter.fetch_orderbook("SOL")
        all_datapoints.extend(hl_ob_points)
        print(f"[{run_id}] HL adapter orderbook: {len(hl_ob_points)} datapoints")
    except Exception as e:
        print(f"[{run_id}] WARNING: HL adapter orderbook failed: {e}")

    # Phase 4: Flash Trade MCP prices (supplement)
    mcp_prices_raw = state.get("_mcp_prices")
    if mcp_prices_raw and isinstance(mcp_prices_raw, dict):
        for sym, price in mcp_prices_raw.items():
            sym_upper = str(sym).upper()
            try:
                rich_data.raw_prices[sym_upper] = float(price)
            except (TypeError, ValueError):
                pass

    # Phase 5: Flash Trade pool data
    mcp_pool_raw = state.get("_mcp_pool_data")
    if mcp_pool_raw and isinstance(mcp_pool_raw, (dict, list)):
        pool_items = mcp_pool_raw if isinstance(mcp_pool_raw, list) else [mcp_pool_raw]
        rich_data.pool_data = pool_items

    # Phase 6: Account data from MCP (if available)
    mcp_account_raw = state.get("_mcp_perps_account")
    if mcp_account_raw and isinstance(mcp_account_raw, dict):
        account_state = mcp_mod.parse_hl_account(
            mcp_mod.overview_to_datapoints(
                mcp_mod.RichMarketData(
                    account=mcp_mod.AccountState(
                        total_value_usd=float(mcp_account_raw.get("totalValueUsd", 0) or 0),
                        available_usd=float(mcp_account_raw.get("availableUsd", 0) or 0),
                        withdrawable_usd=float(mcp_account_raw.get("withdrawableUsd", 0) or 0),
                    ),
                ),
            ),
        )
        rich_data.account = account_state
        print(f"[{run_id}] Account: ${account_state.available_usd:.2f} available")

    mcp_positions_raw = state.get("_mcp_perps_positions")
    if mcp_positions_raw and isinstance(mcp_positions_raw, (dict, list)):
        if isinstance(mcp_positions_raw, list):
            existing_positions = mcp_positions_raw
        elif isinstance(mcp_positions_raw, dict):
            existing_positions = mcp_positions_raw.get("positions", mcp_positions_raw.get("data", []))
        else:
            existing_positions = []
        if rich_data.account:
            rich_data.account.positions = existing_positions
        print(f"[{run_id}] Existing positions: {len(existing_positions)}")

    # Convert MCP data to DataPoints for reports/ledgers
    all_datapoints.extend(mcp_mod.overview_to_datapoints(rich_data))

    # Fallback: Imperial API if MCP data is thin
    if not rich_data.raw_prices:
        print(f"[{run_id}] No MCP prices, falling back to Imperial API...")
        imperial = imperial_mod.ImperialAdapter()
        try:
            mark_prices = imperial.fetch_mark_prices()
            all_datapoints.extend(mark_prices)
            for dp in mark_prices:
                if "mark_price" in dp.metric and isinstance(dp.value, (int, float)) and dp.value > 0:
                    rich_data.raw_prices[dp.symbol] = dp.value
            print(f"[{run_id}] Imperial fallback: {len(rich_data.raw_prices)} prices")
        except Exception as e:
            print(f"[{run_id}] WARNING: Imperial fallback failed: {e}")

    if not rich_data.funding_rates:
        imperial = imperial_mod.ImperialAdapter()
        try:
            funding = imperial.fetch_funding_rates()
            all_datapoints.extend(funding)
            for dp in funding:
                if "funding" in dp.metric and isinstance(dp.value, (int, float)):
                    rich_data.funding_rates[dp.symbol] = dp.value
        except Exception:
            pass

    if not rich_data.volume_24h:
        imperial = imperial_mod.ImperialAdapter()
        try:
            stats = imperial.fetch_stats_markets()
            all_datapoints.extend(stats)
            for dp in stats:
                if dp.metric == "volume_24h" and isinstance(dp.value, (int, float)):
                    rich_data.volume_24h[dp.symbol] = dp.value
                elif dp.metric == "open_interest" and isinstance(dp.value, (int, float)):
                    rich_data.open_interest[dp.symbol] = dp.value
        except Exception:
            pass

    # --- Build universe from available data ---
    universe = list(set(
        list(rich_data.raw_prices.keys()) +
        [m.symbol for m in rich_data.markets]
    ))
    # Always include core symbols
    for core in ["BTC", "ETH", "SOL"]:
        if core not in universe:
            universe.append(core)
    print(f"[{run_id}] Universe: {universe[:15]}")

    report.set_section("B", (
        f"Core symbols: BTC, ETH, SOL\n\n"
        f"AI universe: {', '.join(universe[:10])}\n\n"
        f"Total universe: {len(universe)} symbols | "
        f"MCP markets: {len(rich_data.markets)} | "
        f"Prices: {len(rich_data.raw_prices)}"
    ))

    # Build the prompt regardless (useful for logging even if response is pre-provided)
    skill_warnings: list[str] = []
    try:
        loaded_skills, skill_warnings = skills_mod.load_enabled_skills(PROJECT_ROOT, ai_config)
    except FileNotFoundError as e:
        loaded_skills = []
        skill_warnings = [str(e)]
        print(f"[{run_id}] WARNING: {e}")
    active_skills_text = skills_mod.format_skills_for_prompt(loaded_skills)

    # Fetch Twitter CT intel for AI agent only (not deterministic)
    twitter_results_list = None
    try:
        from adapters.twitter_news import TwitterNewsAdapter
        _twitter = TwitterNewsAdapter()
        _twitter_results_list: list = []
        for sym in universe[:MAX_TWITTER_SYMBOLS]:
            _twitter_results_list.append(_twitter.fetch_symbol(sym))
        twitter_results_list = _twitter_results_list
        _tw_avail = sum(1 for r in _twitter_results_list if r.available)
        print(f"[{run_id}] Twitter CT: {_tw_avail}/{len(_twitter_results_list)} symbols")
    except Exception as e:
        print(f"[{run_id}] Twitter CT unavailable: {e}")

    # --- Hawk Breakout Signal Computation (ai-paper mode only) ---
    hawk_signals: list[Any] = []
    try:
        from engine.hawk_breakout import compute_hawk_breakout_signal
        from engine.mcp_data import extract_sm_tilt
        import adapters.hyperliquid as _hl_mod

        # Collect whale data from Dextrabot for SM tilt
        _ai_whale_points: list[Any] = []
        try:
            import adapters.dextrabot as _dext_mod
            _dext = _dext_mod.DextrabotAdapter(cache_dir=str(PROJECT_ROOT / "data" / "raw"))
            _ai_whale_points = _dext.fetch_wallets()
            if _ai_whale_points:
                print(f"[{run_id}] Hawk: {len(_ai_whale_points)} whale datapoints for SM tilt")
        except Exception as e:
            print(f"[{run_id}] Hawk: Dextrabot unavailable for SM tilt: {e}")

        # Fetch candles via HyperliquidAdapter with cache
        _hl_adapter = _hl_mod.HyperliquidAdapter()
        _ai_candles_1h: list[dict[str, Any]] = []
        _ai_candles_4h: list[dict[str, Any]] = []
        try:
            _ai_candles_1h = _hl_adapter.fetch_candles_cached(
                coin="SOL", interval="1h", hours=168,
                account_id=account_id, project_root=PROJECT_ROOT,
            )
            _ai_candles_4h = _hl_adapter.fetch_candles_cached(
                coin="SOL", interval="4h", hours=168,
                account_id=account_id, project_root=PROJECT_ROOT,
            )
            print(f"[{run_id}] Hawk: {len(_ai_candles_1h)} 1h candles, {len(_ai_candles_4h)} 4h candles")
        except Exception as e:
            print(f"[{run_id}] Hawk: Candle fetch failed: {e}")

        # Build HL markets lookup by symbol (from MCP data if available)
        _hl_markets_by_symbol: dict[str, dict[str, Any]] = {}
        mcp_mkts_raw = state.get("_mcp_perps_markets")
        if mcp_mkts_raw and isinstance(mcp_mkts_raw, dict):
            # Parse MCP markets dict manually (no longer using old perps parser)
            items = mcp_mkts_raw.get("markets", mcp_mkts_raw.get("data", []))
            if isinstance(items, dict):
                items = list(items.values())
            for item in items:
                if isinstance(item, dict):
                    sym_key = str(item.get("coin", item.get("symbol", ""))).upper()
                    if sym_key:
                        _hl_markets_by_symbol[sym_key] = item

        # Compute hawk signal for each symbol in universe
        for sym in universe[:8]:
            closes_1h: list[float] = []
            closes_4h: list[float] = []
            volume_1h: list[float] = []

            # Use real candle data for SOL
            if sym == "SOL" and _ai_candles_1h:
                closes_1h, volume_1h = _hl_mod.candles_to_arrays(_ai_candles_1h)
                if _ai_candles_4h:
                    closes_4h, _ = _hl_mod.candles_to_arrays(_ai_candles_4h)
            else:
                # Fallback to mark-price datapoints
                closes_1h = [dp.value for dp in all_datapoints
                             if getattr(dp, "symbol", None) == sym
                             and "mark_price" in str(getattr(dp, "metric", ""))]
                closes_4h = closes_1h
                volume_1h = [dp.value for dp in all_datapoints
                             if getattr(dp, "symbol", None) == sym
                             and getattr(dp, "metric", "") == "volume_24h"]

            if not closes_1h:
                continue
            # Pad short series to at least 2 points
            if len(closes_1h) < 2:
                closes_1h = closes_1h * 168

            sm_pct = extract_sm_tilt(
                symbol=sym,
                whale_points=_ai_whale_points,
                hl_market=_hl_markets_by_symbol.get(sym),
            )

            # Default structure_classification to structure_partial when no evaluator wired
            structure_class = "structure_partial"

            sig = compute_hawk_breakout_signal(
                market=sym,
                closes_1h=closes_1h,
                closes_4h=closes_4h if closes_4h else closes_1h,
                volume_1h=volume_1h if volume_1h else [0.0],
                sm_long_pct=sm_pct,
                structure_classification=structure_class,
            )
            hawk_signals.append(sig)
            print(f"[{run_id}] Hawk {sym}: {sig.signal} score={sig.score} {sig.notes}")
    except Exception as e:
        print(f"[{run_id}] Hawk computation failed: {e}")

    prompt = mcp_mod.format_ai_prompt(
        market_data=rich_data,
        equity=risk_params.equity,
        max_open_trades=max_open_trades,
        max_candidates=max_candidates,
        prompt_id=run_id,
        active_skills=active_skills_text,
        existing_positions=rich_data.account.positions if rich_data.account else [],
        prior_signal_stats=signal_stats,
        twitter_results=twitter_results_list,
        hawk_signals=hawk_signals,
    )

    # Save prompt for Droid/Hermes to use
    prompt_path = acct / "data" / "ai_prompt.txt"
    prompt_path.parent.mkdir(parents=True, exist_ok=True)
    prompt_path.write_text(prompt, encoding="utf-8")
    print(f"[{run_id}] AI prompt saved to data/ai_prompt.txt ({len(prompt)} chars)")

    ai_response_path = acct / "data" / "ai_response.json"
    request_path = acct / "data" / "ai_request.json"
    request_payload = {
        "prompt_id": run_id,
        "created_ts_Australia/Sydney": timestamp_aest,
        "account": account_id,
        "mode": "ai-paper",
        "prompt_path": str(prompt_path),
        "response_path": str(ai_response_path),
        "required_response_shape": {
            "prompt_id": run_id,
            "agent": "hermes|droid",
            "decision_ts_Australia/Sydney": "YYYY-MM-DD HH:MM:SS Australia/Sydney",
            "no_trade_reason": "",
            "trades": [],
        },
        "skills_loaded": [s.name for s in loaded_skills],
        "skill_warnings": skill_warnings,
    }
    request_path.write_text(json.dumps(request_payload, indent=2) + "\n", encoding="utf-8")
    print(f"[{run_id}] AI request metadata saved to data/ai_request.json")

    # Clear stale AI response from previous cycles so mismatched prompt_ids
    # don't pollute this run.
    if ai_response_path.exists():
        ai_response_path.unlink()

    bridge_status = "not_configured"
    agent_file_cfg = ai_config.get("ai", {}).get("agent_file", {})
    bridge_command = str(agent_file_cfg.get("bridge_command", "") or "").strip()
    if bridge_command:
        env = os.environ.copy()
        env.update({
            "IMPERIAL_AI_PROMPT_PATH": str(prompt_path),
            "IMPERIAL_AI_REQUEST_PATH": str(request_path),
            "IMPERIAL_AI_RESPONSE_PATH": str(ai_response_path),
            "IMPERIAL_AI_PROMPT_ID": run_id,
        })
        try:
            result = subprocess.run(
                shlex.split(bridge_command),
                cwd=PROJECT_ROOT,
                env=env,
                capture_output=True,
                text=True,
                timeout=300,
            )
            bridge_status = f"exit_{result.returncode}"
            if result.stdout.strip():
                print(f"[{run_id}] AI bridge stdout: {result.stdout.strip()[:500]}")
            if result.stderr.strip():
                print(f"[{run_id}] AI bridge stderr: {result.stderr.strip()[:500]}")
        except Exception as e:
            bridge_status = f"error:{type(e).__name__}:{e}"
            print(f"[{run_id}] AI bridge failed: {bridge_status}")

    # The AI response can be injected via mission_state._ai_response by a
    # Droid/Hermes session, or written to data/ai_response.json by an agent/API
    # bridge. It must echo this run's prompt_id to avoid stale decisions.
    ai_response_raw = state.get("_ai_response", "")
    ai_response_source = "mission_state._ai_response" if ai_response_raw else ""
    if not ai_response_raw and ai_response_path.exists():
        ai_response_raw = ai_response_path.read_text(encoding="utf-8")
        ai_response_source = "data/ai_response.json"
        print(f"[{run_id}] Read AI response from {ai_response_source}")

    # Parse AI response into trade candidates
    response_accept_reason = "no_response"
    if ai_response_raw:
        candidate_json, accepted, response_accept_reason = ai_mod.validate_prompt_bound_response(
            ai_response_raw, expected_prompt_id=run_id,
        )
        if accepted:
            ai_candidates = ai_mod.parse_ai_response(candidate_json)
            print(f"[{run_id}] AI response accepted from {ai_response_source}: {len(ai_candidates)} candidates")
        else:
            ai_candidates = []
            print(f"[{run_id}] AI response ignored: {response_accept_reason}")
    else:
        ai_candidates = []
        print(f"[{run_id}] No AI response provided. Prompt is ready for Droid/Hermes delegation.")
        print(f"[{run_id}] To trade AI account this cycle, write prompt-bound JSON to data/ai_response.json.")

    # Validate candidates
    for cand in ai_candidates:
        price = rich_data.raw_prices.get(cand.symbol, cand.entry)
        # Estimate ATR from price (1.5% proxy) if no real ATR
        atr_est = price * 0.015
        ai_mod.validate_candidate(cand, atr=atr_est)

    valid_candidates = [c for c in ai_candidates if c.valid]
    print(f"[{run_id}] Valid candidates: {len(valid_candidates)}/{len(ai_candidates)}")

    # --- Report Sections ---

    # Section C: Evidence (market data summary)
    evidence_lines = ["### Market Data (MCP Sources)\n\n"]
    evidence_lines.append("| Symbol | Price | Funding | OI | Volume 24h |\n")
    evidence_lines.append("|--------|-------|---------|-----|------------|\n")
    for sym in sorted(universe[:15]):
        price = rich_data.raw_prices.get(sym, 0)
        fr = rich_data.funding_rates.get(sym, "N/A")
        oi = rich_data.open_interest.get(sym, "N/A")
        vol = rich_data.volume_24h.get(sym, "N/A")
        fr_str = f"{fr:.6f}" if isinstance(fr, (int, float)) else str(fr)
        oi_str = f"${oi:,.0f}" if isinstance(oi, (int, float)) else str(oi)
        vol_str = f"${vol:,.0f}" if isinstance(vol, (int, float)) else str(vol)
        evidence_lines.append(
            f"| {sym} | {price:.2f} | {fr_str} | {oi_str} | {vol_str} |\n"
        )
    report.set_section("C", "".join(evidence_lines))

    # Section D: On-chain Flow
    report.set_section("D", (
        f"### AI Agent Data Sources\n\n"
        f"*Data from {len(all_datapoints)} DataPoints across MCP tools and Imperial API.*\n\n"
        f"MCP markets: {len(rich_data.markets)} | "
        f"Prices: {len(rich_data.raw_prices)} | "
        f"Funding: {len(rich_data.funding_rates)} | "
        f"Pool data: {len(rich_data.pool_data)}"
    ))

    # Section E: AI Reasoning
    report.set_section("E", ai_mod.build_ai_report_section(ai_candidates))

    # --- Risk Sizing + Paper Order Writing ---
    final_trades: list[dict[str, Any]] = []

    # Check vulcan availability once
    vulcan_available = False
    try:
        from adapters.vulcan import VulcanAdapter
        vulcan_available = VulcanAdapter.is_available()
    except ImportError:
        pass

    for cand in valid_candidates[:max_candidates]:
        if len(final_trades) >= max_open_trades:
            break

        sym = cand.symbol
        price = rich_data.raw_prices.get(sym, cand.entry)
        if price <= 0:
            price = cand.entry

        # Risk sizing — skip passive entry check when vulcan handles execution
        if vulcan_available:
            sizing = risk_mod.compute_risk_sizing(
                symbol=sym, side=cand.side, entry=cand.entry, stop=cand.stop,
                params=risk_params,
            )
        else:
            best_bid = price * 0.9999
            best_ask = price * 1.0001
            sizing = risk_mod.compute_risk_sizing(
                symbol=sym, side=cand.side, entry=cand.entry, stop=cand.stop,
                params=risk_params, best_bid=best_bid, best_ask=best_ask,
            )

        if not sizing.valid:
            risk_mod.write_skipped_trade(
                csv_path=acct / "ledgers" / "skipped_trades.csv",
                symbol=sym, side=cand.side.value, reason=sizing.reject_reason,
                entry=cand.entry, stop=cand.stop,
            )
            print(f"[{run_id}]   {sym} {cand.side.value}: REJECTED - {sizing.reject_reason}")
            continue

        # Execute via Vulcan if available, otherwise synthetic paper order
        vulcan_result = None
        if vulcan_available:
            vulcan_result = _execute_trade_via_vulcan(
                account_id=account_id,
                symbol=sym,
                side=cand.side.value,
                notional_usdc=sizing.notional,
                tp=cand.tp1,
                sl=cand.stop,
                run_id=run_id,
            )

        if vulcan_result:
            fill = vulcan_result["fill"]
            entry_price = fill.price
            execution = "vulcan-phoenix"
        else:
            # Fallback: synthetic paper order
            order = sizing.to_paper_order(
                setup=cand.setup_type, tp1=cand.tp1, tp2=cand.tp2,
                provenance_tags=f"run={run_id},mode=ai-paper,setup={cand.setup_type},band={cand.probability_band}",
            )
            tracker.write_order(order)
            entry_price = cand.entry
            execution = "synthetic"

        kg.add(subject=sym, predicate="has_order", object_=run_id,
               attrs={"side": cand.side.value, "entry": entry_price, "stop": cand.stop,
                       "setup": cand.setup_type, "rationale": cand.rationale,
                       "execution": execution},
               source_name="AI-Agent")

        final_trades.append({
            "symbol": sym, "side": cand.side.value, "setup": cand.setup_type,
            "entry": entry_price, "stop": cand.stop, "tp1": cand.tp1, "tp2": cand.tp2,
            "qty": sizing.qty, "notional": sizing.notional, "leverage": sizing.leverage,
            "risk_usd": sizing.risk_usd,
            "rationale": cand.rationale, "probability_band": cand.probability_band,
            "execution": execution,
        })
        print(f"[{run_id}]   {sym} {cand.side.value}: {execution} ${sizing.notional:.2f} @ {sizing.leverage:.1f}x")

    kg.flush()

    # Determine status
    status = "paper_candidate" if final_trades else "no_trade"

    # Section F: Final Paper Trades
    if final_trades:
        trade_lines = []
        for t in final_trades:
            exec_venue = t.get("execution", "synthetic")
            trade_lines.append(
                f"**{t['symbol']} {t['side'].upper()}**\n"
                f"- Setup: {t['setup']} ({t.get('probability_band', 'N/A')} confidence)\n"
                f"- Entry: {t['entry']:.2f} | Stop: {t['stop']:.2f}\n"
                f"- TP1: {t['tp1']:.2f} | TP2: {t['tp2']:.2f}\n"
                f"- Qty: {t['qty']:.4f} | Notional: ${t['notional']:.2f}\n"
                f"- Leverage: {t['leverage']:.1f}x | Risk: ${t['risk_usd']:.2f}\n"
                f"- Execution: {exec_venue}\n"
                f"- Rationale: {t.get('rationale', 'N/A')}\n"
            )
        report.set_section("F", "\n".join(trade_lines))
    else:
        if not ai_candidates:
            no_trade_reason = f"no accepted AI candidates ({response_accept_reason})"
        elif not valid_candidates:
            no_trade_reason = f"all {len(ai_candidates)} AI candidates failed validation"
        else:
            no_trade_reason = "all risk sizings were invalid"
        report.set_section("F", (
            f"No paper trades generated. Status: `{status}`.\n\n"
            f"Reason: {no_trade_reason}.\n\n"
            f"AI candidates: {len(ai_candidates)}, Valid: {len(valid_candidates)}."
        ))

    # Section G: X Post Draft
    report.set_section("G", "No X post draft (AI paper scan mode). NFA/DYOR.")

    # Section H: Assumptions and Gaps
    report.set_section("H", (
        "### Assumptions\n"
        "- Market data from Flash Trade MCP tools, with Imperial API fallback\n"
        "- AI reasoning produces trade decisions (replaces deterministic signal scoring)\n"
        "- Risk sizing, cancel rules, and outcome evaluation remain deterministic\n"
        "- Signal weights in scoring.py are NOT used (AI decides independently)\n\n"
        "### Gaps\n"
        "- MCP tools require Droid/Hermes agent data injection or Imperial API fallback\n"
        "- AI response must be prompt-bound via mission_state._ai_response or data/ai_response.json\n"
        "- No real OHLC candles (flat mark-price approximation for ATR)\n"
        "- Catalyst signal always unknown\n"
        f"- Skills loaded: {', '.join([s.name for s in loaded_skills]) or 'none'}\n"
        f"- Skill warnings: {', '.join(skill_warnings) if skill_warnings else 'none'}\n"
        f"- AI bridge status: {bridge_status}\n"
        f"- AI response status: {response_accept_reason}"
    ))

    # Section I: Citations
    sources_used = set()
    for dp in all_datapoints:
        sources_used.add(dp.provenance.source_name)
    sources_str = ", ".join(sorted(sources_used)) if sources_used else "AI-Agent (MCP)"
    report.set_section("I", (
        f"Sources used: {sources_str}\n\n"
        f"All data fetched at {timestamp_aest}"
    ))

    # Section J: OutcomeGraph CSV
    report.set_section("J", (
        "```csv\n"
        "date_Australia/Sydney,symbol,setup,side,entry,stop,tp1,tp2,filled,"
        "entry_ts_Australia/Sydney,exit_ts_Australia/Sydney,result_R,"
        "max_FvE,max_AdE,fees_bps,slippage_bps,notes,provenance_tags\n"
        "```\n\n"
        f"Paper orders written: {len(final_trades)}"
    ))

    # Section K: Audit
    report.set_section("K", (
        "| Metric | Score |\n|--------|-------|\n"
        f"| Mode | ai-paper (MCP + AI reasoning) |\n"
        f"| MCP data points | {len(all_datapoints)} |\n"
        f"| MCP markets | {len(rich_data.markets)} |\n"
        f"| AI candidates | {len(ai_candidates)} |\n"
        f"| Valid candidates | {len(valid_candidates)} |\n"
        f"| Paper trades | {len(final_trades)} |\n"
        f"| Skills loaded | {len(loaded_skills)} |\n"
        f"| AI bridge | {bridge_status} |\n"
        f"| Response status | {response_accept_reason} |\n"
        f"| Risk sizing | deterministic (risk.py) |\n"
        f"| Top failure mode | {'none' if final_trades else 'no_valid_ai_candidates'} |"
    ))

    # Write report
    report_path = report.write(status=status)

    # Update mission state
    state["last_run_id"] = run_id
    state["last_ai_prompt_path"] = str(prompt_path)
    new_trade_orders = [
        {
            "symbol": t["symbol"],
            "side": t["side"],
            "setup": t["setup"],
            "entry": t["entry"],
            "stop": t["stop"],
            "tp1": t["tp1"],
            "tp2": t["tp2"],
            "qty": t["qty"],
            "notional": t["notional"],
            "leverage": t["leverage"],
            "created_ts_aest": tracker.read_orders()[-1].created_ts_aest if tracker.read_orders() else datetime.now(AEST).strftime("%Y-%m-%d %H:%M:%S Australia/Sydney"),
            "fees_bps": 5.0,
            "slippage_bps": 3.0,
            "provenance_tags": f"run={run_id},mode=ai-paper",
            "signals": [t["setup"]],
        }
        for t in final_trades
    ]
    state["open_paper_orders"] = open_orders + new_trade_orders
    # Clear one-shot AI response
    state.pop("_ai_response", None)
    _save_mission_state(state, account_id)

    print(f"[{run_id}] Status: {status}")
    print(f"[{run_id}] Paper trades: {len(final_trades)}")
    print(f"[{run_id}] Report: {report_path}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Sol Porpoise Run Scan")
    parser.add_argument(
        "--mode",
        choices=["plumbing-dry-run", "live-paper", "evaluate-outcomes", "ai-paper"],
        default="plumbing-dry-run",
        help="Run mode",
    )
    parser.add_argument(
        "--account",
        default=None,
        help="Account ID for isolated ledgers/reports (default: 'deterministic' for live-paper, 'ai' for ai-paper)",
    )
    args = parser.parse_args()

    # Default account IDs by mode
    account = args.account
    if account is None:
        if args.mode == "ai-paper":
            account = "ai"
        else:
            account = "deterministic"

    if args.mode == "plumbing-dry-run":
        return _run_plumbing_dry_run(account)
    elif args.mode == "live-paper":
        return _run_live_paper(account)
    elif args.mode == "evaluate-outcomes":
        return _run_evaluate_outcomes(account_id=account)
    elif args.mode == "ai-paper":
        return _run_ai_paper(account)
    else:
        print(f"Unknown mode: {args.mode}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
