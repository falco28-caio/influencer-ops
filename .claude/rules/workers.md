---
paths:
  - "src/workers/**/*.py"
---

# Celery Worker Gotchas

- Celery tasks are synchronous — use `run_async(coro)` helper to bridge into async code
- **Every task must check kill switch** at the start: `if await check_kill_switch(): return`
- Beat schedule intervals: Gmail poll=60s, reminders=300s, HubSpot sync=3600s, campaigns=1800s — don't shorten Gmail below 60s (API rate limits)
- `task_acks_late=True` means tasks re-execute on worker crash — all tasks must be idempotent
- `task_time_limit=600s` — if a task might exceed this (e.g., large campaign batch), split into subtasks
- The `process_incoming_email` task is the main pipeline: triage → find/create entities → draft → guardrail → autopilot decision → Slack approval. Keep this order
