# Risk and Execution Rails

The machine validator enforces final sizing, but the AI must propose only candidates that respect these rails:

- Account equity: use the value in the prompt.
- Risk per trade: 20% of paper equity.
- Leverage target: 9x to 12x.
- Long entry must be at or below current price proxy.
- Short entry must be at or above current price proxy.
- Long stop must be below entry; short stop must be above entry.
- TP1 must be at least 2R; TP2 must be at least 3R.
- Stop distance must be at least 0.8x ATR estimate.
- Do not duplicate an existing same-symbol same-side position.

If the best thesis cannot satisfy passive entry and stop math, return no trade.

