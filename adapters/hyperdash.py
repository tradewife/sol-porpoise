"""Hyperdash whale cohort adapter: GraphQL client for cohort positioning data.

API Discovery (2026-06-03):
  Discovered via agent-browser on https://hyperdash.com/explore/cohorts.
  The SPA frontend uses Apollo Client with a GraphQL API at api.hyperdash.com.

  Endpoint: POST https://api.hyperdash.com/graphql
  Auth: none (public)
  Query: CohortSummary → analytics.cohortSummary.sizeCohorts
  Response schema:
    sizeCohorts: [
      {
        id: "whale" | "large" | "apex" | "medium" | "small",
        label: str,
        range: str,
        totalTraders: int,
        longNotional: float,      # total long OI across all assets
        shortNotional: float,     # total short OI across all assets
        topMarkets(limit: N): [
          { ticker: "SOL", longNotional: float, shortNotional: float },
          ...
        ]
      }
    ]

  SOL-specific data is extracted from topMarkets with limit=20 to ensure
  SOL appears in the results. For each target cohort, we compute:
    - whale_cohort_long_pct: SOL long / (SOL long + SOL short) * 100
    - cohort_direction: "long" if >55%, "short" if <45%, "neutral" if 45-55%
    - cohort_oi_usd: SOL long + SOL short (aggregate OI for SOL in cohort)
"""

from __future__ import annotations

import time
from typing import Any

import httpx

from adapters.base import AdapterHealth, DataPoint, Provenance, SourceTier
from adapters.normalizer import aest_now_iso, make_provenance

# ---------------------------------------------------------------------------
# API constants
# ---------------------------------------------------------------------------

GRAPHQL_URL = "https://api.hyperdash.com/graphql"
TIMEOUT_S = 30.0

# GraphQL query for cohort summary with per-asset breakdown
COHORT_SUMMARY_QUERY = """
query CohortSummary {
  analytics {
    cohortSummary {
      timestamp
      totalTraders
      sizeCohorts {
        id
        label
        range
        totalTraders
        longNotional
        shortNotional
        topMarkets(limit: 20) {
          ticker
          longNotional
          shortNotional
        }
      }
    }
  }
}
"""

# Target cohorts: Hyperdash size-cohort IDs mapped to feature tier names
TARGET_COHORTS: dict[str, str] = {
    "whale": "Large Whale ($1M-$5M)",   # Hyperdash: $1M - $5M
    "large": "Whale ($100K-$1M)",       # Hyperdash: $100K - $1M
}


def _compute_cohort_direction(long_pct: float) -> str:
    """Compute cohort direction from long percentage.

    Thresholds:
      long:   > 55%
      short:  < 45%
      neutral: 45% <= x <= 55%
    """
    if long_pct > 55.0:
        return "long"
    elif long_pct < 45.0:
        return "short"
    else:
        return "neutral"


class HyperdashAdapter:
    """GraphQL client for Hyperdash whale cohort positioning data.

    Extracts SOL-specific whale cohort metrics from the Hyperdash
    CohortSummary GraphQL API. Targets the 'whale' ($1M-$5M) and
    'large' ($100K-$1M) size cohorts.

    Provenance: source_tier=OPEN, confidence=0.75.
    Graceful degradation: returns empty list on any failure.
    """

    def __init__(self, graphql_url: str = GRAPHQL_URL) -> None:
        self.graphql_url = graphql_url.rstrip("/")
        self._client = httpx.Client(
            timeout=TIMEOUT_S,
            headers={
                "User-Agent": (
                    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/148.0.0.0 Safari/537.36"
                ),
                "Accept": "application/json",
                "Content-Type": "application/json",
                "Origin": "https://hyperdash.com",
                "Referer": "https://hyperdash.com/explore/cohorts",
            },
        )
        self._last_health: AdapterHealth | None = None

    def provenance(self) -> Provenance:
        return make_provenance(
            source_name="Hyperdash",
            source_tier=SourceTier.OPEN,
            source_link=self.graphql_url,
            confidence=0.75,
        )

    def health_check(self) -> AdapterHealth:
        try:
            start = time.monotonic()
            r = self._client.post(
                self.graphql_url,
                json={"query": COHORT_SUMMARY_QUERY},
            )
            latency = (time.monotonic() - start) * 1000
            healthy = r.status_code == 200
            self._last_health = AdapterHealth(
                name="Hyperdash",
                healthy=healthy,
                latency_ms=latency,
                last_success_ts=aest_now_iso() if healthy else None,
                last_failure_ts=None if healthy else aest_now_iso(),
                error_message=None if healthy else f"HTTP {r.status_code}: {r.text[:200]}",
            )
        except Exception as e:
            self._last_health = AdapterHealth(
                name="Hyperdash",
                healthy=False,
                last_failure_ts=aest_now_iso(),
                error_message=str(e),
            )
        return self._last_health  # type: ignore[return-value]

    async def fetch(self, params: dict[str, Any] | None = None) -> list[DataPoint]:
        """Fetch whale cohort data from Hyperdash GraphQL API.

        Returns DataPoints with metrics:
          - whale_cohort_long_pct: float (0-100)
          - cohort_direction: "long" | "short" | "neutral"
          - cohort_oi_usd: float (aggregate SOL OI in USD)

        For each of 2 target tiers (whale, large).
        SOL-only filter applied.
        Returns empty list on any failure.
        """
        try:
            r = self._client.post(
                self.graphql_url,
                json={"query": COHORT_SUMMARY_QUERY},
            )
            r.raise_for_status()
            data = r.json()
            return self._parse_response(data)
        except Exception:
            return []

    def _parse_response(self, data: dict[str, Any]) -> list[DataPoint]:
        """Parse the GraphQL response into DataPoints for SOL cohort metrics."""
        analytics = data.get("data", {}).get("analytics", {})
        cohort_summary = analytics.get("cohortSummary", {})
        size_cohorts = cohort_summary.get("sizeCohorts", [])

        if not size_cohorts:
            return []

        prov = self.provenance()
        points: list[DataPoint] = []

        for cohort in size_cohorts:
            cohort_id = cohort.get("id", "")
            if cohort_id not in TARGET_COHORTS:
                continue

            tier_label = TARGET_COHORTS[cohort_id]
            top_markets = cohort.get("topMarkets", [])

            # Find SOL in topMarkets (SOL-only filter)
            sol_market: dict[str, Any] | None = None
            for market in top_markets:
                if market.get("ticker", "").upper() == "SOL":
                    sol_market = market
                    break

            if sol_market is None:
                # SOL not in top 20 markets for this cohort — skip
                continue

            sol_long = float(sol_market.get("longNotional", 0))
            sol_short = float(sol_market.get("shortNotional", 0))
            sol_total = sol_long + sol_short

            if sol_total <= 0:
                continue

            long_pct = (sol_long / sol_total) * 100.0
            direction = _compute_cohort_direction(long_pct)
            oi_usd = sol_total

            cohort_range = cohort.get("range", "")
            total_traders = cohort.get("totalTraders", 0)

            attrs: dict[str, Any] = {
                "cohort_id": cohort_id,
                "tier": tier_label,
                "range": cohort_range,
                "total_traders": total_traders,
                "cohort_long_notional": sol_long,
                "cohort_short_notional": sol_short,
            }

            points.append(DataPoint(
                symbol="SOL",
                metric="whale_cohort_long_pct",
                value=round(long_pct, 2),
                provenance=prov,
                attrs=attrs,
            ))

            points.append(DataPoint(
                symbol="SOL",
                metric="cohort_direction",
                value=direction,
                provenance=prov,
                attrs=attrs,
            ))

            points.append(DataPoint(
                symbol="SOL",
                metric="cohort_oi_usd",
                value=round(oi_usd, 2),
                provenance=prov,
                attrs=attrs,
            ))

        return points
