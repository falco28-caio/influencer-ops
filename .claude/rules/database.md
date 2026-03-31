---
paths:
  - "src/models/**/*.py"
  - "alembic/**/*.py"
---

# Database Rules

- All models extend `src.models.base.Base` (provides UUID pk, created_at, updated_at)
- Use `Mapped[T]` with `mapped_column()` (SQLAlchemy 2.0 style) - never use `Column()`
- UUID primary keys: `mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)`
- DateTime fields must be timezone-aware: `DateTime(timezone=True)`
- After adding/modifying models, create a migration: `alembic revision --autogenerate -m "description"`
- Apply migrations: `alembic upgrade head`
- All DB operations must be async (use `AsyncSession`)
