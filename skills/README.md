# AI Trading Skills

Skills in this directory are runtime prompt modules for the AI paper-trading account.

`config/ai_agent.yaml` controls which skills are injected into `accounts/ai/data/ai_prompt.txt`.

Each skill must:

- Add a specific data lens, source class, or reasoning pattern.
- Preserve `live-paper-only` operation.
- Require provenance for factual claims.
- Avoid live trading, signing, fund movement, paid API spend, or historical simulated trade outcomes.

Add new skills as `skills/<skill-name>/SKILL.md`, then list `<skill-name>` under `skills.enabled` in `config/ai_agent.yaml`.

