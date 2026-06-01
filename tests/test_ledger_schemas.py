"""Tests for ledger file schemas per VAL-SCAFFOLD-002.

Validates that all 10 ledger files exist under ledgers/ and have the correct
header rows matching the spec in MISSION.md.
"""

import csv
import json
from pathlib import Path

import pytest

LEDGERS_DIR = Path(__file__).resolve().parent.parent / "ledgers"


def _read_header(path: Path) -> list[str]:
    """Read the first line of a CSV file and split into column names."""
    assert path.is_file(), f"Ledger file missing: {path.name}"
    with open(path, newline="") as f:
        reader = csv.reader(f)
        header = next(reader)
    # Strip whitespace from column names
    return [col.strip() for col in header]


# ---------------------------------------------------------------------------
# Schemas from MISSION.md (OutcomeGraph CSV section J)
# ---------------------------------------------------------------------------

OUTCOME_GRAPH_HEADER = [
    "date_Australia/Sydney",
    "symbol",
    "setup",
    "side",
    "entry",
    "stop",
    "tp1",
    "tp2",
    "filled",
    "entry_ts_Australia/Sydney",
    "exit_ts_Australia/Sydney",
    "result_R",
    "max_FvE",
    "max_AdE",
    "fees_bps",
    "slippage_bps",
    "notes",
    "provenance_tags",
]

# ---------------------------------------------------------------------------
# Schemas from MISSION.md (Knowledge Graph Mode section)
# ---------------------------------------------------------------------------

KG_TRIPLES_HEADER = [
    "subject",
    "predicate",
    "object",
    "attrs_json",
    "source_name",
    "source_tier",
    "source_link_or_[no-link]",
    "source_ts",
    "fetched_ts_Australia/Sydney",
    "confidence_0to1",
]

# ---------------------------------------------------------------------------
# Schemas from MISSION.md (Hypothesis Registry section)
# ---------------------------------------------------------------------------

HYPOTHESIS_REGISTRY_HEADER = [
    "hypothesis_id",
    "created_ts_Australia/Sydney",
    "status",
    "edge_claim",
    "mechanism",
    "symbol_scope",
    "required_data",
    "validation_method",
    "min_sample",
    "success_metric",
    "failure_metric",
    "current_n",
    "current_result",
    "next_action",
    "provenance_tags",
]

# ---------------------------------------------------------------------------
# Schemas from MISSION.md (Source Health section)
# ---------------------------------------------------------------------------

SOURCE_HEALTH_HEADER = [
    "source_name",
    "source_tier",
    "last_success_ts_Australia/Sydney",
    "last_failure_ts_Australia/Sydney",
    "latency_ms",
    "freshness_sec",
    "schema_version",
    "status",
    "known_issues",
    "fallback",
    "confidence_adjustment",
]

# ---------------------------------------------------------------------------
# Schemas from MISSION.md (Improvement Backlog section)
# ---------------------------------------------------------------------------

IMPROVEMENT_BACKLOG_HEADER = [
    "item_id",
    "created_ts_Australia/Sydney",
    "priority",
    "category",
    "problem",
    "proposed_change",
    "expected_impact",
    "risk",
    "validation_plan",
    "status",
    "owner_agent",
    "last_updated_ts_Australia/Sydney",
]

# ---------------------------------------------------------------------------
# Schemas from MISSION.md (Signal Outcomes section)
# ---------------------------------------------------------------------------

SIGNAL_OUTCOMES_HEADER = [
    "signal",
    "hit_rate",
    "avg_R",
    "n",
    "last_updated_Australia/Sydney",
]

# ---------------------------------------------------------------------------
# Inferred schemas for paper_fills.csv and skipped_trades.csv
# (These are listed in the MISSION.md directory layout but their explicit
# schemas are not defined in a separate section. We infer from context.)
# ---------------------------------------------------------------------------

PAPER_FILLS_HEADER = [
    "order_date_Australia/Sydney",
    "symbol",
    "fill_ts_Australia/Sydney",
    "fill_price",
    "fill_qty",
    "notional_usd",
    "fill_type",
    "fees_usd",
    "slippage_bps",
    "notes",
    "provenance_tags",
]

SKIPPED_TRADES_HEADER = [
    "date_Australia/Sydney",
    "symbol",
    "side",
    "proposed_entry",
    "stop",
    "skip_reason",
    "evidence",
    "provenance_tags",
]


# ===========================================================================
# Tests
# ===========================================================================


class TestLedgerFilesExist:
    """All 10 ledger files must exist under ledgers/."""

    EXPECTED_FILES = [
        "paper_orders.csv",
        "paper_fills.csv",
        "outcomes.csv",
        "evidence_ledger.jsonl",
        "kg_triples.csv",
        "signal_outcomes.csv",
        "skipped_trades.csv",
        "source_health.csv",
        "hypothesis_registry.csv",
        "improvement_backlog.csv",
    ]

    def test_all_ten_files_exist(self) -> None:
        """All 10 ledger files must exist."""
        for name in self.EXPECTED_FILES:
            path = LEDGERS_DIR / name
            assert path.is_file(), f"Missing ledger file: {name}"

    def test_exactly_ten_files_plus_gitkeep(self) -> None:
        """Ledgers directory should contain exactly the 10 ledger files + .gitkeep."""
        files = {p.name for p in LEDGERS_DIR.iterdir() if p.is_file()}
        expected = set(self.EXPECTED_FILES) | {".gitkeep"}
        assert files == expected, f"Unexpected files in ledgers/: {files - expected}"


class TestPaperOrdersSchema:
    """paper_orders.csv must match MISSION.md OutcomeGraph CSV schema."""

    def test_header_matches_spec(self) -> None:
        header = _read_header(LEDGERS_DIR / "paper_orders.csv")
        assert header == OUTCOME_GRAPH_HEADER, (
            f"paper_orders.csv header mismatch.\n"
            f"  Expected: {OUTCOME_GRAPH_HEADER}\n"
            f"  Got:      {header}"
        )

    def test_no_data_rows(self) -> None:
        """Scaffold should have no data rows."""
        path = LEDGERS_DIR / "paper_orders.csv"
        with open(path) as f:
            lines = f.readlines()
        # Should have header + optional empty trailing line
        data_lines = [l for l in lines if l.strip()]
        assert len(data_lines) == 1, "paper_orders.csv should have header only"


class TestOutcomesSchema:
    """outcomes.csv must match MISSION.md OutcomeGraph CSV schema."""

    def test_header_matches_spec(self) -> None:
        header = _read_header(LEDGERS_DIR / "outcomes.csv")
        assert header == OUTCOME_GRAPH_HEADER, (
            f"outcomes.csv header mismatch.\n"
            f"  Expected: {OUTCOME_GRAPH_HEADER}\n"
            f"  Got:      {header}"
        )

    def test_no_data_rows(self) -> None:
        path = LEDGERS_DIR / "outcomes.csv"
        with open(path) as f:
            lines = f.readlines()
        data_lines = [l for l in lines if l.strip()]
        assert len(data_lines) == 1, "outcomes.csv should have header only"


class TestPaperFillsSchema:
    """paper_fills.csv must have the correct header."""

    def test_header_matches_spec(self) -> None:
        header = _read_header(LEDGERS_DIR / "paper_fills.csv")
        assert header == PAPER_FILLS_HEADER, (
            f"paper_fills.csv header mismatch.\n"
            f"  Expected: {PAPER_FILLS_HEADER}\n"
            f"  Got:      {header}"
        )

    def test_no_data_rows(self) -> None:
        path = LEDGERS_DIR / "paper_fills.csv"
        with open(path) as f:
            lines = f.readlines()
        data_lines = [l for l in lines if l.strip()]
        assert len(data_lines) == 1, "paper_fills.csv should have header only"


class TestKgTriplesSchema:
    """kg_triples.csv must match MISSION.md Knowledge Graph triple format."""

    def test_header_matches_spec(self) -> None:
        header = _read_header(LEDGERS_DIR / "kg_triples.csv")
        assert header == KG_TRIPLES_HEADER, (
            f"kg_triples.csv header mismatch.\n"
            f"  Expected: {KG_TRIPLES_HEADER}\n"
            f"  Got:      {header}"
        )

    def test_no_data_rows(self) -> None:
        path = LEDGERS_DIR / "kg_triples.csv"
        with open(path) as f:
            lines = f.readlines()
        data_lines = [l for l in lines if l.strip()]
        assert len(data_lines) == 1, "kg_triples.csv should have header only"


class TestSignalOutcomesSchema:
    """signal_outcomes.csv must match MISSION.md signal outcomes table."""

    def test_header_matches_spec(self) -> None:
        header = _read_header(LEDGERS_DIR / "signal_outcomes.csv")
        assert header == SIGNAL_OUTCOMES_HEADER, (
            f"signal_outcomes.csv header mismatch.\n"
            f"  Expected: {SIGNAL_OUTCOMES_HEADER}\n"
            f"  Got:      {header}"
        )

    def test_no_data_rows(self) -> None:
        path = LEDGERS_DIR / "signal_outcomes.csv"
        with open(path) as f:
            lines = f.readlines()
        data_lines = [l for l in lines if l.strip()]
        assert len(data_lines) == 1, "signal_outcomes.csv should have header only"


class TestSkippedTradesSchema:
    """skipped_trades.csv must have correct header with skip_reason."""

    def test_header_matches_spec(self) -> None:
        header = _read_header(LEDGERS_DIR / "skipped_trades.csv")
        assert header == SKIPPED_TRADES_HEADER, (
            f"skipped_trades.csv header mismatch.\n"
            f"  Expected: {SKIPPED_TRADES_HEADER}\n"
            f"  Got:      {header}"
        )

    def test_no_data_rows(self) -> None:
        path = LEDGERS_DIR / "skipped_trades.csv"
        with open(path) as f:
            lines = f.readlines()
        data_lines = [l for l in lines if l.strip()]
        assert len(data_lines) == 1, "skipped_trades.csv should have header only"


class TestSourceHealthSchema:
    """source_health.csv must match MISSION.md source health schema."""

    def test_header_matches_spec(self) -> None:
        header = _read_header(LEDGERS_DIR / "source_health.csv")
        assert header == SOURCE_HEALTH_HEADER, (
            f"source_health.csv header mismatch.\n"
            f"  Expected: {SOURCE_HEALTH_HEADER}\n"
            f"  Got:      {header}"
        )

    def test_no_data_rows(self) -> None:
        path = LEDGERS_DIR / "source_health.csv"
        with open(path) as f:
            lines = f.readlines()
        data_lines = [l for l in lines if l.strip()]
        assert len(data_lines) == 1, "source_health.csv should have header only"


class TestHypothesisRegistrySchema:
    """hypothesis_registry.csv must match MISSION.md hypothesis registry schema."""

    def test_header_matches_spec(self) -> None:
        header = _read_header(LEDGERS_DIR / "hypothesis_registry.csv")
        assert header == HYPOTHESIS_REGISTRY_HEADER, (
            f"hypothesis_registry.csv header mismatch.\n"
            f"  Expected: {HYPOTHESIS_REGISTRY_HEADER}\n"
            f"  Got:      {header}"
        )

    def test_no_data_rows(self) -> None:
        path = LEDGERS_DIR / "hypothesis_registry.csv"
        with open(path) as f:
            lines = f.readlines()
        data_lines = [l for l in lines if l.strip()]
        assert len(data_lines) == 1, "hypothesis_registry.csv should have header only"


class TestImprovementBacklogSchema:
    """improvement_backlog.csv must match MISSION.md improvement backlog schema."""

    def test_header_matches_spec(self) -> None:
        header = _read_header(LEDGERS_DIR / "improvement_backlog.csv")
        assert header == IMPROVEMENT_BACKLOG_HEADER, (
            f"improvement_backlog.csv header mismatch.\n"
            f"  Expected: {IMPROVEMENT_BACKLOG_HEADER}\n"
            f"  Got:      {header}"
        )

    def test_no_data_rows(self) -> None:
        path = LEDGERS_DIR / "improvement_backlog.csv"
        with open(path) as f:
            lines = f.readlines()
        data_lines = [l for l in lines if l.strip()]
        assert len(data_lines) == 1, "improvement_backlog.csv should have header only"


class TestEvidenceLedgerSchema:
    """evidence_ledger.jsonl must exist and be valid JSONL (empty is valid)."""

    def test_file_exists(self) -> None:
        path = LEDGERS_DIR / "evidence_ledger.jsonl"
        assert path.is_file(), "evidence_ledger.jsonl not found"

    def test_file_is_valid_jsonl(self) -> None:
        """If file has any lines, each must be valid JSON with provenance fields."""
        path = LEDGERS_DIR / "evidence_ledger.jsonl"
        with open(path) as f:
            lines = [l.strip() for l in f if l.strip()]

        # Empty file is valid for scaffold
        if not lines:
            return

        # If non-empty, each line must be valid JSON with provenance fields
        required_fields = {
            "run_id",
            "ts_aest",
            "source_name",
            "source_tier",
            "source_ts",
            "fetched_ts_aest",
            "confidence",
        }
        for i, line in enumerate(lines):
            obj = json.loads(line)
            missing = required_fields - set(obj.keys())
            assert not missing, (
                f"evidence_ledger.jsonl line {i+1} missing fields: {missing}"
            )
