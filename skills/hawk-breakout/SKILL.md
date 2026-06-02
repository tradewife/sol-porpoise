# Hawk Breakout

This skill interprets the pre-computed Hawk breakout signals that appear in the
Hawk Breakout Signals section of the hourly prompt.

Signal logic is based on Senpi's Hawk v1.0.0: 7-day high/low breakout with
Smart Money tilt >= 55% as a hard gate, 4h trend alignment, and volume
confirmation. See engine/hawk_breakout.py for the deterministic implementation.

This skill is AI-layer only. It does not call engine/hawk_breakout.py --
that runs deterministically in the scan loop before the prompt is built.

## Signal fields

Each entry in Hawk Breakout Signals contains:
- market: symbol (e.g. SOL-PERP)
- signal: long | short | none
- score: integer 0-9 per Hawk scoring table
- basis.htf_breakout: true if latest close breaks 7-day high/low
- basis.sm_tilt_supports: true if Smart Money tilt >= 55% in signal direction
- basis.sm_long_pct: numeric SM long percentage
- basis.breakout_magnitude_pct: % move beyond the 7-day boundary
- basis.4h_trend_aligned: true if 4h trend agrees with direction
- basis.volume_spike: true if last 1h volume >= 1.5x 7-day average
- basis.structure_classification: what market-structure-context assigned

## Interpretation rules

- signal = "none": no breakout candidate this cycle. Do not force an entry.
- score >= 7 + structure_confirmed: strong breakout -- treat as high probability.
- score 5-6 + structure_partial: valid but lower conviction -- medium probability.
- score < 5 or structure_rejected: hawk vetoed -- do not use breakout setup type.

## Hard veto

If basis.structure_classification = "structure_rejected", the signal is already
"none" from the engine. Never override a structure_rejected veto, even if
the market looks interesting from other signals.

## Scoring table

| Signal component | Points |
|---|---|
| Breakout magnitude >= 1.0% | +3 |
| Breakout magnitude 0.3-1.0% | +2 |
| Breakout magnitude < 0.3% | +1 |
| SM tilt gate confirmed | +2 |
| SM strongly tilted (>= 70%) | +1 |
| 4h trend aligned | +2 |
| Volume >= 1.5x 7-day average | +1 |

## Dependencies

- Requires Hawk Breakout Signals section in the prompt (written by run_scan.py).
- Requires market-structure-context to have run first (structure gate is applied
  in the engine before this section is written).
- risk-execution-rails still governs all entry/stop/TP math.

## What this skill does NOT do

- Does not call engine/hawk_breakout.py.
- Does not modify engine/signals.py, engine/scoring.py, or engine/playbooks.py.
- Does not apply to --mode live-paper deterministic path.
- Does not override the SM tilt gate.
