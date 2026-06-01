"""Knowledge graph triple writer and query engine."""

from __future__ import annotations

import csv
import json
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any

from adapters.normalizer import aest_now_iso


class EntityType(str, Enum):
    SYMBOL = "SYMBOL"
    VENUE = "VENUE"
    WALLET = "WALLET"
    TRADER = "TRADER"
    FLOW = "FLOW"
    SIGNAL = "SIGNAL"
    CATALYST = "CATALYST"
    ORDER = "ORDER"
    OUTCOME = "OUTCOME"


class RelationType(str, Enum):
    OWNS_OR_CONTROLS = "owns_or_controls"
    TRADED = "traded"
    BIAS = "bias"
    BASIS_VS = "basis_vs"
    LEAD_LAG = "lead_lag"
    HAS_SIGNAL = "has_signal"
    HAS_CATALYST = "has_catalyst"
    HAS_ORDER = "has_order"
    RESULTED_IN = "resulted_in"
    SCORED_FOR = "scored_for"


KG_CSV_HEADER = (
    "subject,predicate,object,attrs_json,"
    "source_name,source_tier,source_link_or_[no-link],"
    "source_ts,fetched_ts_Australia/Sydney,confidence_0to1"
)


@dataclass
class KGTriple:
    subject: str
    predicate: str
    object: str
    attrs: dict[str, Any] = field(default_factory=dict)
    source_name: str = "Internal"
    source_tier: str = "Internal"
    source_link: str = "[no-link]"
    source_ts: str = ""
    fetched_ts_aest: str = ""
    confidence: float = 0.0

    def to_csv_row(self) -> str:
        attrs_json = json.dumps(self.attrs, separators=(",", ":"))
        ts = self.source_ts or aest_now_iso()
        fts = self.fetched_ts_aest or aest_now_iso()
        return (
            f"{self.subject},{self.predicate},{self.object},{attrs_json},"
            f"{self.source_name},{self.source_tier},{self.source_link},"
            f"{ts},{fts},{self.confidence}"
        )


class KGWriter:
    """Writes KG triples to kg_triples.csv."""

    def __init__(self, csv_path: str | Path = "ledgers/kg_triples.csv") -> None:
        self.csv_path = Path(csv_path)
        self._triples: list[KGTriple] = []

    def add_triple(self, triple: KGTriple) -> None:
        self._triples.append(triple)

    def add(
        self,
        subject: str,
        predicate: str,
        object_: str,
        attrs: dict[str, Any] | None = None,
        source_name: str = "Internal",
        source_tier: str = "Internal",
        source_link: str = "[no-link]",
        source_ts: str = "",
        confidence: float = 0.0,
    ) -> KGTriple:
        t = KGTriple(
            subject=subject,
            predicate=predicate,
            object=object_,
            attrs=attrs or {},
            source_name=source_name,
            source_tier=source_tier,
            source_link=source_link,
            source_ts=source_ts,
            confidence=confidence,
        )
        self.add_triple(t)
        return t

    def flush(self) -> int:
        """Append buffered triples to CSV. Returns number written."""
        if not self._triples:
            return 0
        header_needed = not self.csv_path.exists() or self.csv_path.stat().st_size == 0
        with open(self.csv_path, "a", encoding="utf-8", newline="") as f:
            if header_needed:
                f.write(KG_CSV_HEADER + "\n")
            for triple in self._triples:
                f.write(triple.to_csv_row() + "\n")
        count = len(self._triples)
        self._triples.clear()
        return count

    def query(
        self, subject: str | None = None, predicate: str | None = None, object_: str | None = None
    ) -> list[KGTriple]:
        """Read triples from CSV matching filters."""
        results: list[KGTriple] = []
        if not self.csv_path.exists():
            return results
        with open(self.csv_path, encoding="utf-8", newline="") as f:
            reader = csv.reader(f)
            header = next(reader, None)
            if not header:
                return results
            for row in reader:
                if len(row) < 10:
                    continue
                t = KGTriple(
                    subject=row[0], predicate=row[1], object=row[2],
                    attrs=json.loads(row[3]) if row[3] else {},
                    source_name=row[4], source_tier=row[5], source_link=row[6],
                    source_ts=row[7], fetched_ts_aest=row[8], confidence=float(row[9]),
                )
                if subject and t.subject != subject:
                    continue
                if predicate and t.predicate != predicate:
                    continue
                if object_ and t.object != object_:
                    continue
                results.append(t)
        return results
