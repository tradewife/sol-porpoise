"""MCP data fetcher: calls Flash Trade and Phantom MCP tools and normalizes
results into the project's DataPoint format for use by the AI agent scan loop.

This module does NOT call MCP tools directly (those are available only in the
Droid session). Instead, it provides a structured interface that the
run_scan.py ai-paper mode will call through ToolSearch/invoke. For testing
and dry-run, it falls back to Imperial API data.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from zoneinfo import ZoneInfo

from adapters.base import DataPoint, Provenance, SourceTier
from adapters.normalizer import aest_now_iso, make_provenance
from adapters.twitter_news import TwitterResult, format_twitter_prompt_section

# Lazy import to avoid circular dependency; resolved at call time.
# from engine.hawk_breakout import HawkSignal

AEST = ZoneInfo("Australia/Sydney")


@dataclass
class MarketOverview:
    """Summary of a single market from Flash Trade trading overview."""
    symbol: str
    price: float
    max_leverage: float
    pool_utilization_pct: float
    side: str  # "long" / "short"
    pool_pubkey: str = ""
    market_pubkey: str = ""


@dataclass
class AccountState:
    """Current perps account state from Phantom."""
    total_value_usd: float
    available_usd: float
    withdrawable_usd: float
    positions: list[dict[str, Any]] = field(default_factory=list)
    open_orders: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class RichMarketData:
    """Aggregated market data from multiple MCP sources."""
    markets: list[MarketOverview] = field(default_factory=list)
    account: AccountState | None = None
    raw_prices: dict[str, float] = field(default_factory=dict)
    funding_rates: dict[str, float] = field(default_factory=dict)
    open_interest: dict[str, float] = field(default_factory=dict)
    volume_24h: dict[str, float] = field(default_factory=dict)
    pool_data: list[dict[str, Any]] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


def _make_mcp_provenance(source_name: str) -> Provenance:
    return make_provenance(
        source_name=source_name,
        source_tier=SourceTier.SOLANA_NATIVE,
        source_link="[mcp-tool]",
        confidence=0.95,
    )


def overview_to_datapoints(data: RichMarketData) -> list[DataPoint]:
    """Convert RichMarketData into a list of DataPoints for report/ledger use."""
    points: list[DataPoint] = []
    prov_flash = _make_mcp_provenance("FlashTrade-MCP")
    prov_phantom = _make_mcp_provenance("Phantom-MCP")

    for market in data.markets:
        sym = market.symbol
        if market.price > 0:
            points.append(DataPoint(
                symbol=sym, metric="mark_price_flash", value=market.price,
                provenance=prov_flash,
            ))
        if market.pool_utilization_pct > 0:
            points.append(DataPoint(
                symbol=sym, metric="pool_utilization_pct",
                value=market.pool_utilization_pct,
                provenance=prov_flash,
            ))

    for sym, price in data.raw_prices.items():
        if sym not in {p.symbol for p in points if "mark_price" in p.metric}:
            points.append(DataPoint(
                symbol=sym, metric="mark_price", value=price,
                provenance=prov_flash,
            ))

    for sym, rate in data.funding_rates.items():
        points.append(DataPoint(
            symbol=sym, metric="funding_rate", value=rate,
            provenance=prov_flash,
        ))

    for sym, oi in data.open_interest.items():
        points.append(DataPoint(
            symbol=sym, metric="open_interest", value=oi,
            provenance=prov_flash,
        ))

    for sym, vol in data.volume_24h.items():
        points.append(DataPoint(
            symbol=sym, metric="volume_24h", value=vol,
            provenance=prov_flash,
        ))

    if data.account:
        points.append(DataPoint(
            symbol="ACCOUNT", metric="perps_total_value_usd",
            value=data.account.total_value_usd,
            provenance=prov_phantom,
        ))
        points.append(DataPoint(
            symbol="ACCOUNT", metric="perps_available_usd",
            value=data.account.available_usd,
            provenance=prov_phantom,
        ))

    return points


def extract_sm_tilt(
    symbol: str,
    whale_points: list,
    hl_market: dict | None,
) -> float | None:
    """Extract Smart Money long % from HL leaderboard data or whale DataPoints.

    Tries HL leaderboard ratio first (topTraderLongRatio / longRatio in 0-1 range).
    Falls back to counting whale DataPoint long/short labels for the target symbol.
    Returns None when no usable data is available.
    """
    # Try HL leaderboard ratio first
    if hl_market:
        ratio = hl_market.get("topTraderLongRatio") or hl_market.get("longRatio")
        if ratio is not None:
            try:
                return float(ratio) * 100  # 0-1 to 0-100
            except (TypeError, ValueError):
                pass

    # Fallback: count whale direction labels
    longs = sum(
        1 for dp in whale_points
        if getattr(dp, "symbol", None) == symbol
        and "long" in str(getattr(dp, "metric", "")).lower()
    )
    shorts = sum(
        1 for dp in whale_points
        if getattr(dp, "symbol", None) == symbol
        and "short" in str(getattr(dp, "metric", "")).lower()
    )
    total = longs + shorts
    if total > 0:
        return longs / total * 100
    return None


def format_hawk_prompt_section(hawk_signals: list) -> str:
    """Format a list of HawkSignal objects into a markdown prompt section.

    Returns a section header plus per-signal details, or a no-signals message
    when the list is empty.
    """
    if not hawk_signals:
        return "## Hawk Breakout Signals\n\nNo signals computed this cycle.\n"
    lines = ["## Hawk Breakout Signals", ""]
    for sig in hawk_signals:
        lines.append(f"### {sig.market}")
        lines.append(f"- signal: {sig.signal}")
        lines.append(f"- score: {sig.score}/9")
        lines.append(f"- basis: {sig.basis}")
        lines.append(f"- notes: {sig.notes}")
        lines.append("")
    return "\n".join(lines)


def parse_trading_overview(raw: dict[str, Any]) -> list[MarketOverview]:
    """Parse the result of flash-trade___get_trading_overview into MarketOverview list."""
    markets: list[MarketOverview] = []
    if not isinstance(raw, dict):
        return markets

    overview_list = raw.get("markets", raw.get("data", []))
    if isinstance(overview_list, dict):
        overview_list = list(overview_list.values())

    for item in overview_list:
        if not isinstance(item, dict):
            continue
        symbol = str(item.get("symbol", item.get("name", ""))).upper()
        if not symbol:
            continue
        price = float(item.get("price", item.get("markPrice", 0)) or 0)
        leverage = float(item.get("maxLeverage", item.get("max_leverage", 0)) or 0)
        utilization = float(item.get("poolUtilizationPct", item.get("utilization", 0)) or 0)
        side = str(item.get("side", "long")).lower()
        pool_pk = str(item.get("poolPubkey", item.get("pool", "")))
        market_pk = str(item.get("pubkey", item.get("marketPubkey", "")))
        markets.append(MarketOverview(
            symbol=symbol, price=price, max_leverage=leverage,
            pool_utilization_pct=utilization, side=side,
            pool_pubkey=pool_pk, market_pubkey=market_pk,
        ))
    return markets


def parse_account_summary(raw: dict[str, Any]) -> AccountState:
    """Parse the result of phantom___perps_account into AccountState."""
    if not isinstance(raw, dict):
        return AccountState(total_value_usd=0, available_usd=0, withdrawable_usd=0)

    return AccountState(
        total_value_usd=float(raw.get("totalValueUsd", raw.get("total_value", 0)) or 0),
        available_usd=float(raw.get("availableUsd", raw.get("available", 0)) or 0),
        withdrawable_usd=float(raw.get("withdrawableUsd", raw.get("withdrawable", 0)) or 0),
        positions=raw.get("positions", []),
        open_orders=raw.get("openOrders", []),
    )


def parse_perps_positions(raw: dict[str, Any]) -> list[dict[str, Any]]:
    """Parse the result of phantom___perps_positions into a list of position dicts."""
    if isinstance(raw, list):
        return raw
    if isinstance(raw, dict):
        return raw.get("positions", raw.get("data", []))
    return []


def parse_perps_markets(raw: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Parse the result of phantom___perps_markets into a symbol-keyed dict."""
    result: dict[str, dict[str, Any]] = {}
    if not isinstance(raw, dict):
        return result
    items = raw.get("markets", raw.get("data", []))
    if isinstance(items, dict):
        items = list(items.values())
    for item in items:
        if not isinstance(item, dict):
            continue
        symbol = str(item.get("coin", item.get("symbol", ""))).upper()
        if symbol:
            result[symbol] = item
    return result


def format_ai_prompt(
    market_data: RichMarketData,
    equity: float,
    max_open_trades: int,
    max_candidates: int,
    prompt_id: str = "",
    active_skills: str = "",
    existing_positions: list[dict[str, Any]] | None = None,
    prior_signal_stats: list[dict[str, Any]] | None = None,
    twitter_results: list[TwitterResult] | None = None,
    hawk_signals: list | None = None,
) -> str:
    """Build the AI reasoning prompt from aggregated market data.

    Returns a structured prompt that the AI agent will use to decide trades.
    All numbers in the prompt are from live data -- no hardcoded examples.
    """
    lines = [
        "# Sol Porpoise AI Agent — Paper Trade Decision",
        "",
        f"Prompt ID: {prompt_id or 'unknown'}",
        f"Timestamp_Australia/Sydney: {datetime.now(AEST).strftime('%Y-%m-%d %H:%M:%S Australia/Sydney')}",
        "",
        "## Mission Posture",
        "You are the external AI decision delegate for the Sol Porpoise live-paper account.",
        "Act with the urgency and selectivity of an elite crypto perps trader, but obey hard paper-trading rails.",
        "Output actionable trade JSON only when the live evidence supports it. Return no trades when evidence is weak.",
        "Never invent data, never use historical simulated trades as outcomes, and never suggest live execution.",
        "",
    ]

    if active_skills:
        lines.extend([active_skills, ""])

    # Twitter CT Intel — AI agent only
    if twitter_results is not None:
        lines.extend([format_twitter_prompt_section(twitter_results), ""])

    # Hawk Breakout Signals — injected after twitter, before Account
    if hawk_signals is not None:
        lines.extend([format_hawk_prompt_section(hawk_signals), ""])

    lines.extend([
        "## Account",
        f"- Paper equity: {equity} USDC",
        f"- Max concurrent trades: {max_open_trades}",
        f"- Max candidates this scan: {max_candidates}",
        f"- Available perps balance: {market_data.account.available_usd if market_data.account else 'N/A'} USDC",
        "",
    ])

    if existing_positions:
        lines.append("## Existing Positions")
        for pos in existing_positions:
            lines.append(
                f"- {pos.get('coin', pos.get('symbol', '?'))} "
                f"{pos.get('side', pos.get('direction', '?')).upper()} "
                f"| Size: ${pos.get('sizeUsd', pos.get('notional', '?'))} "
                f"| Entry: {pos.get('entryPrice', '?')} "
                f"| Unrealized PnL: {pos.get('unrealizedPnl', '?')}"
            )
        lines.append("")

    if prior_signal_stats:
        lines.append("## Prior Signal Performance (informational only — do NOT reduce aggression)")
        for stat in prior_signal_stats[:10]:
            lines.append(
                f"- {stat.get('signal', '?')}: hit_rate={stat.get('hit_rate', 0):.0%}, "
                f"avg_R={stat.get('avg_R', 0):+.2f}, n={stat.get('n', 0)}"
            )
        lines.append("")

    # Unified market table with all available data per symbol
    all_symbols = sorted(set(
        [m.symbol for m in market_data.markets] +
        list(market_data.raw_prices.keys()) +
        list(market_data.funding_rates.keys()) +
        list(market_data.open_interest.keys()) +
        list(market_data.volume_24h.keys())
    ))
    # Build lookup by symbol for markets
    market_by_symbol: dict[str, MarketOverview] = {m.symbol: m for m in market_data.markets}

    lines.append("## Market Data")
    lines.append("| Symbol | Price | Funding Rate | Open Interest | 24h Volume | Max Lev | Pool Util |")
    lines.append("|--------|------:|-------------:|-------------:|-----------:|--------:|---------:|")
    for sym in all_symbols[:25]:
        m = market_by_symbol.get(sym)
        price = market_data.raw_prices.get(sym, m.price if m else 0)
        fr = market_data.funding_rates.get(sym, "")
        oi = market_data.open_interest.get(sym, "")
        vol = market_data.volume_24h.get(sym, "")
        lev = m.max_leverage if m else ""
        util = f"{m.pool_utilization_pct:.1f}%" if m else ""

        price_str = f"${price:,.2f}" if price else "-"
        fr_str = f"{fr:.6f}" if isinstance(fr, (int, float)) and fr != "" else "-"
        oi_str = f"${oi:,.0f}" if isinstance(oi, (int, float)) and oi != "" else "-"
        vol_str = f"${vol:,.0f}" if isinstance(vol, (int, float)) and vol != "" else "-"
        lev_str = f"{lev:.0f}x" if isinstance(lev, (int, float)) and lev != "" else "-"
        util_str = util if util else "-"
        lines.append(f"| {sym} | {price_str} | {fr_str} | {oi_str} | {vol_str} | {lev_str} | {util_str} |")
    lines.append("")

    # ATR estimates (1.5% of price as proxy when real ATR unavailable)
    lines.append("## ATR Estimates")
    lines.append("When real ATR is unavailable, a 1.5% of price proxy is used.")
    lines.append("Stop distance must be >= 0.8 * ATR from entry.")
    for sym in all_symbols[:15]:
        price = market_data.raw_prices.get(sym)
        if not price:
            m = market_by_symbol.get(sym)
            price = m.price if m else None
        if price and price > 0:
            atr_est = price * 0.015
            lines.append(f"- {sym}: ~${atr_est:,.2f} (1.5% of ${price:,.2f})")
    lines.append("")

    lines.extend([
        "## Task",
        "",
        "Analyze the live market data above and produce up to {} paper trade candidates.".format(max_candidates),
        "Use the ACTUAL prices shown in the Market Data table — do not invent or estimate prices.",
        "",
        "For each candidate, provide:",
        '  "symbol": symbol from the table above',
        '  "side": "long" or "short"',
        '  "setup_type": one of breakout, fade, vwap_reclaim, funding_fade, momentum_continuation, liquidity_sweep, or custom',
        '  "entry": passive entry price (long: at or below current price, short: at or above current price)',
        '  "stop": stop-loss price (long: below entry, short: above entry). Must be >= 0.8 * ATR from entry.',
        '  "tp1": take-profit 1 — at least 2R (2x stop distance) from entry in profit direction',
        '  "tp2": take-profit 2 — at least 3R (3x stop distance) from entry',
        '  "probability_band": "high", "medium", or "low"',
        '  "rationale": 1-2 sentence thesis',
        '  "evidence": array of 2-5 short evidence tags from the prompt',
        '  "risk_notes": main invalidation/failure mode',
        '  "data_gaps": missing evidence that reduces confidence',
        "",
        "Constraints:",
        "- Leverage range: 9-12x on {} USDC equity".format(equity),
        "- Risk per trade: 20% of equity (${:.0f} max risk)".format(equity * 0.20),
        "- Max {} concurrent trades".format(max_open_trades),
        "- Do NOT duplicate existing positions in the same symbol/side",
        "- Do NOT reduce aggression based on prior signal stats",
        "",
        "Respond with ONLY this JSON object shape (no markdown fences, no commentary before or after):",
        "{",
        f'  "prompt_id": "{prompt_id or "unknown"}",',
        '  "agent": "hermes" or "droid",',
        '  "decision_ts_Australia/Sydney": "YYYY-MM-DD HH:MM:SS Australia/Sydney",',
        '  "no_trade_reason": "" if trades exist, otherwise a concise reason,',
        '  "trades": [{...}, {...}]',
        "}",
        "",
        "If no high-quality setups exist, respond with the same object and an empty trades array.",
    ])

    return "\n".join(lines)
