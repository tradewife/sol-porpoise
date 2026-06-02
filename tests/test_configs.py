"""Tests for config file validity per VAL-SCAFFOLD-001.

Validates that all four config files exist, parse as valid YAML,
and contain the expected top-level keys and values.
"""

from pathlib import Path

import yaml

import pytest

CONFIG_DIR = Path(__file__).resolve().parent.parent / "config"


def _load(name: str) -> dict:
    """Load and return a YAML config file as a dict."""
    path = CONFIG_DIR / name
    assert path.is_file(), f"Config file missing: {name}"
    with open(path) as f:
        data = yaml.safe_load(f)
    assert isinstance(data, dict), f"{name} did not parse as a dict"
    return data


# ---------------------------------------------------------------------------
# run.yaml
# ---------------------------------------------------------------------------


class TestRunYaml:
    """Validate config/run.yaml."""

    def test_file_exists_and_parses(self) -> None:
        data = _load("run.yaml")
        assert "mode" in data

    def test_mode_is_live_paper_only(self) -> None:
        data = _load("run.yaml")
        assert data["mode"] == "live-paper-only"

    def test_schedule_present(self) -> None:
        data = _load("run.yaml")
        assert "schedule" in data
        schedule = data["schedule"]
        assert isinstance(schedule, dict)
        # Should have timezone and at least one cron entry
        assert "timezone" in schedule

    def test_account_params_present(self) -> None:
        data = _load("run.yaml")
        assert "account" in data
        account = data["account"]
        assert isinstance(account, dict)
        assert "equity" in account
        assert "currency" in account


# ---------------------------------------------------------------------------
# venues.yaml
# ---------------------------------------------------------------------------


class TestVenuesYaml:
    """Validate config/venues.yaml."""

    def test_file_exists_and_parses(self) -> None:
        data = _load("venues.yaml")
        assert "imperial_api" in data

    def test_imperial_api_has_base_url(self) -> None:
        data = _load("venues.yaml")
        api = data["imperial_api"]
        assert "base_url" in api
        assert isinstance(api["base_url"], str)
        assert "imperial" in api["base_url"]

    def test_venue_codes_present(self) -> None:
        data = _load("venues.yaml")
        assert "venues" in data
        venues = data["venues"]
        assert isinstance(venues, dict)
        # Must include the 4 Solana perp venues
        expected_venues = {"jupiter", "flash_trade", "phoenix", "gmtrade"}
        assert expected_venues.issubset(set(venues.keys())), (
            f"Missing venues: {expected_venues - set(venues.keys())}"
        )


# ---------------------------------------------------------------------------
# risk.yaml
# ---------------------------------------------------------------------------


class TestRiskYaml:
    """Validate config/risk.yaml."""

    def test_file_exists_and_parses(self) -> None:
        data = _load("risk.yaml")
        assert "equity" in data

    def test_equity_value(self) -> None:
        data = _load("risk.yaml")
        assert data["equity"] == 1000

    def test_max_risk_pct(self) -> None:
        data = _load("risk.yaml")
        assert "max_risk_pct" in data
        assert data["max_risk_pct"] == 0.20

    def test_leverage_range(self) -> None:
        data = _load("risk.yaml")
        assert "leverage" in data
        lev = data["leverage"]
        assert isinstance(lev, dict)
        assert lev["min"] == 9
        assert lev["max"] == 12

    def test_cancel_rules_present(self) -> None:
        data = _load("risk.yaml")
        assert "cancel_rules" in data
        rules = data["cancel_rules"]
        assert isinstance(rules, dict)
        # Must include timeout
        assert "timeout_minutes" in rules
        # hard_exit_time key still present but empty/disabled for hourly trial
        assert "hard_exit_time" in rules


# ---------------------------------------------------------------------------
# sources.yaml
# ---------------------------------------------------------------------------


class TestSourcesYaml:
    """Validate config/sources.yaml."""

    def test_file_exists_and_parses(self) -> None:
        data = _load("sources.yaml")
        assert "tiers" in data

    def test_source_tiers_defined(self) -> None:
        data = _load("sources.yaml")
        tiers = data["tiers"]
        assert isinstance(tiers, list)
        assert len(tiers) > 0
        # Each tier should have a name and description
        for tier in tiers:
            assert "name" in tier

    def test_fallback_order_defined(self) -> None:
        data = _load("sources.yaml")
        assert "fallback_order" in data
        fallback = data["fallback_order"]
        assert isinstance(fallback, list)
        assert len(fallback) > 0

    def test_expected_tier_names_present(self) -> None:
        data = _load("sources.yaml")
        tiers = data["tiers"]
        tier_names = {t["name"] for t in tiers}
        # Verify the canonical tier names from MISSION.md are present
        expected = {"Open", "HL-native", "Solana-native", "On-chain", "Derived"}
        assert expected.issubset(tier_names), (
            f"Missing tier names: {expected - tier_names}"
        )


# ---------------------------------------------------------------------------
# Cross-file validation (VAL-SCAFFOLD-001)
# ---------------------------------------------------------------------------


class TestAllConfigs:
    """Validate all four config files together."""

    def test_all_four_configs_parse(self) -> None:
        """All four config files must parse as valid YAML."""
        for name in ["run.yaml", "venues.yaml", "risk.yaml", "sources.yaml"]:
            _load(name)  # will raise on failure

    def test_run_mode_matches_mission_state(self) -> None:
        """run.yaml mode must be live-paper-only."""
        data = _load("run.yaml")
        assert data["mode"] == "live-paper-only"
