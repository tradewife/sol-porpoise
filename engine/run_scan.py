"""Run scan entry point: full 14-step live paper trading scan loop.

Usage:
    python -m engine.run_scan --mode plumbing-dry-run
    python -m engine.run_scan --mode live-paper
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

from zoneinfo import ZoneInfo

PROJECT_ROOT = Path(__file__).resolve().parent.parent
AEST = ZoneInfo("Australia/Sydney")


def _load_mission_state() -> dict:
    state_path = PROJECT_ROOT / "memory" / "mission_state.json"
    if state_path.exists():
        return json.loads(state_path.read_text(encoding="utf-8"))
    return {}


def _save_mission_state(state: dict) -> None:
    state_path = PROJECT_ROOT / "memory" / "mission_state.json"
    state_path.write_text(json.dumps(state, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def _load_yaml_config(name: str) -> dict:
    import yaml
    path = PROJECT_ROOT / "config" / f"{name}.yaml"
    if path.exists():
        return yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return {}


def _save_mission_state_to(path: Path, state: dict) -> None:
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


def _run_plumbing_dry_run() -> int:
    from engine.report import build_dry_run_report

    report_dir = PROJECT_ROOT / "reports"
    path = build_dry_run_report(report_dir)

    paper_orders = PROJECT_ROOT / "ledgers" / "paper_orders.csv"
    lines = paper_orders.read_text(encoding="utf-8").strip().split("\n")
    assert len(lines) == 1, "paper_orders.csv must have only header row in dry run"

    print(f"[dry-run] Report written to {path}")
    print(f"[dry-run] Status: no_trade")
    print(f"[dry-run] paper_orders.csv: header only (verified)")
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


def _run_live_paper() -> int:
    """Execute the full 14-step scan loop for live paper trading."""
    import adapters.imperial as imperial_mod
    import adapters.flash_trade as ft_mod
    import adapters.phantom as phantom_mod
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
    state = _load_mission_state()
    mode = state.get("mode", "unknown")
    if mode != "live-paper-only":
        print(f"[FATAL] Mode is '{mode}', expected 'live-paper-only'. Aborting.", file=sys.stderr)
        return 1

    run_config = _load_yaml_config("run")
    risk_config = _load_yaml_config("risk")
    run_id = datetime.now(AEST).strftime("run_%Y%m%dT%H%M%S_AEST")
    timestamp_aest = datetime.now(AEST).strftime("%Y-%m-%d %H:%M:%S Australia/Sydney")

    print(f"[{run_id}] Starting live-paper scan at {timestamp_aest}")
    print(f"[{run_id}] Mode: {mode}")

    # Load previous state
    open_orders = state.get("open_paper_orders", [])
    unresolved = state.get("unresolved_outcomes", [])
    print(f"[{run_id}] Previous open orders: {len(open_orders)}, unresolved: {len(unresolved)}")

    # Initialize components
    report = report_mod.ReportWriter(PROJECT_ROOT / "reports")
    report.set_section("A", f"Run {run_id} started at {timestamp_aest}. Mode: {mode}.")
    kg = kg_mod.KGWriter(PROJECT_ROOT / "ledgers" / "kg_triples.csv")
    imperial = imperial_mod.ImperialAdapter()
    ft_adapter = ft_mod.FlashTradeAdapter()
    phantom = phantom_mod.PhantomAdapter()
    dext = dext_mod.DextrabotAdapter(cache_dir=str(PROJECT_ROOT / "data" / "raw"))
    tracker = po_mod.PaperOrderTracker(PROJECT_ROOT / "ledgers" / "paper_orders.csv")
    evaluator = outcomes_mod.OutcomeEvaluator(
        outcomes_path=PROJECT_ROOT / "ledgers" / "outcomes.csv",
        signal_outcomes_path=PROJECT_ROOT / "ledgers" / "signal_outcomes.csv",
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
        _save_mission_state(state)
        open_orders = remaining_orders
        print(f"[{run_id}] Auto-evaluate complete. Remaining open: {len(open_orders)}")
    else:
        print(f"[{run_id}] Auto-evaluate: no open orders to evaluate.")

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

    # Collect whale and HL data (currently empty, adapters not called in scan loop)
    whale_datapoints: list[Any] = []
    hl_datapoints: list[Any] = []

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
                csv_path=PROJECT_ROOT / "ledgers" / "skipped_trades.csv",
                symbol=sym, side="none", reason="no_playbook_qualified",
                entry=price, stop=0,
            )
            continue

        best_pb = playbooks[0]

        sizing = risk_mod.compute_risk_sizing(
            symbol=sym, side=best_pb.side, entry=best_pb.entry, stop=best_pb.stop,
            params=risk_params, best_bid=best_bid, best_ask=best_ask,
        )

        if not sizing.valid:
            risk_mod.write_skipped_trade(
                csv_path=PROJECT_ROOT / "ledgers" / "skipped_trades.csv",
                symbol=sym, side=best_pb.side.value, reason=sizing.reject_reason,
                entry=best_pb.entry, stop=best_pb.stop,
            )
            continue

        order = sizing.to_paper_order(
            setup=best_pb.setup_type, tp1=best_pb.tp1, tp2=best_pb.tp2,
            provenance_tags=f"run={run_id},score={score.weighted_score:.3f},setup={best_pb.setup_type}",
        )
        tracker.write_order(order)
        kg.add(subject=sym, predicate="has_order", object_=run_id,
               attrs={"side": best_pb.side.value, "entry": best_pb.entry, "stop": best_pb.stop},
               source_name="Internal")

        final_trades.append({
            "symbol": sym, "side": best_pb.side.value, "setup": best_pb.setup_type,
            "entry": best_pb.entry, "stop": best_pb.stop, "tp1": best_pb.tp1, "tp2": best_pb.tp2,
            "qty": sizing.qty, "notional": sizing.notional, "leverage": sizing.leverage,
            "risk_usd": sizing.risk_usd,
            "rationale": best_pb.rationale, "probability_band": best_pb.probability_band,
        })

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
            trade_lines.append(
                f"**{t['symbol']} {t['side'].upper()}**\n"
                f"- Setup: {t['setup']} ({t.get('probability_band', 'N/A')} confidence)\n"
                f"- Entry: {t['entry']:.2f} | Stop: {t['stop']:.2f}\n"
                f"- TP1: {t['tp1']:.2f} | TP2: {t['tp2']:.2f}\n"
                f"- Qty: {t['qty']:.4f} | Notional: ${t['notional']:.2f}\n"
                f"- Leverage: {t['leverage']:.1f}x | Risk: ${t['risk_usd']:.2f}\n"
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
            "created_ts_aest": tracker.read_orders()[-1].created_ts_aest if tracker.read_orders() else aest_now_iso(),
            "fees_bps": 5.0,
            "slippage_bps": 3.0,
            "provenance_tags": f"run={run_id}",
            "signals": [k for k, v in candidates[0]["components"].items() if v.label != "unknown"][:5] if candidates else [],
        }
        for t in final_trades
    ]
    # Preserve in-trade/pending orders from auto-evaluate alongside new trades
    state["open_paper_orders"] = open_orders + new_trade_orders
    _save_mission_state(state)

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


def _run_evaluate_outcomes(base_path: Path | None = None) -> int:
    """Evaluate open paper orders against post-order market data.

    Reads open orders from mission_state.json, fetches current prices,
    evaluates fills, computes outcomes, updates state.
    """
    import engine.paper_orders as po_mod
    import engine.outcomes as outcomes_mod

    root = base_path or PROJECT_ROOT
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


def main() -> int:
    parser = argparse.ArgumentParser(description="Imperial Agent Run Scan")
    parser.add_argument(
        "--mode",
        choices=["plumbing-dry-run", "live-paper", "evaluate-outcomes"],
        default="plumbing-dry-run",
        help="Run mode",
    )
    args = parser.parse_args()

    if args.mode == "plumbing-dry-run":
        return _run_plumbing_dry_run()
    elif args.mode == "live-paper":
        return _run_live_paper()
    elif args.mode == "evaluate-outcomes":
        return _run_evaluate_outcomes()
    else:
        print(f"Unknown mode: {args.mode}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
