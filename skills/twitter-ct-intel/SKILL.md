# Twitter CT Intel

Use Twitter/X Crypto Twitter (CT) search data only when it is present in the
"## Twitter CT Intel" section of the prompt. If that section is absent or
marked unavailable, treat CT sentiment as unknown and omit it from evidence.

This data comes from twitter-cli (public-clis/twitter-cli). It is AI-agent
use only. It is never scored and never influences the deterministic scan loop,
signals.py, or scoring.py.

## What to do with CT data

- Use as soft context, not a primary signal. CT sentiment is directional
  colour, not alpha. It confirms or contradicts what price, funding, and OI
  are already saying.
- Cite it in the `evidence` array as "CT-sentiment:<symbol>:<direction>"
  only when it materially agrees with at least one hard signal.
- If CT sentiment conflicts with price action or funding, flag it in
  `data_gaps` or `risk_notes` and reduce confidence slightly. Do not let CT
  override a clear structural trade.
- Never cite a tweet directly in the rationale. Summarise the signal
  (e.g. "CT mixed/bullish on SOL-PERP").

## Classification

- `ct_bullish`: majority of recent CT activity skews long, pump, or breakout.
- `ct_bearish`: majority skews short, fade, or dump.
- `ct_mixed`: both directions present, no clear edge.
- `ct_unknown`: no data returned, or section absent.

## Rules

- Low confidence source (0.35). Do not upgrade to medium without corroboration
  from at least two on-chain or structural signals.
- Rate limits are real. If CT data is absent for some symbols but present for
  others, use what is available and note the gap.
- Never fabricate tweet content or infer activity beyond what is shown.
- CT hype alone is not a trade. Require structural confluence.
