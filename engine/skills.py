"""Repo-local AI trading skill loader."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class LoadedSkill:
    """A prompt skill loaded from skills/<name>/SKILL.md."""

    name: str
    path: str
    content: str


def load_enabled_skills(
    project_root: Path,
    ai_config: dict[str, Any],
) -> tuple[list[LoadedSkill], list[str]]:
    """Load skills configured in config/ai_agent.yaml.

    Returns (skills, warnings). Missing non-core skills become warnings.
    Missing core skills raise FileNotFoundError when fail_on_missing_core_skill
    is true.
    """
    skills_cfg = ai_config.get("skills", {}) if isinstance(ai_config, dict) else {}
    enabled = skills_cfg.get("enabled", []) or []
    max_chars = int(skills_cfg.get("max_chars_per_skill", 2500) or 2500)
    fail_on_missing_core = bool(skills_cfg.get("fail_on_missing_core_skill", True))

    loaded: list[LoadedSkill] = []
    warnings: list[str] = []

    for name in enabled:
        skill_name = str(name).strip()
        if not skill_name:
            continue
        path = project_root / "skills" / skill_name / "SKILL.md"
        if not path.exists():
            msg = f"missing skill: {skill_name} ({path})"
            if fail_on_missing_core and skill_name == "core-trader-mandate":
                raise FileNotFoundError(msg)
            warnings.append(msg)
            continue

        content = path.read_text(encoding="utf-8").strip()
        if max_chars > 0 and len(content) > max_chars:
            content = content[:max_chars].rstrip() + "\n\n[truncated]"
        loaded.append(LoadedSkill(name=skill_name, path=str(path), content=content))

    return loaded, warnings


def format_skills_for_prompt(skills: list[LoadedSkill]) -> str:
    """Format loaded skills as a prompt section."""
    if not skills:
        return ""

    lines = ["## Active Trading Skills", ""]
    for skill in skills:
        lines.extend([
            f"### {skill.name}",
            skill.content,
            "",
        ])
    return "\n".join(lines).strip()

