---
paths:
  - "src/services/guardrail.py"
  - "src/services/drafting.py"
  - "src/services/autopilot.py"
  - "src/agents/**/*.py"
---

# Security Gotchas

- Guardrail checks run in this order: prompt injection → length → forbidden terms → PII → financial → policy (LLM). **Do not reorder** — injection must be caught first to prevent malicious content from reaching the LLM policy check
- If prompt injection is detected, skip the LLM-based policy check entirely (the content could manipulate it)
- `_parse_response()` must handle both raw JSON and ```json fenced blocks — LLMs are inconsistent
- Autopilot auto-send requires ALL of: confidence >= threshold, intent in allowed list, guardrails passed, kill switch off. Missing any one → human review
- `sanitize_for_prompt()` escapes delimiter patterns (`[SYSTEM]`, `<system>`, `### SYSTEM`) — call this on any user content before prompt inclusion
- PII masking intentionally skips email addresses in email content — they're expected in that context
- Guardrail `_check_policy_llm` uses `claude-haiku-4-20250514` (fast model) — don't change to a larger model; guardrails must be low-latency
