---
paths:
  - "src/services/guardrail.py"
  - "src/agents/**/*.py"
  - "src/services/drafting.py"
  - "src/services/autopilot.py"
---

# Security Rules

- All user-provided content MUST pass through `GuardrailService.check_content()` before LLM processing or outbound sending
- Use `sanitize_for_prompt()` when embedding user content into LLM prompts
- Check for prompt injection before passing email content to Claude
- PII masking must be applied before logging any email content
- Financial commitment patterns must be flagged for human review
- Autopilot auto-send requires confidence >= threshold AND intent in allowed list
- Kill switch (`settings.kill_switch_key`) must be checked before any auto-send
