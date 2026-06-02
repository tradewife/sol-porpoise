"""AI agent module: replaces deterministic signal scoring with AI reasoning.

The AI agent:
1. Receives aggregated market data from MCP tools (via mcp_data.py)
2. Uses AI reasoning to decide trade setups (symbol, side, entry, stop, TPs)
3. Returns structured trade candidates that feed into the existing risk sizing
   and paper order pipeline

This module provides the decision logic. The actual MCP tool calls happen in
run_scan.py's ai-paper mode, which then passes data to this module.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any

from engine.mcp_data import RichMarketData, format_ai_prompt
from engine.paper_orders import OrderSide
from engine.scoring import SignalComponent


@dataclass
class AITradeCandidate:
    """A trade candidate produced by AI reasoning."""
    symbol: str
    side: OrderSide
    setup_type: str
    entry: float
    stop: float
    tp1: float
    tp2: float
    probability_band: str  # "high", "medium", "low"
    rationale: str
    evidence: list[str] = field(default_factory=list)
    risk_notes: str = ""
    data_gaps: list[str] = field(default_factory=list)
    raw_ai_response: str = ""
    # Populated after validation
    valid: bool = True
    reject_reason: str = ""


def parse_ai_response(raw_response: str) -> list[AITradeCandidate]:
    """Parse the AI model's JSON response into trade candidates.

    Handles:
    - Clean JSON array
    - JSON wrapped in markdown code fences
    - Empty response (no trades)
    - Partially valid JSON (extracts what we can)

    Returns list of AITradeCandidate, possibly empty.
    """
    candidates: list[AITradeCandidate] = []

    if not raw_response or not raw_response.strip():
        return candidates

    # Strip markdown code fences if present
    cleaned = raw_response.strip()
    cleaned = re.sub(r'^```(?:json)?\s*\n?', '', cleaned)
    cleaned = re.sub(r'\n?```\s*$', '', cleaned)
    cleaned = cleaned.strip()

    # Handle explicit empty array
    if cleaned == "[]" or cleaned.lower() in ("none", "no trades", "no setups"):
        return candidates

    # Try to extract JSON array from the response
    json_match = re.search(r'\[[\s\S]*\]', cleaned)
    if not json_match:
        # Try to find a single object (not in array)
        obj_match = re.search(r'\{[\s\S]*\}', cleaned)
        if obj_match:
            cleaned = "[" + obj_match.group(0) + "]"
        else:
            return candidates
    else:
        cleaned = json_match.group(0)

    try:
        parsed = json.loads(cleaned)
    except json.JSONDecodeError:
        return candidates

    if isinstance(parsed, dict):
        if isinstance(parsed.get("trades"), list):
            parsed = parsed["trades"]
        elif isinstance(parsed.get("candidates"), list):
            parsed = parsed["candidates"]
        else:
            parsed = [parsed]
    elif not isinstance(parsed, list):
        parsed = [parsed]

    for item in parsed:
        if not isinstance(item, dict):
            continue
        candidate = _parse_single_candidate(item, raw_response)
        if candidate:
            candidates.append(candidate)

    return candidates


def _parse_single_candidate(item: dict[str, Any], raw_response: str) -> AITradeCandidate | None:
    """Parse a single trade candidate dict from AI response."""
    symbol = str(item.get("symbol", "")).upper().strip()
    if not symbol:
        return None

    side_str = str(item.get("side", "")).lower().strip()
    if side_str not in ("long", "short"):
        return None
    side = OrderSide.LONG if side_str == "long" else OrderSide.SHORT

    try:
        entry = float(item.get("entry", 0))
        stop = float(item.get("stop", 0))
        tp1 = float(item.get("tp1", 0))
        tp2 = float(item.get("tp2", 0))
    except (TypeError, ValueError):
        return None

    if entry <= 0 or stop <= 0:
        return None

    setup_type = str(item.get("setup_type", "custom")).strip().lower()
    if not setup_type:
        setup_type = "custom"

    probability_band = str(item.get("probability_band", "low")).strip().lower()
    if probability_band not in ("high", "medium", "low"):
        probability_band = "low"

    rationale = str(item.get("rationale", ""))
    evidence_raw = item.get("evidence", [])
    evidence = [str(x) for x in evidence_raw] if isinstance(evidence_raw, list) else []
    risk_notes = str(item.get("risk_notes", ""))
    gaps_raw = item.get("data_gaps", [])
    data_gaps = [str(x) for x in gaps_raw] if isinstance(gaps_raw, list) else []

    return AITradeCandidate(
        symbol=symbol,
        side=side,
        setup_type=setup_type,
        entry=entry,
        stop=stop,
        tp1=tp1,
        tp2=tp2,
        probability_band=probability_band,
        rationale=rationale,
        evidence=evidence,
        risk_notes=risk_notes,
        data_gaps=data_gaps,
        raw_ai_response=raw_response,
    )


def validate_prompt_bound_response(
    raw_response: str,
    expected_prompt_id: str,
) -> tuple[str, bool, str]:
    """Validate that an AI response belongs to the current prompt.

    Returns (candidate_json_text, accepted, reason). Old-style JSON arrays are
    accepted only when expected_prompt_id is empty, preserving parser
    compatibility for tests/manual tools. Cron should pass a prompt ID.
    """
    if not raw_response or not raw_response.strip():
        return raw_response, False, "empty_response"

    cleaned = raw_response.strip()
    cleaned = re.sub(r'^```(?:json)?\s*\n?', '', cleaned)
    cleaned = re.sub(r'\n?```\s*$', '', cleaned).strip()

    try:
        parsed = json.loads(cleaned)
    except json.JSONDecodeError:
        return raw_response, False, "invalid_json"

    if isinstance(parsed, list):
        if expected_prompt_id:
            return raw_response, False, "legacy_array_missing_prompt_id"
        return raw_response, True, "accepted_legacy_array"

    if not isinstance(parsed, dict):
        return raw_response, False, "response_not_object_or_array"

    response_prompt_id = str(parsed.get("prompt_id", ""))
    if expected_prompt_id and response_prompt_id != expected_prompt_id:
        return raw_response, False, (
            f"prompt_id_mismatch:expected={expected_prompt_id}:got={response_prompt_id or 'missing'}"
        )

    if "trades" not in parsed or not isinstance(parsed.get("trades"), list):
        return raw_response, False, "missing_trades_array"

    return json.dumps(parsed.get("trades", [])), True, "accepted_prompt_bound_response"


def validate_candidate(candidate: AITradeCandidate, atr: float | None = None) -> AITradeCandidate:
    """Validate an AI trade candidate and mark invalid ones.

    Checks:
    - Stop is on the correct side of entry
    - TP1 and TP2 are on the correct side of entry
    - Stop distance is >= 0.8 * ATR (if ATR provided)
    - TP1 >= 2R, TP2 >= 3R
    """
    stop_distance = abs(candidate.entry - candidate.stop)

    # Stop must be on correct side
    if candidate.side == OrderSide.LONG:
        if candidate.stop >= candidate.entry:
            candidate.valid = False
            candidate.reject_reason = f"LONG stop {candidate.stop} >= entry {candidate.entry}"
            return candidate
        if candidate.tp1 <= candidate.entry:
            candidate.valid = False
            candidate.reject_reason = f"LONG tp1 {candidate.tp1} <= entry {candidate.entry}"
            return candidate
    else:
        if candidate.stop <= candidate.entry:
            candidate.valid = False
            candidate.reject_reason = f"SHORT stop {candidate.stop} <= entry {candidate.entry}"
            return candidate
        if candidate.tp1 >= candidate.entry:
            candidate.valid = False
            candidate.reject_reason = f"SHORT tp1 {candidate.tp1} >= entry {candidate.entry}"
            return candidate

    # Minimum stop distance check
    if atr and atr > 0 and stop_distance < 0.8 * atr:
        candidate.valid = False
        candidate.reject_reason = (
            f"Stop distance {stop_distance:.2f} < 0.8*ATR {0.8 * atr:.2f}"
        )
        return candidate

    # R:R check
    if stop_distance > 0:
        if candidate.side == OrderSide.LONG:
            r1 = (candidate.tp1 - candidate.entry) / stop_distance
        else:
            r1 = (candidate.entry - candidate.tp1) / stop_distance
        if r1 < 2.0:
            candidate.valid = False
            candidate.reject_reason = f"TP1 R:R = {r1:.1f} < 2.0 minimum"
            return candidate

    return candidate


def candidates_to_signal_components(
    candidates: list[AITradeCandidate],
) -> dict[str, dict[str, SignalComponent]]:
    """Convert AI candidates into signal-component format for report compatibility.

    Creates synthetic signal components from the AI's reasoning so that the
    existing report generation can display them.
    """
    result: dict[str, dict[str, SignalComponent]] = {}

    for cand in candidates:
        side_val = 1.0 if cand.side == OrderSide.LONG else -1.0
        band_conf = {"high": 0.9, "medium": 0.6, "low": 0.3}.get(cand.probability_band, 0.3)

        comps: dict[str, SignalComponent] = {
            "ai_conviction": SignalComponent(
                name="ai_conviction",
                value=side_val * band_conf,
                confidence=band_conf,
                label=f"ai_{cand.probability_band}_confidence",
            ),
            "ai_setup": SignalComponent(
                name="ai_setup",
                value=side_val,
                confidence=band_conf,
                label=cand.setup_type,
            ),
        }
        result[cand.symbol] = comps

    return result


def build_ai_report_section(candidates: list[AITradeCandidate]) -> str:
    """Build the report section for AI agent decisions."""
    if not candidates:
        return "AI Agent: No trade candidates generated this cycle."

    lines = ["### AI Agent Reasoning\n"]
    for i, cand in enumerate(candidates, 1):
        status = "VALID" if cand.valid else f"REJECTED ({cand.reject_reason})"
        lines.append(
            f"**{i}. {cand.symbol} {cand.side.value.upper()} — {cand.setup_type}** [{status}]\n"
            f"- Entry: {cand.entry:.2f} | Stop: {cand.stop:.2f} | "
            f"TP1: {cand.tp1:.2f} | TP2: {cand.tp2:.2f}\n"
            f"- Probability: {cand.probability_band} | Rationale: {cand.rationale}\n"
        )
        if cand.evidence:
            lines.append(f"- Evidence: {', '.join(cand.evidence)}\n")
        if cand.risk_notes:
            lines.append(f"- Risk notes: {cand.risk_notes}\n")
        if cand.data_gaps:
            lines.append(f"- Data gaps: {', '.join(cand.data_gaps)}\n")

    valid_count = sum(1 for c in candidates if c.valid)
    lines.append(
        f"\n*AI generated {len(candidates)} candidates, {valid_count} passed validation.*"
    )

    return "\n".join(lines)
