"""Vulcan (Phoenix Perps) adapter for paper trading execution.

Wraps the vulcan CLI to provide:
- Paper trade execution (market buy/sell with real prices)
- TP/SL trigger management
- Position and account status
- Market data (tickers, funding, OI)
- Multi-account isolation via per-account paper state files

The vulcan CLI must be installed: https://github.com/Ellipsis-Labs/vulcan-cli
"""

from __future__ import annotations

import json
import shutil
import subprocess
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from zoneinfo import ZoneInfo

AEST = ZoneInfo("Australia/Sydney")


@dataclass
class VulcanFill:
    fill_id: str
    symbol: str
    side: str
    price: float
    size_tokens: float
    fee: float
    timestamp: str


@dataclass
class VulcanPosition:
    symbol: str
    side: str
    size_tokens: float
    entry_price: float
    mark_price: float
    notional_usdc: float
    unrealized_pnl: float
    leverage: float
    liquidation_price: float | None = None


@dataclass
class VulcanTrigger:
    trigger_id: str
    symbol: str
    kind: str  # "take_profit" or "stop_loss"
    trigger_price: float
    size_tokens: float
    created_at: str = ""


@dataclass
class VulcanAccount:
    balance: float
    equity: float
    position_notional_usdc: float
    unrealized_pnl: float
    realized_pnl: float
    fees_paid: float
    open_positions: int
    open_orders: int
    triggers: int
    fills: int


@dataclass
class VulcanTicker:
    symbol: str
    mark_price: float
    mid_price: float
    oracle_price: float
    volume_24h_usd: float
    open_interest: float
    funding_rate: float


class VulcanAdapter:
    """Vulcan CLI adapter for Phoenix Perps paper trading."""

    def __init__(
        self,
        vulcan_bin: str = "vulcan",
        account_id: str = "deterministic",
        project_root: Path | None = None,
    ):
        self.vulcan_bin = vulcan_bin
        self.account_id = account_id
        self.project_root = project_root or Path.cwd()
        self._state_path = self.project_root / "accounts" / account_id / "data" / "vulcan-paper-state.json"
        self._vulcan_state_path = Path.home() / ".vulcan" / "paper-state.json"

    def _run(self, args: list[str], timeout: int = 60) -> dict[str, Any]:
        """Run a vulcan CLI command with JSON output."""
        cmd = [self.vulcan_bin] + args + ["-o", "json"]
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        if result.returncode != 0:
            raise RuntimeError(
                f"vulcan {' '.join(args)} failed (exit {result.returncode}): "
                f"{result.stderr[:500]}"
            )
        try:
            return json.loads(result.stdout)
        except json.JSONDecodeError:
            raise RuntimeError(f"vulcan returned non-JSON: {result.stdout[:500]}")

    def _swap_in(self) -> None:
        """Copy account's paper state into vulcan's expected location."""
        if self._state_path.exists():
            shutil.copy2(self._state_path, self._vulcan_state_path)

    def _swap_out(self) -> None:
        """Save vulcan's current paper state back to account directory."""
        if self._vulcan_state_path.exists():
            self._state_path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(self._vulcan_state_path, self._state_path)

    def _exec(self, args: list[str], timeout: int = 60) -> dict[str, Any]:
        """Swap in state, run command, swap out state."""
        self._swap_in()
        try:
            result = self._run(args, timeout=timeout)
        finally:
            self._swap_out()
        return result

    def init(self, balance: float = 1000.0) -> VulcanAccount:
        """Initialize or reset the paper trading account."""
        resp = self._exec(["paper", "init", "--balance", str(balance), "-y"])
        return self._parse_account(resp.get("data", {}).get("state", {}))

    def status(self) -> VulcanAccount:
        """Get current paper account status."""
        resp = self._exec(["paper", "status"])
        state = resp.get("data", {})
        # Some commands nest state under "state" key, others flatten into "data"
        if "state" in state and isinstance(state["state"], dict):
            state = state["state"]
        return self._parse_account(state)

    def buy(self, symbol: str, notional_usdc: float, order_type: str = "market") -> VulcanFill:
        """Open a paper long position."""
        resp = self._exec([
            "paper", "buy", symbol,
            "--notional-usdc", str(notional_usdc),
            "--type", order_type,
        ])
        fill_data = resp.get("data", {}).get("fill", {})
        return VulcanFill(
            fill_id=fill_data.get("fill_id", ""),
            symbol=fill_data.get("symbol", symbol),
            side=fill_data.get("side", "buy"),
            price=float(fill_data.get("price", 0)),
            size_tokens=float(fill_data.get("size_tokens", 0)),
            fee=float(fill_data.get("fee", 0)),
            timestamp=fill_data.get("timestamp", ""),
        )

    def sell(self, symbol: str, notional_usdc: float, order_type: str = "market") -> VulcanFill:
        """Open a paper short position."""
        resp = self._exec([
            "paper", "sell", symbol,
            "--notional-usdc", str(notional_usdc),
            "--type", order_type,
        ])
        fill_data = resp.get("data", {}).get("fill", {})
        return VulcanFill(
            fill_id=fill_data.get("fill_id", ""),
            symbol=fill_data.get("symbol", symbol),
            side=fill_data.get("side", "sell"),
            price=float(fill_data.get("price", 0)),
            size_tokens=float(fill_data.get("size_tokens", 0)),
            fee=float(fill_data.get("fee", 0)),
            timestamp=fill_data.get("timestamp", ""),
        )

    def set_tpsl(
        self,
        symbol: str,
        tp: float | None = None,
        sl: float | None = None,
    ) -> list[VulcanTrigger]:
        """Set take-profit and/or stop-loss on an existing paper position."""
        args = ["paper", "set-tpsl", symbol]
        if tp is not None:
            args.extend(["--tp", str(tp)])
        if sl is not None:
            args.extend(["--sl", str(sl)])
        resp = self._exec(args)
        triggers = []
        for t in resp.get("data", {}).get("triggers", []):
            triggers.append(VulcanTrigger(
                trigger_id=t.get("trigger_id", ""),
                symbol=t.get("symbol", symbol),
                kind=t.get("kind", ""),
                trigger_price=float(t.get("trigger_price", 0)),
                size_tokens=float(t.get("size_tokens", 0)),
                created_at=t.get("created_at", ""),
            ))
        return triggers

    def positions(self) -> list[VulcanPosition]:
        """List open paper positions."""
        resp = self._exec(["paper", "positions"])
        positions = []
        pos_list = resp.get("data", {}).get("positions", [])
        if not pos_list:
            pos_list = resp.get("data", []) if isinstance(resp.get("data"), list) else []
        for p in pos_list:
            positions.append(VulcanPosition(
                symbol=p.get("symbol", ""),
                side=p.get("side", ""),
                size_tokens=float(p.get("size_tokens", 0)),
                entry_price=float(p.get("entry_price", 0)),
                mark_price=float(p.get("mark_price", 0)),
                notional_usdc=float(p.get("notional_usdc", 0)),
                unrealized_pnl=float(p.get("unrealized_pnl", 0)),
                leverage=float(p.get("leverage", 1)),
                liquidation_price=p.get("liquidation_price"),
            ))
        return positions

    def triggers(self) -> list[VulcanTrigger]:
        """List active paper TP/SL triggers."""
        resp = self._exec(["paper", "triggers"])
        trig_list = resp.get("data", {}).get("triggers", [])
        if not trig_list:
            trig_list = resp.get("data", []) if isinstance(resp.get("data"), list) else []
        triggers = []
        for t in trig_list:
            triggers.append(VulcanTrigger(
                trigger_id=t.get("trigger_id", ""),
                symbol=t.get("symbol", ""),
                kind=t.get("kind", ""),
                trigger_price=float(t.get("trigger_price", 0)),
                size_tokens=float(t.get("size_tokens", 0)),
                created_at=t.get("created_at", ""),
            ))
        return triggers

    def cancel_tpsl(self, symbol: str) -> dict[str, Any]:
        """Cancel all TP/SL triggers for a symbol."""
        return self._exec(["paper", "cancel-tpsl", symbol, "-y"])

    def reconcile(self) -> dict[str, Any]:
        """Reconcile paper state against live market prices."""
        return self._exec(["paper", "reconcile"])

    def ticker(self, symbol: str) -> VulcanTicker | None:
        """Get market ticker for a symbol (no state swap needed)."""
        try:
            resp = self._run(["market", "ticker", symbol])
            d = resp.get("data", {})
            return VulcanTicker(
                symbol=d.get("symbol", symbol),
                mark_price=float(d.get("mark_price", 0)),
                mid_price=float(d.get("mid_price", 0)),
                oracle_price=float(d.get("oracle_price", 0)),
                volume_24h_usd=float(d.get("volume_24h_usd", 0)),
                open_interest=float(d.get("open_interest", 0)),
                funding_rate=float(d.get("funding_rate", 0)),
            )
        except Exception:
            return None

    def market_list(self) -> list[dict[str, Any]]:
        """List all available Phoenix markets."""
        resp = self._run(["market", "list"])
        return resp.get("data", {}).get("markets", [])

    def close_position(self, symbol: str, size_tokens: float | None = None) -> VulcanFill | None:
        """Close a paper position. If size_tokens is None, closes full position."""
        args = ["paper", "sell", symbol]  # sell closes longs
        pos_list = self.positions()
        target = None
        for p in pos_list:
            if p.symbol == symbol:
                target = p
                break
        if not target:
            return None

        if size_tokens is None:
            size_tokens = target.size_tokens

        if target.side == "long":
            args = ["paper", "sell", symbol, "--size", str(size_tokens)]
        else:
            args = ["paper", "buy", symbol, "--size", str(size_tokens)]

        resp = self._exec(args)
        fill_data = resp.get("data", {}).get("fill", {})
        if not fill_data:
            return None
        return VulcanFill(
            fill_id=fill_data.get("fill_id", ""),
            symbol=fill_data.get("symbol", symbol),
            side=fill_data.get("side", ""),
            price=float(fill_data.get("price", 0)),
            size_tokens=float(fill_data.get("size_tokens", 0)),
            fee=float(fill_data.get("fee", 0)),
            timestamp=fill_data.get("timestamp", ""),
        )

    @staticmethod
    def _parse_account(state: dict[str, Any]) -> VulcanAccount:
        return VulcanAccount(
            balance=float(state.get("balance", 0)),
            equity=float(state.get("equity", 0)),
            position_notional_usdc=float(state.get("position_notional_usdc", 0)),
            unrealized_pnl=float(state.get("unrealized_pnl", 0)),
            realized_pnl=float(state.get("realized_pnl", 0)),
            fees_paid=float(state.get("fees_paid", 0)),
            open_positions=int(state.get("open_positions", 0)),
            open_orders=int(state.get("open_orders", 0)),
            triggers=int(state.get("triggers", 0)),
            fills=int(state.get("fills", 0)),
        )

    @staticmethod
    def is_available() -> bool:
        """Check if vulcan CLI is installed."""
        return shutil.which("vulcan") is not None
