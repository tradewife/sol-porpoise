"""Hypothesis registry: CRUD for candidate edges being tested."""

from __future__ import annotations

import csv
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from adapters.normalizer import aest_now_iso
from zoneinfo import ZoneInfo

AEST = ZoneInfo("Australia/Sydney")


HYPOTHESIS_HEADER = (
    "hypothesis_id,created_ts_Australia/Sydney,status,edge_claim,mechanism,"
    "symbol_scope,required_data,validation_method,min_sample,success_metric,"
    "failure_metric,current_n,current_result,next_action,provenance_tags"
)


class HypothesisStatus(str):
    ACTIVE = "active"
    REJECTED = "rejected"
    SUPERSEDED = "superseded"


@dataclass
class Hypothesis:
    hypothesis_id: str
    created_ts: str
    status: str = HypothesisStatus.ACTIVE
    edge_claim: str = ""
    mechanism: str = ""
    symbol_scope: str = ""
    required_data: str = ""
    validation_method: str = ""
    min_sample: int = 10
    success_metric: str = ""
    failure_metric: str = ""
    current_n: int = 0
    current_result: str = ""
    next_action: str = ""
    provenance_tags: str = ""

    def to_csv_row(self) -> str:
        return (
            f"{self.hypothesis_id},{self.created_ts},{self.status},"
            f"{self.edge_claim},{self.mechanism},{self.symbol_scope},"
            f"{self.required_data},{self.validation_method},{self.min_sample},"
            f"{self.success_metric},{self.failure_metric},{self.current_n},"
            f"{self.current_result},{self.next_action},{self.provenance_tags}"
        )


class HypothesisRegistry:
    def __init__(self, csv_path: str | Path = "ledgers/hypothesis_registry.csv") -> None:
        self.csv_path = Path(csv_path)

    def create(self, hypothesis: Hypothesis) -> None:
        header_needed = not self.csv_path.exists() or self.csv_path.stat().st_size == 0
        with open(self.csv_path, "a", encoding="utf-8", newline="") as f:
            if header_needed:
                f.write(HYPOTHESIS_HEADER + "\n")
            f.write(hypothesis.to_csv_row() + "\n")

    def read_all(self) -> list[Hypothesis]:
        hypotheses: list[Hypothesis] = []
        if not self.csv_path.exists():
            return hypotheses
        with open(self.csv_path, encoding="utf-8") as f:
            reader = csv.reader(f)
            next(reader, None)
            for row in reader:
                if len(row) < 15:
                    continue
                hypotheses.append(Hypothesis(
                    hypothesis_id=row[0], created_ts=row[1], status=row[2],
                    edge_claim=row[3], mechanism=row[4], symbol_scope=row[5],
                    required_data=row[6], validation_method=row[7],
                    min_sample=int(row[8]) if row[8] else 10,
                    success_metric=row[9], failure_metric=row[10],
                    current_n=int(row[11]) if row[11] else 0,
                    current_result=row[12], next_action=row[13],
                    provenance_tags=row[14],
                ))
        return hypotheses

    def update_status(self, hypothesis_id: str, new_status: str, next_action: str = "") -> bool:
        hypotheses = self.read_all()
        found = False
        for h in hypotheses:
            if h.hypothesis_id == hypothesis_id:
                h.status = new_status
                h.next_action = next_action
                found = True
                break
        if not found:
            return False
        self._rewrite(hypotheses)
        return True

    def update_result(self, hypothesis_id: str, current_n: int, current_result: str) -> bool:
        hypotheses = self.read_all()
        found = False
        for h in hypotheses:
            if h.hypothesis_id == hypothesis_id:
                h.current_n = current_n
                h.current_result = current_result
                found = True
                break
        if not found:
            return False
        self._rewrite(hypotheses)
        return True

    def query_active(self) -> list[Hypothesis]:
        return [h for h in self.read_all() if h.status == HypothesisStatus.ACTIVE]

    def _rewrite(self, hypotheses: list[Hypothesis]) -> None:
        with open(self.csv_path, "w", encoding="utf-8", newline="") as f:
            f.write(HYPOTHESIS_HEADER + "\n")
            for h in hypotheses:
                f.write(h.to_csv_row() + "\n")
