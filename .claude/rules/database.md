---
paths:
  - "src/models/**/*.py"
  - "alembic/**/*.py"
---

# Database Gotchas

- Base class (`src.models.base.Base`) already provides `id`, `created_at`, `updated_at` — don't redeclare them
- PostgreSQL UUID uses `UUID(as_uuid=True)` — always set `as_uuid=True` or you get raw bytes
- DateTime MUST be `DateTime(timezone=True)` — naive datetimes break cross-TZ queries
- Relationships: use `lazy="selectin"` for collections loaded in async context (default `lazy="select"` causes greenlet errors with asyncpg)
- After model changes: `alembic revision --autogenerate -m "description"` then `alembic upgrade head`
- Tests use SQLite — avoid PostgreSQL-only features (JSONB operators, array types) in model definitions; use `JSON` type instead for portability
