"""Report writer: generates markdown reports with sections A-K."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from zoneinfo import ZoneInfo

AEST = ZoneInfo("Australia/Sydney")

SECTION_HEADERS = [
    ("A", "Market Snapshot"),
    ("B", "Trending Universe Chosen"),
    ("C", "Evidence Tables"),
    ("D", "On-chain and HL-native Flow Digest"),
    ("E", "Playbook Cards"),
    ("F", "Final Paper Trades"),
    ("G", "X Post Draft"),
    ("H", "Assumptions and Gaps"),
    ("I", "Citations and Provenance"),
    ("J", "OutcomeGraph CSV"),
    ("K", "Prompt and System Audit"),
    ("L", "Signal Learning Output"),
]


def _aest_now() -> datetime:
    return datetime.now(AEST)


def _aest_iso(dt: datetime | None = None) -> str:
    dt = dt or _aest_now()
    return dt.strftime("%Y-%m-%d %H:%M:%S Australia/Sydney")


def _run_id() -> str:
    return _aest_now().strftime("run_%Y%m%dT%H%M%S_AEST")


class ReportWriter:
    def __init__(self, report_dir: str | Path = "reports") -> None:
        self.report_dir = Path(report_dir)
        self.report_dir.mkdir(parents=True, exist_ok=True)
        self.run_id = _run_id()
        self.timestamp_aest = _aest_iso()
        self.sections: dict[str, str] = {}

    def set_section(self, letter: str, content: str) -> None:
        self.sections[letter] = content

    def _build_report(self, status: str) -> str:
        lines: list[str] = []
        lines.append(f"# Sol Porpoise Run Report")
        lines.append("")
        lines.append(f"- **Run ID**: {self.run_id}")
        lines.append(f"- **Timestamp**: {self.timestamp_aest}")
        lines.append(f"- **Status**: `{status}`")
        lines.append("")

        for letter, title in SECTION_HEADERS:
            key = letter
            lines.append(f"## {letter}. {title}")
            lines.append("")
            lines.append(self.sections.get(key, "_No data for this section._"))
            lines.append("")

        return "\n".join(lines)

    def write(self, status: str = "no_trade") -> Path:
        content = self._build_report(status)
        ts = _aest_now().strftime("%Y-%m-%dT%H-%M-%S_Australia-Sydney")
        filename = f"{ts}_report.md"
        path = self.report_dir / filename
        path.write_text(content, encoding="utf-8")
        return path


def build_dry_run_report(report_dir: str | Path = "reports") -> Path:
    """Build a plumbing-dry-run report with all A-K sections, status no_trade."""
    writer = ReportWriter(report_dir)

    writer.set_section("A", (
        "Plumbing dry run. No live market data fetched.\n\n"
        f"Timestamp: {_aest_iso()}\n\n"
        "Market regime: _unknown (dry run)_."
    ))

    writer.set_section("B", (
        "No universe selected (dry run).\n\n"
        "Core symbols (BTC, ETH, SOL) would always be included."
    ))

    writer.set_section("C", (
        "Steps 2-6 skipped. No evidence tables (dry run)."
    ))

    writer.set_section("D", (
        "No on-chain or HL-native flow data (dry run)."
    ))

    writer.set_section("E", (
        "No playbook cards (dry run)."
    ))

    writer.set_section("F", (
        "No paper trades (dry run). Status: `no_trade`."
    ))

    writer.set_section("G", (
        "No X post draft (dry run)."
    ))

    writer.set_section("H", (
        "This is a plumbing dry run. No adapters were called.\n\n"
        "- Market data: unavailable (dry run)\n"
        "- Adapter status: not exercised\n"
        "- Mode enforcement: live-paper-only\n"
        "- Internal memory flag: used=false"
    ))

    writer.set_section("I", (
        "No citations (dry run). No data sources were accessed."
    ))

    writer.set_section("J", (
        "```csv\n"
        "date_Australia/Sydney,symbol,setup,side,entry,stop,tp1,tp2,filled,"
        "entry_ts_Australia/Sydney,exit_ts_Australia/Sydney,result_R,"
        "max_FvE,max_AdE,fees_bps,slippage_bps,notes,provenance_tags\n"
        "```\n\n"
        "No outcome rows (dry run)."
    ))

    audit_lines = [
        "| Metric | Score |",
        "|--------|-------|",
        "| Data completeness | N/A (dry run) |",
        "| Provenance quality | N/A (dry run) |",
        "| Passive-entry correctness | N/A (dry run) |",
        "| Risk sizing correctness | N/A (dry run) |",
        "| Paper-execution evaluability | N/A (dry run) |",
        "| Report usefulness | validated (template renders) |",
        "| Top failure mode | None (dry run) |",
        f"| Next improvement | Run with live adapters |",
    ]
    writer.set_section("K", "\n".join(audit_lines))

    writer.set_section("L", (
        "No signal outcome data yet (dry run)."
    ))

    return writer.write(status="no_trade")
