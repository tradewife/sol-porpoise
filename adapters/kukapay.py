"""Kukapay News adapter for catalyst signal extraction.

Publicly hosted MCP server: https://news.kukapay.com/mcp
Provides: latest news, trending topics/entities, sentiment trends.
No API key required.

Used by engine/signals.py to produce the catalyst signal component.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

import requests

from adapters.base import DataPoint, Provenance, SourceTier
from adapters.normalizer import aest_now_iso, make_provenance


KUKAPAY_MCP_URL = "https://news.kukapay.com/mcp"


@dataclass
class NewsItem:
    title: str
    published_at: str = ""
    topic: str = ""
    entity: str = ""
    keyword: str = ""
    sentiment: float = 0.0  # -1 to +1
    source: str = ""


@dataclass
class SentimentTrend:
    keyword: str
    days: list[str] = field(default_factory=list)
    scores: list[float] = field(default_factory=list)  # -100 to +100


@dataclass
class TrendingTopic:
    topic: str
    count: int = 0


class KukapayNewsAdapter:
    """Client for Kukapay News MCP server (streamable HTTP transport)."""

    def __init__(self, url: str = KUKAPAY_MCP_URL, timeout: int = 30) -> None:
        self.url = url.rstrip("/")
        self.timeout = timeout
        self._session = requests.Session()
        self._session.headers.update({
            "Content-Type": "application/json",
            "Accept": "application/json",
        })
        self._cache: dict[str, tuple[float, Any]] = {}
        self._cache_ttl = 300  # 5 minutes

    def _call_tool(self, tool_name: str, arguments: dict[str, Any] | None = None) -> Any:
        """Call an MCP tool via streamable HTTP transport."""
        cache_key = f"{tool_name}:{json.dumps(arguments or {}, sort_keys=True)}"
        now = time.monotonic()
        if cache_key in self._cache:
            ts, cached = self._cache[cache_key]
            if now - ts < self._cache_ttl:
                return cached

        payload = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {
                "name": tool_name,
                "arguments": arguments or {},
            },
        }

        resp = self._session.post(self.url, json=payload, timeout=self.timeout)
        resp.raise_for_status()
        data = resp.json()

        # MCP responses wrap results in result.content array
        result = data.get("result", {})
        content = result.get("content", [])
        if isinstance(content, list) and content:
            text = content[0].get("text", "[]")
            try:
                parsed = json.loads(text)
            except json.JSONDecodeError:
                parsed = text
        else:
            parsed = result

        self._cache[cache_key] = (now, parsed)
        return parsed

    def get_latest_news(
        self,
        days: int = 1,
        limit: int = 20,
        keyword: str | None = None,
        topic: str | None = None,
        entity: str | None = None,
    ) -> list[NewsItem]:
        """Fetch recent news items."""
        args: dict[str, Any] = {"days": days, "limit": limit}
        if keyword:
            args["keyword"] = keyword
        if topic:
            args["topic"] = topic
        if entity:
            args["entity"] = entity

        raw = self._call_tool("get_latest_news", args)
        items: list[NewsItem] = []

        if isinstance(raw, list):
            for item in raw:
                if isinstance(item, dict):
                    items.append(NewsItem(
                        title=item.get("title", ""),
                        published_at=item.get("published_at", ""),
                        topic=item.get("topic", ""),
                        entity=item.get("entity", ""),
                        keyword=keyword or "",
                        sentiment=self._parse_sentiment(item),
                        source=item.get("source", ""),
                    ))
        return items

    def get_sentiment_trend(
        self,
        days: int = 7,
        keyword: str | None = None,
        topic: str | None = None,
        entity: str | None = None,
    ) -> SentimentTrend | None:
        """Fetch daily sentiment scores."""
        args: dict[str, Any] = {"days": days}
        if keyword:
            args["keyword"] = keyword
        if topic:
            args["topic"] = topic
        if entity:
            args["entity"] = entity

        raw = self._call_tool("get_sentiment_trend", args)
        if isinstance(raw, dict):
            return SentimentTrend(
                keyword=keyword or topic or entity or "unknown",
                days=raw.get("days", []),
                scores=raw.get("scores", []),
            )
        if isinstance(raw, list) and raw:
            # Might be a list of {date, score} dicts
            days_list = [str(r.get("date", "")) for r in raw if isinstance(r, dict)]
            scores_list = [float(r.get("score", 0)) for r in raw if isinstance(r, dict)]
            return SentimentTrend(
                keyword=keyword or topic or entity or "unknown",
                days=days_list,
                scores=scores_list,
            )
        return None

    def list_trending_topics(self, days: int = 1, limit: int = 10) -> list[TrendingTopic]:
        """Fetch trending topics."""
        raw = self._call_tool("list_trending_topics", {"days": days, "limit": limit})
        topics: list[TrendingTopic] = []
        if isinstance(raw, list):
            for item in raw:
                if isinstance(item, dict):
                    topics.append(TrendingTopic(
                        topic=item.get("topic", item.get("name", "")),
                        count=item.get("count", item.get("mentions", 0)),
                    ))
        return topics

    def extract_catalyst_signal(
        self,
        symbol: str,
        days: int = 1,
    ) -> tuple[float, float, str]:
        """Extract catalyst signal for a symbol.

        Returns (value, confidence, label).
        - value: -1 to +1 (bearish to bullish catalyst)
        - confidence: 0 to 1
        - label: descriptive string
        """
        try:
            news = self.get_latest_news(days=days, limit=10, keyword=symbol)
        except Exception:
            return 0.0, 0.0, "no_data"

        if not news:
            return 0.0, 0.1, "no_recent_news"

        # Score based on sentiment of recent articles
        total_sentiment = sum(n.sentiment for n in news)
        avg_sentiment = total_sentiment / len(news) if news else 0.0

        # Confidence scales with article count (more articles = higher confidence)
        confidence = min(0.8, len(news) / 10.0)

        # Label
        if avg_sentiment > 0.3:
            label = f"bullish_catalyst_{len(news)}_articles"
        elif avg_sentiment < -0.3:
            label = f"bearish_catalyst_{len(news)}_articles"
        else:
            label = f"neutral_catalyst_{len(news)}_articles"

        return avg_sentiment, confidence, label

    def to_datapoints(self, symbol: str, news: list[NewsItem]) -> list[DataPoint]:
        """Convert news items to DataPoints for the signal pipeline."""
        prov = make_provenance(
            source_name="KukapayNews",
            source_tier=SourceTier.OPEN,
            source_link="https://news.kukapay.com",
            confidence=0.65,
        )
        points: list[DataPoint] = []
        for n in news:
            points.append(DataPoint(
                symbol=symbol,
                metric="catalyst_news",
                value=n.sentiment,
                provenance=prov,
                attrs={
                    "title": n.title[:200],
                    "published_at": n.published_at,
                    "source": n.source,
                },
            ))
        return points

    @staticmethod
    def _parse_sentiment(item: dict[str, Any]) -> float:
        """Try to extract sentiment from a news item."""
        # Try explicit sentiment field
        s = item.get("sentiment")
        if isinstance(s, (int, float)):
            return max(-1.0, min(1.0, float(s)))

        # Try votes
        votes = item.get("votes", {})
        if isinstance(votes, dict):
            pos = votes.get("positive", 0)
            neg = votes.get("negative", 0)
            total = pos + neg
            if total > 0:
                return (pos - neg) / total

        # Try title keywords as fallback
        title = item.get("title", "").lower()
        bullish_words = ["rally", "surge", "breakout", "upgrade", "adoption", "bullish", "pump", "moon"]
        bearish_words = ["crash", "dump", "hack", "ban", "sec", "bearish", "rug", "exploit", "fud"]
        bullish = sum(1 for w in bullish_words if w in title)
        bearish = sum(1 for w in bearish_words if w in title)
        total = bullish + bearish
        if total > 0:
            return (bullish - bearish) / total

        return 0.0
