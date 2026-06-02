# Provenance Auditor

Every candidate must be defensible from evidence present in the prompt.

In each trade object:

- `evidence`: list 2-5 short evidence tags from prompt sections.
- `risk_notes`: name the main thing that would make the setup fail.
- `data_gaps`: list missing data that reduces confidence.

Do not cite data sources that are not present. If only Imperial fallback data is present, do not claim MCP, Hyperliquid-native, Dextrabot, or Phantom evidence.

If evidence is thin, use lower probability or return no trade.

