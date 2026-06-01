"""Source health tracker and signal outcome scoring."""

from __future__ import annotations

import csv
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from adapters.normalizer import aest_now_iso


SOURCE_HEALTH_HEADER = (
    "source_name,source_tier,last_success_ts_Australia/Sydney,"
    "last_failure_ts_Australia/Sydney,latency_ms,freshness_sec,"
    "schema_version,status,known_issues,fallback,confidence_adjustment"
)

SIGNAL_OUTCOMES_HEADER = "signal,hit_rate,avg_R,n,last_updated_Australia/Sydney"


@dataclass
class SourceHealthRecord:
    source_name: str
    source_tier: str
    last_success_ts: str = ""
    last_failure_ts: str = ""
    latency_ms: float | None = None
    freshness_sec: float | None = None
    schema_version: str = ""
    status: str = "unknown"
    known_issues: str = ""
    fallback: str = ""
    confidence_adjustment: float = 1.0

    def to_csv_row(self) -> str:
        return (
            f"{self.source_name},{self.source_tier},{self.last_success_ts},"
            f"{self.last_failure_ts},{self.latency_ms or ''},{self.freshness_sec or ''},"
            f"{self.schema_version},{self.status},{self.known_issues},"
            f"{self.fallback},{self.confidence_adjustment}"
        )


class SourceHealthTracker:
    def __init__(self, csv_path: str | Path = "ledgers/source_health.csv") -> None:
        self.csv_path = Path(csv_path)

    def record_success(
        self, source_name: str, source_tier: str,
        latency_ms: float | None = None,
        freshness_sec: float | None = None,
    ) -> None:
        record = SourceHealthRecord(
            source_name=source_name,
            source_tier=source_tier,
            last_success_ts=aest_now_iso(),
            latency_ms=latency_ms,
            freshness_sec=freshness_sec,
            status="healthy",
            confidence_adjustment=1.0,
        )
        self._upsert(record)

    def record_failure(
        self, source_name: str, source_tier: str,
        error: str = "",
    ) -> None:
        record = SourceHealthRecord(
            source_name=source_name,
            source_tier=source_tier,
            last_failure_ts=aest_now_iso(),
            status="degraded",
            known_issues=error,
            confidence_adjustment=0.7,
        )
        self._upsert(record)

    def read_all(self) -> list[SourceHealthRecord]:
        records: list[SourceHealthRecord] = []
        if not self.csv_path.exists():
            return records
        with open(self.csv_path, encoding="utf-8") as f:
            reader = csv.reader(f)
            next(reader, None)
            for row in reader:
                if len(row) < 11:
                    continue
                records.append(SourceHealthRecord(
                    source_name=row[0], source_tier=row[1],
                    last_success_ts=row[2], last_failure_ts=row[3],
                    latency_ms=float(row[4]) if row[4] else None,
                    freshness_sec=float(row[5]) if row[5] else None,
                    schema_version=row[6], status=row[7],
                    known_issues=row[8], fallback=row[9],
                    confidence_adjustment=float(row[10]) if row[10] else 1.0,
                ))
        return records

    def get_confidence(self, source_name: str) -> float:
        for r in self.read_all():
            if r.source_name == source_name:
                return r.confidence_adjustment
        return 1.0

    def _upsert(self, record: SourceHealthRecord) -> None:
        records = self.read_all()
        found = False
        for i, r in enumerate(records):
            if r.source_name == record.source_name:
                # Merge: keep non-empty fields from existing
                if not record.last_success_ts and r.last_success_ts:
                    record.last_success_ts = r.last_success_ts
                if not record.last_failure_ts and r.last_failure_ts:
                    record.last_failure_ts = r.last_failure_ts
                records[i] = record
                found = True
                break
        if not found:
            records.append(record)
        self._rewrite(records)

    def _rewrite(self, records: list[SourceHealthRecord]) -> None:
        with open(self.csv_path, "w", encoding="utf-8", newline="") as f:
            f.write(SOURCE_HEALTH_HEADER + "\n")
            for r in records:
                f.write(r.to_csv_row() + "\n")


class SignalOutcomeScorer:
    """Computes per-signal hit rate, average R, and sample size."""

    def __init__(self, csv_path: str | Path = "ledgers/signal_outcomes.csv") -> None:
        self.csv_path = Path(csv_path)

    def update_stats(self, signal_results: dict[str, list[float]]) -> None:
        """Write updated signal statistics."""
        rows: list[str] = []
        for signal, rs in signal_results.items():
            n = len(rs)
            wins = [r for r in rs if r > 0]
            hit_rate = len(wins) / n if n > 0 else 0.0
            avg_r = sum(rs) / n if n > 0 else 0.0
            rows.append(f"{signal},{hit_rate:.4f},{avg_r:.4f},{n},{aest_now_iso()}")

        with open(self.csv_path, "w", encoding="utf-8", newline="") as f:
            f.write(SIGNAL_OUTCOMES_HEADER + "\n")
            for row in rows:
                f.write(row + "\n")

    def read_stats(self) -> dict[str, dict[str, Any]]:
        stats: dict[str, dict[str, Any]] = {}
        if not self.csv_path.exists():
            return stats
        with open(self.csv_path, encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                signal = row.get("signal", "")
                if signal:
                    try:
                        stats[signal] = {
                            "hit_rate": float(row.get("hit_rate", 0)),
                            "avg_R": float(row.get("avg_R", 0)),
                            "n": int(row.get("n", 0)),
                        }
                    except (ValueError, TypeError):
                        pass
        return stats
