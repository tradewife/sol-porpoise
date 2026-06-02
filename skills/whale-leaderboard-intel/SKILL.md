# Whale and Leaderboard Intel

Use whale, leaderboard, Dextrabot, Phantom position, or vault evidence only when it is present in the prompt.

Classification:

- `smart_money`: labeled fund/MM or repeatable risk-adjusted PnL evidence is present.
- `whale_unlabeled`: large size without enough alpha evidence.
- `unknown`: size, label, or performance context is missing.

Rules:

- Never copy trade.
- Treat whale direction as a signal component, not a trade command.
- Downgrade confidence when whale evidence conflicts with price, funding, OI, or liquidity.
- Prefer overlap between independent sources over a single large print.
- If whale data is absent, say so in the rationale or leave it out.

