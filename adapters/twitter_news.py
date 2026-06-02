"""Thin adapter for twitter-cli (https://github.com/public-clis/twitter-cli).
AI-agent use only. Never called by the deterministic scan loop.

Rate-limit strategy:
  - One CLI invocation per symbol per scan cycle (not per tick).
  - Max MAX_SYMBOLS_PER_RUN symbols per call to fetch().
  - On any CLI failure, subprocess error, or timeout: returns empty list
    and sets healthy=False. Caller annotates the prompt section as
    "Twitter data unavailable this cycle" — no retry, no exception raised.
  - Cache: results keyed by (symbol, UTC-minute-bucket) to avoid duplicate
    CLI calls if fetch() is somehow called twice in the same cycle.
"""

from __future__ import annotations

import subprocess
import time
from dataclasses import dataclass, field
from typing import Any

import yaml

from adapters.base import AdapterHealth, DataPoint, Provenance, SourceTier
from adapters.normalizer import make_provenance

# Hard cap — do not raise this without understanding X rate limits.
MAX_SYMBOLS_PER_RUN = 5
CLI_TIMEOUT_SECONDS = 15


@dataclass
class TwitterResult:
    """Parsed result for a single symbol."""
    symbol: str
    tweets: list[dict[str, Any]] = field(default_factory=list)
    sentiment_summary: str = "unknown"   # "bullish" | "bearish" | "mixed" | "unknown"
    top_accounts: list[str] = field(default_factory=list)
    available: bool = True
    error: str = ""


def _sentiment_from_tweets(tweets: list[dict[str, Any]]) -> str:
    """
    Simple keyword heuristic over tweet text.
    Returns "bullish", "bearish", "mixed", or "unknown".
    Not a signal — purely a reading aid for the AI.
    """
    bull_words = {"long", "buy", "pump", "moon", "breakout", "bullish", "calls", "rip"}
    bear_words = {"short", "sell", "dump", "rekt", "crash", "bearish", "puts", "fade"}
    bull, bear = 0, 0
    for t in tweets:
        text = str(t.get("text", t.get("content", ""))).lower()
        bull += sum(1 for w in bull_words if w in text)
        bear += sum(1 for w in bear_words if w in text)
    if bull == 0 and bear == 0:
        return "unknown"
    if bull > bear * 1.5:
        return "bullish"
    if bear > bull * 1.5:
        return "bearish"
    return "mixed"


class TwitterNewsAdapter:
    """
    Calls twitter-cli to search recent CT activity for a list of symbols.
    Results are AI-prompt context only — not scored, not passed to signals.py.
    """

    def __init__(self, cli_path: str = "") -> None:
        # Default to the installed twitter-cli venv binary
        if not cli_path:
            cli_path = "/home/kt/twitter-cli/.venv/bin/twitter"
        self.cli_path = cli_path
        self._cache: dict[str, tuple[float, TwitterResult]] = {}
        self._cache_ttl_seconds = 300  # 5-minute bucket

    def _cache_key(self, symbol: str) -> str:
        bucket = int(time.time() // self._cache_ttl_seconds)
        return f"{symbol}:{bucket}"

    def fetch_symbol(self, symbol: str) -> TwitterResult:
        """Fetch CT intel for a single symbol. Returns cached result if fresh."""
        key = self._cache_key(symbol)
        if key in self._cache:
            return self._cache[key][1]

        query = f"${symbol} perps OR futures OR trade crypto"
        try:
            result = subprocess.run(
                [
                    self.cli_path, "search", query,
                    "--yaml", "--max", "10",
                    "-t", "latest",
                ],
                capture_output=True, text=True, timeout=CLI_TIMEOUT_SECONDS,
            )
            if result.returncode != 0:
                raise RuntimeError(result.stderr.strip() or "non-zero exit")

            raw = yaml.safe_load(result.stdout) or {}

            # twitter-cli wraps results in {ok: true, data: [...]}
            if isinstance(raw, dict):
                tweets = raw.get("data", [])
                if not isinstance(tweets, list):
                    tweets = []
            elif isinstance(raw, list):
                tweets = raw
            else:
                tweets = []

            tweets = tweets[:10]

            # Extract account names from nested author dict or flat fields
            top_accounts: list[str] = []
            seen: set[str] = set()
            for t in tweets:
                author = t.get("author", {})
                name = ""
                if isinstance(author, dict):
                    name = author.get("screenName", author.get("username", ""))
                elif isinstance(author, str):
                    name = author
                else:
                    name = str(t.get("user", t.get("username", "")))
                if name and name not in seen:
                    seen.add(name)
                    top_accounts.append(name)
            top_accounts = top_accounts[:5]

            tr = TwitterResult(
                symbol=symbol,
                tweets=tweets,
                sentiment_summary=_sentiment_from_tweets(tweets),
                top_accounts=top_accounts,
                available=True,
            )
        except Exception as exc:
            tr = TwitterResult(symbol=symbol, available=False, error=str(exc))

        self._cache[key] = (time.time(), tr)
        return tr

    def fetch(self, params: dict[str, Any]) -> list[DataPoint]:
        """
        DataAdapter protocol: fetch() for multiple symbols.
        params: {"symbols": ["SOL", "BTC", ...]}
        Returns DataPoints with metric="twitter_ct_intel" per symbol.
        Only called from the AI scan loop — never from the deterministic loop.
        """
        symbols = params.get("symbols", [])[:MAX_SYMBOLS_PER_RUN]
        points: list[DataPoint] = []
        prov = self.provenance()

        for sym in symbols:
            tr = self.fetch_symbol(sym)
            value = {
                "available": tr.available,
                "sentiment": tr.sentiment_summary,
                "tweet_count": len(tr.tweets),
                "top_accounts": tr.top_accounts,
                "sample_texts": [
                    str(t.get("text", t.get("content", "")))[:200]
                    for t in tr.tweets[:5]
                ],
                "error": tr.error,
            }
            points.append(DataPoint(
                symbol=sym,
                metric="twitter_ct_intel",
                value=value,
                provenance=prov,
            ))
        return points

    def provenance(self) -> Provenance:
        return make_provenance(
            source_name="twitter-cli/X-CT",
            source_tier=SourceTier.OPEN,
            source_link="https://github.com/public-clis/twitter-cli",
            confidence=0.35,  # Low: CT is noisy; AI contextual use only
        )

    def health_check(self) -> AdapterHealth:
        try:
            result = subprocess.run(
                [self.cli_path, "--version"],
                capture_output=True, text=True, timeout=5,
            )
            healthy = result.returncode == 0
            return AdapterHealth(name="twitter-cli", healthy=healthy)
        except Exception as exc:
            return AdapterHealth(name="twitter-cli", healthy=False, error_message=str(exc))


def format_twitter_prompt_section(results: list[TwitterResult]) -> str:
    """
    Format fetched TwitterResults into a prompt section string.
    This is the ONLY function called by mcp_data.py / run_scan.py.
    Returns a string to be appended to the AI prompt.
    If all results are unavailable, returns a single unavailability note.
    """
    available = [r for r in results if r.available]
    if not available:
        return "## Twitter CT Intel\nUnavailable this cycle (CLI timeout or rate limit). Omit from evidence.\n"

    lines = [
        "## Twitter CT Intel",
        "Source: twitter-cli/X (public-clis). Confidence: low. Use as context only — not a scored signal.",
        "",
    ]
    for r in available:
        lines.append(f"### {r.symbol}")
        lines.append(f"- CT Sentiment: {r.sentiment_summary}")
        lines.append(f"- Recent accounts active: {', '.join(r.top_accounts) if r.top_accounts else 'none identified'}")
        for i, t in enumerate(r.tweets[:3], 1):
            text = str(t.get("text", t.get("content", ""))).replace("\n", " ")[:180]
            lines.append(f"- Tweet {i}: {text}")
        lines.append("")
    return "\n".join(lines)
