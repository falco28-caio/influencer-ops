---
paths:
  - "src/adapters/**/*.py"
---

# Adapter Gotchas

- All adapters extend `BaseAdapter` — implement `initialize()`, `health_check()`, `close()`
- Gmail adapter uses OAuth2 tokens stored in `data/gmail_token.json` — this file is gitignored and must exist at runtime
- HubSpot `list_contacts()` returns paginated results — always handle `offset`/`has_more`
- Notion adapter is **read-only** — SOPs are the source of truth, never write back
- Slack approval messages use interactive blocks — `action_id` must match registered handlers in `SlackAdapter.register_action_handler()`
- Use `tenacity` retry with exponential backoff on all external HTTP calls — transient failures are expected
- Health check failures should log warnings but not crash the app — adapters can be unavailable at startup
