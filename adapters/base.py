"""Base adapter types: DataAdapter protocol, DataPoint, Provenance, AdapterHealth."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Protocol, runtime_checkable


class SourceTier(str, Enum):
    OPEN = "Open"
    PAID = "Paid"
    PROPRIETARY = "Proprietary"
    ON_CHAIN = "On-chain"
    HL_NATIVE = "HL-native"
    SOLANA_NATIVE = "Solana-native"
    INTERNAL = "Internal"
    DERIVED = "Derived"


@dataclass(frozen=True)
class Provenance:
    source_name: str
    source_tier: SourceTier
    source_link: str  # URL or "[no-link]"
    source_ts: str  # ISO timestamp from the source
    fetched_ts_aest: str  # ISO timestamp when fetched (Australia/Sydney)
    confidence: float  # 0.0 to 1.0

    def __post_init__(self) -> None:
        if not 0.0 <= self.confidence <= 1.0:
            raise ValueError(f"confidence must be 0.0-1.0, got {self.confidence}")


@dataclass
class DataPoint:
    symbol: str
    metric: str  # e.g., "funding_rate", "mark_price", "volume_24h", "oi"
    value: Any
    provenance: Provenance
    attrs: dict[str, Any] = field(default_factory=dict)

    def to_evidence_row(self) -> dict[str, Any]:
        return {
            "source_name": self.provenance.source_name,
            "source_tier": self.provenance.source_tier.value,
            "source_link_or_[no-link]": self.provenance.source_link,
            "source_ts": self.provenance.source_ts,
            "fetched_ts_Australia/Sydney": self.provenance.fetched_ts_aest,
            "confidence_0to1": self.provenance.confidence,
            "symbol": self.symbol,
            "metric": self.metric,
            "value": self.value,
        }


@dataclass
class AdapterHealth:
    name: str
    healthy: bool
    latency_ms: float | None = None
    last_success_ts: str | None = None
    last_failure_ts: str | None = None
    error_message: str | None = None


@runtime_checkable
class DataAdapter(Protocol):
    async def fetch(self, params: dict[str, Any]) -> list[DataPoint]: ...
    def provenance(self) -> Provenance: ...
    def health_check(self) -> AdapterHealth: ...
