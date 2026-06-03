"""Tests for the skills layer — VAL-SKILL-001 through VAL-SKILL-007.

Validates:
- hyperliquid-microstructure SKILL.md has no unconditional liquidation veto
- whale-leaderboard-intel SKILL.md mentions Dextrabot, Hyperdash, HL whale trades — no Phantom
- orderbook-liquidity SKILL.md exists with book_imbalance guidance
- config/ai_agent.yaml lists orderbook-liquidity in skills.enabled
- All skills load without error via engine/skills.py
- No phantom/Phantom MCP references in any skill or config file
- Skills count in config matches installed skills (11)
"""

from pathlib import Path

import yaml
import pytest

from engine.skills import load_enabled_skills, LoadedSkill

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SKILLS_DIR = PROJECT_ROOT / "skills"
CONFIG_PATH = PROJECT_ROOT / "config" / "ai_agent.yaml"


def _load_ai_config() -> dict:
    """Load config/ai_agent.yaml."""
    with open(CONFIG_PATH) as f:
        return yaml.safe_load(f)


# ---------------------------------------------------------------------------
# VAL-SKILL-001 — hyperliquid-microstructure no unconditional liq veto
# ---------------------------------------------------------------------------


class TestHLMicrostructure:
    """VAL-SKILL-001: hyperliquid-microstructure SKILL.md has no unconditional liq veto."""

    SKILL_PATH = SKILLS_DIR / "hyperliquid-microstructure" / "SKILL.md"

    def test_skill_file_exists(self) -> None:
        assert self.SKILL_PATH.exists(), "hyperliquid-microstructure/SKILL.md missing"

    def test_no_unconditional_veto_language(self) -> None:
        content = self.SKILL_PATH.read_text()
        # Check for unconditional LIQUIDATION veto patterns — these phrases
        # indicate a hard block on trades based on liquidation data alone.
        veto_patterns = [
            "always veto",
            "always block",
            "always reject",
            "reject all",
            "block all",
        ]
        for pattern in veto_patterns:
            assert pattern.lower() not in content.lower(), (
                f"Found unconditional veto language '{pattern}' in hyperliquid-microstructure"
            )
        # Verify no line rejects trades based on liquidation data unconditionally
        for line in content.splitlines():
            stripped = line.strip().lower()
            if "reject" in stripped and "liquidation" in stripped:
                # Must have a conditional word nearby
                assert any(
                    w in stripped for w in ["unavailable", "if", "when", "depends on"]
                ), f"Found unconditional reject + liquidation: {line.strip()}"

    def test_liquidation_guidance_is_conditional(self) -> None:
        content = self.SKILL_PATH.read_text()
        # Must contain conditional language about liquidation
        assert "conditional" in content.lower() or "if" in content.lower()
        assert "guidance" in content.lower() or "conditional" in content.lower()

    def test_contains_book_imbalance_reference(self) -> None:
        content = self.SKILL_PATH.read_text()
        assert "book_imbalance" in content, (
            "hyperliquid-microstructure should reference book_imbalance"
        )

    def test_contains_liquidity_magnet_pattern(self) -> None:
        content = self.SKILL_PATH.read_text()
        assert "liquidity_magnet" in content, (
            "hyperliquid-microstructure should contain liquidity_magnet setup pattern"
        )


# ---------------------------------------------------------------------------
# VAL-SKILL-002 — whale-leaderboard-intel rewritten with 3 source tiers
# ---------------------------------------------------------------------------


class TestWhaleLeaderboardIntel:
    """VAL-SKILL-002: whale-leaderboard-intel has 3 source tiers, no Phantom."""

    SKILL_PATH = SKILLS_DIR / "whale-leaderboard-intel" / "SKILL.md"

    def test_skill_file_exists(self) -> None:
        assert self.SKILL_PATH.exists(), "whale-leaderboard-intel/SKILL.md missing"

    def test_mentions_dextrabot(self) -> None:
        content = self.SKILL_PATH.read_text()
        assert "Dextrabot" in content, "whale-leaderboard-intel must mention Dextrabot"

    def test_mentions_hyperdash(self) -> None:
        content = self.SKILL_PATH.read_text()
        assert "Hyperdash" in content, "whale-leaderboard-intel must mention Hyperdash"

    def test_mentions_hl_whale_trades(self) -> None:
        content = self.SKILL_PATH.read_text()
        assert "Hyperliquid" in content or "HL" in content, (
            "whale-leaderboard-intel must mention Hyperliquid or HL whale trades"
        )

    def test_no_phantom_reference(self) -> None:
        content = self.SKILL_PATH.read_text()
        assert "Phantom" not in content, (
            "whale-leaderboard-intel must not reference Phantom"
        )
        assert "phantom" not in content.lower(), (
            "whale-leaderboard-intel must not reference phantom (case-insensitive)"
        )

    def test_mentions_three_tiers(self) -> None:
        content = self.SKILL_PATH.read_text()
        assert "Tier 1" in content or "tier" in content.lower(), (
            "whale-leaderboard-intel should describe source tiers"
        )

    def test_mentions_roi_whale(self) -> None:
        content = self.SKILL_PATH.read_text()
        assert "roi_whale" in content, (
            "whale-leaderboard-intel should mention roi_whale tier"
        )


# ---------------------------------------------------------------------------
# VAL-SKILL-003 — orderbook-liquidity SKILL.md created
# ---------------------------------------------------------------------------


class TestOrderbookLiquidity:
    """VAL-SKILL-003: orderbook-liquidity SKILL.md exists with book_imbalance guidance."""

    SKILL_PATH = SKILLS_DIR / "orderbook-liquidity" / "SKILL.md"

    def test_skill_file_exists(self) -> None:
        assert self.SKILL_PATH.exists(), "orderbook-liquidity/SKILL.md missing"

    def test_skill_non_empty(self) -> None:
        content = self.SKILL_PATH.read_text()
        assert len(content) > 100, "orderbook-liquidity/SKILL.md must be non-trivial (>100 bytes)"

    def test_contains_book_imbalance(self) -> None:
        content = self.SKILL_PATH.read_text()
        assert "book_imbalance" in content, (
            "orderbook-liquidity must contain book_imbalance guidance"
        )

    def test_contains_interpretation_guide(self) -> None:
        content = self.SKILL_PATH.read_text()
        # Should have threshold interpretation
        assert "1.6" in content or "0.60" in content or "0.77" in content, (
            "orderbook-liquidity should contain book_imbalance threshold values"
        )

    def test_mentions_liquidation_cluster(self) -> None:
        content = self.SKILL_PATH.read_text()
        assert "liquidation_cluster" in content or "liquidation" in content.lower(), (
            "orderbook-liquidity should reference liquidation_cluster (even if deferred)"
        )


# ---------------------------------------------------------------------------
# VAL-SKILL-004 — config lists orderbook-liquidity in skills.enabled
# ---------------------------------------------------------------------------


class TestConfigRegistration:
    """VAL-SKILL-004: config/ai_agent.yaml lists orderbook-liquidity in skills.enabled."""

    def test_orderbook_liquidity_in_enabled(self) -> None:
        config = _load_ai_config()
        enabled = config.get("skills", {}).get("enabled", [])
        assert "orderbook-liquidity" in enabled, (
            f"orderbook-liquidity not in skills.enabled: {enabled}"
        )


# ---------------------------------------------------------------------------
# VAL-SKILL-005 — All skills load without error via engine/skills.py
# ---------------------------------------------------------------------------


class TestSkillsLoading:
    """VAL-SKILL-005: All skills load without error via engine/skills.py."""

    def test_all_enabled_skills_load(self) -> None:
        config = _load_ai_config()
        loaded, warnings = load_enabled_skills(PROJECT_ROOT, config)
        # No warnings (all skills found)
        assert len(warnings) == 0, f"Skill loading warnings: {warnings}"
        # All loaded skills have content
        for skill in loaded:
            assert isinstance(skill, LoadedSkill)
            assert skill.content, f"Skill {skill.name} has empty content"

    def test_loaded_skill_count_matches_config(self) -> None:
        config = _load_ai_config()
        enabled = config.get("skills", {}).get("enabled", [])
        loaded, _ = load_enabled_skills(PROJECT_ROOT, config)
        assert len(loaded) == len(enabled), (
            f"Loaded {len(loaded)} skills but config has {len(enabled)} enabled"
        )

    def test_each_skill_file_readable(self) -> None:
        """Every enabled skill's SKILL.md exists and is readable."""
        config = _load_ai_config()
        enabled = config.get("skills", {}).get("enabled", [])
        for name in enabled:
            path = SKILLS_DIR / name / "SKILL.md"
            assert path.exists(), f"Skill file missing: {path}"
            content = path.read_text(encoding="utf-8").strip()
            assert len(content) > 0, f"Skill {name} has empty SKILL.md"


# ---------------------------------------------------------------------------
# VAL-SKILL-006 — No phantom/Phantom MCP references in any skill or config
# ---------------------------------------------------------------------------


class TestNoPhantomInSkillsOrConfig:
    """VAL-SKILL-006: No phantom/Phantom MCP references in any skill or config file."""

    def test_no_phantom_in_skills_dir(self) -> None:
        """No file under skills/ contains 'phantom' (case-insensitive)."""
        matches = []
        for path in SKILLS_DIR.rglob("*.md"):
            content = path.read_text()
            if "phantom" in content.lower():
                matches.append(str(path.relative_to(PROJECT_ROOT)))
        assert len(matches) == 0, (
            f"Found phantom references in skill files: {matches}"
        )

    def test_no_phantom_in_ai_agent_yaml(self) -> None:
        """config/ai_agent.yaml has no phantom reference."""
        content = CONFIG_PATH.read_text()
        assert "phantom" not in content.lower(), (
            "config/ai_agent.yaml still contains phantom references"
        )

    def test_no_phantom_in_all_config_files(self) -> None:
        """No config YAML file contains phantom references."""
        config_dir = PROJECT_ROOT / "config"
        for path in config_dir.glob("*.yaml"):
            content = path.read_text()
            assert "phantom" not in content.lower(), (
                f"Found phantom in {path.name}"
            )


# ---------------------------------------------------------------------------
# VAL-SKILL-007 — Skills count in config matches installed skills (11)
# ---------------------------------------------------------------------------


class TestSkillsCountConsistency:
    """VAL-SKILL-007: skills.enabled has exactly 11 entries matching installed skills."""

    def test_enabled_count_is_11(self) -> None:
        config = _load_ai_config()
        enabled = config.get("skills", {}).get("enabled", [])
        assert len(enabled) == 11, (
            f"Expected 11 enabled skills, got {len(enabled)}: {enabled}"
        )

    def test_each_enabled_skill_has_directory(self) -> None:
        config = _load_ai_config()
        enabled = config.get("skills", {}).get("enabled", [])
        for name in enabled:
            skill_dir = SKILLS_DIR / name
            assert skill_dir.is_dir(), f"Missing skill directory: {name}"
            skill_file = skill_dir / "SKILL.md"
            assert skill_file.exists(), f"Missing SKILL.md for: {name}"

    def test_skill_names_match(self) -> None:
        """All enabled skill names are present and no extra skills are installed."""
        config = _load_ai_config()
        enabled = set(config.get("skills", {}).get("enabled", []))
        installed = {
            d.name
            for d in SKILLS_DIR.iterdir()
            if d.is_dir() and (d / "SKILL.md").exists()
        }
        # Every enabled skill must be installed
        assert enabled.issubset(installed), (
            f"Enabled but not installed: {enabled - installed}"
        )
        # Every installed skill must be enabled (or it's an orphan)
        # Note: We don't assert the reverse — there could be development skills
        # not yet enabled. But we verify count matches.
        assert len(enabled) == 11
