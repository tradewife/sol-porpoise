"""Tests for scaffold-report-template feature (VAL-SCAFFOLD-004, VAL-SCAFFOLD-005)."""

from __future__ import annotations

import csv
import json
import subprocess
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent


class TestReportWriter:
    def test_report_writer_creates_file(self, tmp_path: Path) -> None:
        from engine.report import ReportWriter

        writer = ReportWriter(tmp_path)
        path = writer.write(status="no_trade")
        assert path.exists()
        assert path.parent == tmp_path

    def test_report_contains_no_trade(self, tmp_path: Path) -> None:
        from engine.report import ReportWriter

        writer = ReportWriter(tmp_path)
        path = writer.write(status="no_trade")
        content = path.read_text()
        assert "no_trade" in content

    def test_report_has_all_section_headers(self, tmp_path: Path) -> None:
        from engine.report import ReportWriter

        writer = ReportWriter(tmp_path)
        path = writer.write(status="no_trade")
        content = path.read_text()
        for letter, title in [
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
        ]:
            assert f"## {letter}. {title}" in content, f"Missing section {letter}. {title}"

    def test_report_has_valid_timestamps(self, tmp_path: Path) -> None:
        from engine.report import ReportWriter

        writer = ReportWriter(tmp_path)
        path = writer.write(status="no_trade")
        content = path.read_text()
        assert "Australia/Sydney" in content
        assert "Run ID" in content

    def test_report_filename_has_timestamp(self, tmp_path: Path) -> None:
        from engine.report import ReportWriter

        writer = ReportWriter(tmp_path)
        path = writer.write(status="no_trade")
        name = path.name
        assert "Australia-Sydney" in name
        assert name.endswith("_report.md")

    def test_set_section_content(self, tmp_path: Path) -> None:
        from engine.report import ReportWriter

        writer = ReportWriter(tmp_path)
        writer.set_section("A", "Custom market snapshot content.")
        path = writer.write(status="no_trade")
        content = path.read_text()
        assert "Custom market snapshot content." in content


class TestDryRunReport:
    def test_dry_run_produces_report(self, tmp_path: Path) -> None:
        from engine.report import build_dry_run_report

        path = build_dry_run_report(tmp_path)
        assert path.exists()

    def test_dry_run_no_trade(self, tmp_path: Path) -> None:
        from engine.report import build_dry_run_report

        path = build_dry_run_report(tmp_path)
        content = path.read_text()
        assert "no_trade" in content

    def test_dry_run_all_sections(self, tmp_path: Path) -> None:
        from engine.report import build_dry_run_report

        path = build_dry_run_report(tmp_path)
        content = path.read_text()
        for letter in "ABCDEFGHIJKL":
            assert f"## {letter}." in content

    def test_dry_run_timestamps_valid(self, tmp_path: Path) -> None:
        from engine.report import build_dry_run_report

        path = build_dry_run_report(tmp_path)
        content = path.read_text()
        # At least 2 Australia/Sydney timestamps (header + section A)
        count = content.count("Australia/Sydney")
        assert count >= 2, f"Expected >= 2 Australia/Sydney references, got {count}"


class TestRunScanEntryPoint:
    def test_plumbing_dry_run_exits_0(self) -> None:
        result = subprocess.run(
            [sys.executable, "-m", "engine.run_scan", "--mode", "plumbing-dry-run"],
            capture_output=True,
            text=True,
            cwd=str(PROJECT_ROOT),
        )
        assert result.returncode == 0, f"Exit code {result.returncode}: {result.stderr}"

    def test_dry_run_creates_report(self) -> None:
        result = subprocess.run(
            [sys.executable, "-m", "engine.run_scan", "--mode", "plumbing-dry-run"],
            capture_output=True,
            text=True,
            cwd=str(PROJECT_ROOT),
        )
        assert result.returncode == 0
        reports_dir = PROJECT_ROOT / "reports"
        md_files = list(reports_dir.glob("*_report.md"))
        assert len(md_files) >= 1, "Expected at least one report file"

    def test_dry_run_no_paper_orders(self) -> None:
        subprocess.run(
            [sys.executable, "-m", "engine.run_scan", "--mode", "plumbing-dry-run"],
            capture_output=True,
            text=True,
            cwd=str(PROJECT_ROOT),
        )
        paper_orders = PROJECT_ROOT / "ledgers" / "paper_orders.csv"
        lines = paper_orders.read_text().strip().split("\n")
        assert len(lines) == 1, "paper_orders.csv should have only header row"

    def test_report_has_valid_run_id(self) -> None:
        subprocess.run(
            [sys.executable, "-m", "engine.run_scan", "--mode", "plumbing-dry-run"],
            capture_output=True,
            text=True,
            cwd=str(PROJECT_ROOT),
        )
        reports_dir = PROJECT_ROOT / "reports"
        md_files = sorted(reports_dir.glob("*_report.md"))
        latest = md_files[-1]
        content = latest.read_text()
        assert "run_" in content  # Run ID starts with "run_"

    def test_report_contains_mode(self) -> None:
        subprocess.run(
            [sys.executable, "-m", "engine.run_scan", "--mode", "plumbing-dry-run"],
            capture_output=True,
            text=True,
            cwd=str(PROJECT_ROOT),
        )
        reports_dir = PROJECT_ROOT / "reports"
        md_files = sorted(reports_dir.glob("*_report.md"))
        latest = md_files[-1]
        content = latest.read_text()
        assert "live-paper-only" in content or "dry run" in content.lower()


class TestRunScanScript:
    def test_script_is_executable(self) -> None:
        script = PROJECT_ROOT / "scripts" / "run_scan.sh"
        assert script.exists()
        assert script.stat().st_mode & 0o111, "run_scan.sh must be executable"

    def test_script_has_shebang(self) -> None:
        script = PROJECT_ROOT / "scripts" / "run_scan.sh"
        first_line = script.read_text().split("\n")[0]
        assert first_line == "#!/bin/bash"
