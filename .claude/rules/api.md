---
paths:
  - "src/api/**/*.py"
---

# API Gotchas

- All routes live in `src/api/routes.py`, all schemas in `src/api/schemas.py` — don't split into multiple route files
- DB sessions via `Depends(get_session)` — never create sessions manually in routes
- Schema enums (e.g., `TaskStatusEnum`) must stay in sync with model/service enums — if you add a value to one, add it to the other
- Kill switch endpoint (`POST /kill-switch`) bypasses normal auth — it's an emergency control
- Slack webhook endpoints must handle the `url_verification` challenge type (return `challenge` field)
- Use `status_code=201` for creation endpoints, `status_code=204` for deletes
