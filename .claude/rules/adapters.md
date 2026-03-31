---
paths:
  - "src/adapters/**/*.py"
---

# Adapter Rules

- All adapters extend `BaseAdapter` from `src.adapters.base`
- Must implement: `async initialize()`, `async health_check() -> bool`, `async close()`
- Support async context manager pattern (`__aenter__`/`__aexit__`)
- Get API credentials from `src.core.config.settings` (never hardcode)
- Use `tenacity` for retry logic on external API calls
- Log all external API calls with structlog
