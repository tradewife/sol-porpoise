# Failure Modes

Known ways the system has failed or produced weak candidates.
Used to inform risk assessments and improve future scan quality.

## Known Failure Modes

Each failure mode entry should follow this structure:

```
### [FM-ID] Title

- **Description**: What went wrong
- **Severity**: critical | high | medium | low
- **Frequency**: How often this has been observed
- **Root cause**: Underlying cause if identified
- **Mitigation**: What was done or should be done to prevent recurrence
- **Evidence**: Links to runs, reports, or source health entries
- **Status**: active | resolved | monitoring
```

_No failure modes recorded yet. Entries will be added as failures are observed during runs._

## Mitigation Strategies

General mitigation strategies applied across the system:

1. **Missing data degrades confidence** — never silently fill in bullish/bearish values
2. **Provenance-first** — every data point carries source, tier, timestamp, confidence
3. **Conservative fill logic** — same-candle ambiguity resolved conservatively
4. **Mode enforcement** — live-paper-only gate at run start
5. **Source health tracking** — degraded sources logged and confidence reduced
