# Promotion Decisions

Prior decisions to keep live-paper-only, block live trading, approve a limited
experiment, or roll back. Any mode change requires explicit human approval via
file creation — the self-improvement harness has **no** code path that changes
mode from `live-paper-only`.

## Decision Log

Each decision entry should follow this structure:

```
### [DEC-ID] Title (date Australia/Sydney)

- **Decision**: What was decided
- **Rationale**: Why, with evidence references
- **Approved by**: human | auto-blocked | auto-maintained
- **Status**: active | superseded | reverted
```

| Date (AEST) | Decision | Rationale | Approved by |
|-------------|----------|-----------|-------------|
| 2026-06-02 | Maintain live-paper-only | Initial scaffold, no paper outcomes yet | auto-maintained |

## Active Promotion Gates

The system remains in `live-paper-only` mode until **all** gates are satisfied
and human approval is explicit:

- [ ] At least 50 paper trade candidates evaluated, **or** 30 days of scheduled runs (whichever is longer)
- [ ] All promotion evidence from forward live paper trades logged before outcomes occurred
- [ ] Positive expectancy after fees, funding, and slippage proxy
- [ ] Profit factor > 1.25
- [ ] Max drawdown within predefined small-account policy
- [ ] No critical data/provenance failures in last 10 runs
- [ ] Passive-entry checks passed on all promoted candidates
- [ ] Live-paper/live-capped reconciliation implemented
- [ ] Human explicitly approves live-capped mode

**Current gate status: 0 / 8 gates passed.**
