"""Tests for memory file initialization per VAL-SCAFFOLD-003.

Validates that memory/mission_state.json has the correct structure
and all memory markdown files exist with correct section headers.
"""

import json
from pathlib import Path

import pytest

MEMORY_DIR = Path(__file__).resolve().parent.parent / "memory"


# ---------------------------------------------------------------------------
# mission_state.json
# ---------------------------------------------------------------------------


class TestMissionState:
    """Validate memory/mission_state.json structure."""

    def _load(self) -> dict:
        path = MEMORY_DIR / "mission_state.json"
        assert path.is_file(), "memory/mission_state.json does not exist"
        with open(path) as f:
            data = json.load(f)
        assert isinstance(data, dict), "mission_state.json must be a JSON object"
        return data

    def test_mode_is_live_paper_only(self) -> None:
        data = self._load()
        assert "mode" in data, "mission_state.json missing 'mode' key"
        assert data["mode"] == "live-paper-only"

    def test_last_run_id_is_null(self) -> None:
        data = self._load()
        assert "last_run_id" in data, "mission_state.json missing 'last_run_id' key"
        assert data["last_run_id"] is None

    def test_open_paper_orders_is_empty_array(self) -> None:
        data = self._load()
        assert "open_paper_orders" in data
        assert isinstance(data["open_paper_orders"], list)
        assert len(data["open_paper_orders"]) == 0

    def test_unresolved_outcomes_is_empty_array(self) -> None:
        data = self._load()
        assert "unresolved_outcomes" in data
        assert isinstance(data["unresolved_outcomes"], list)
        assert len(data["unresolved_outcomes"]) == 0

    def test_active_experiments_is_empty_array(self) -> None:
        data = self._load()
        assert "active_experiments" in data
        assert isinstance(data["active_experiments"], list)
        assert len(data["active_experiments"]) == 0

    def test_disabled_sources_is_empty_array(self) -> None:
        data = self._load()
        assert "disabled_sources" in data
        assert isinstance(data["disabled_sources"], list)
        assert len(data["disabled_sources"]) == 0

    def test_promotion_status_present(self) -> None:
        data = self._load()
        assert "promotion_status" in data
        assert isinstance(data["promotion_status"], dict)


# ---------------------------------------------------------------------------
# durable_lessons.md
# ---------------------------------------------------------------------------


class TestDurableLessons:
    """Validate memory/durable_lessons.md has correct headers."""

    def _read(self) -> str:
        path = MEMORY_DIR / "durable_lessons.md"
        assert path.is_file(), "memory/durable_lessons.md does not exist"
        return path.read_text()

    def test_has_title_header(self) -> None:
        content = self._read()
        assert "# Durable Lessons" in content

    def test_has_active_lessons_section(self) -> None:
        content = self._read()
        assert "## Active Lessons" in content

    def test_has_retired_lessons_section(self) -> None:
        content = self._read()
        assert "## Retired Lessons" in content

    def test_has_format_note(self) -> None:
        content = self._read()
        assert "Format" in content or "format" in content


# ---------------------------------------------------------------------------
# adapter_registry.md
# ---------------------------------------------------------------------------


class TestAdapterRegistry:
    """Validate memory/adapter_registry.md has correct headers."""

    def _read(self) -> str:
        path = MEMORY_DIR / "adapter_registry.md"
        assert path.is_file(), "memory/adapter_registry.md does not exist"
        return path.read_text()

    def test_has_title_header(self) -> None:
        content = self._read()
        assert "# Adapter Registry" in content

    def test_has_registered_adapters_section(self) -> None:
        content = self._read()
        assert "## Registered Adapters" in content

    def test_has_source_quality_notes_section(self) -> None:
        content = self._read()
        assert "## Source Quality Notes" in content

    def test_has_fallback_order_section(self) -> None:
        content = self._read()
        assert "## Fallback Order" in content


# ---------------------------------------------------------------------------
# failure_modes.md
# ---------------------------------------------------------------------------


class TestFailureModes:
    """Validate memory/failure_modes.md has correct headers."""

    def _read(self) -> str:
        path = MEMORY_DIR / "failure_modes.md"
        assert path.is_file(), "memory/failure_modes.md does not exist"
        return path.read_text()

    def test_has_title_header(self) -> None:
        content = self._read()
        assert "# Failure Modes" in content

    def test_has_known_failures_section(self) -> None:
        content = self._read()
        assert "## Known Failure Modes" in content

    def test_has_mitigation_strategies_section(self) -> None:
        content = self._read()
        assert "## Mitigation Strategies" in content


# ---------------------------------------------------------------------------
# promotion_decisions.md
# ---------------------------------------------------------------------------


class TestPromotionDecisions:
    """Validate memory/promotion_decisions.md has correct headers."""

    def _read(self) -> str:
        path = MEMORY_DIR / "promotion_decisions.md"
        assert path.is_file(), "memory/promotion_decisions.md does not exist"
        return path.read_text()

    def test_has_title_header(self) -> None:
        content = self._read()
        assert "# Promotion Decisions" in content

    def test_has_decision_log_section(self) -> None:
        content = self._read()
        assert "## Decision Log" in content

    def test_has_active_gates_section(self) -> None:
        content = self._read()
        assert "## Active Promotion Gates" in content


# ---------------------------------------------------------------------------
# Cross-file validation (VAL-SCAFFOLD-003)
# ---------------------------------------------------------------------------


class TestAllMemoryFiles:
    """Validate all memory files exist and have correct structure."""

    def test_all_memory_files_exist(self) -> None:
        """All required memory files must exist."""
        expected = [
            "mission_state.json",
            "durable_lessons.md",
            "adapter_registry.md",
            "failure_modes.md",
            "promotion_decisions.md",
        ]
        for name in expected:
            path = MEMORY_DIR / name
            assert path.is_file(), f"Missing memory file: {name}"

    def test_mission_state_valid_json(self) -> None:
        """mission_state.json must be valid JSON."""
        path = MEMORY_DIR / "mission_state.json"
        with open(path) as f:
            json.load(f)  # raises on invalid JSON

    def test_val_scaffold_003_assertion(self) -> None:
        """Exact VAL-SCAFFOLD-003 evidence assertion.

        mission_state.json has mode: live-paper-only, last_run_id: null,
        empty arrays for open_paper_orders and unresolved_outcomes.
        """
        path = MEMORY_DIR / "mission_state.json"
        with open(path) as f:
            s = json.load(f)
        assert s["mode"] == "live-paper-only"
        assert s["last_run_id"] is None
        assert s["open_paper_orders"] == []
        assert s["unresolved_outcomes"] == []
