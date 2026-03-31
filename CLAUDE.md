# InfluencerOps Agent

Influencer Operations AI Agent - Email triage, draft generation, workflow automation for influencer communications.

## Tech Stack

- **Language**: Python 3.11+
- **Framework**: FastAPI + Uvicorn
- **Database**: PostgreSQL (asyncpg) + SQLAlchemy 2.0 (async) + Alembic
- **Cache/Queue**: Redis + Celery
- **LLM**: Anthropic Claude (triage/drafting) + OpenAI (embeddings) + ChromaDB (RAG)
- **Integrations**: Gmail API, Slack SDK, HubSpot API, Notion API
- **Validation**: Pydantic v2 + pydantic-settings
- **Logging**: structlog (structured JSON logging)

## Commands

```bash
make dev            # Start dev server (uvicorn --reload on :8000)
make test           # Run tests with coverage
make lint           # ruff check + mypy strict
make format         # black + ruff --fix
make migrate        # alembic upgrade head
make worker         # Start Celery worker
make beat           # Start Celery beat scheduler
make docker-up      # Start PostgreSQL + Redis via docker-compose
make docker-down    # Stop Docker services
```

Single test: `pytest tests/test_triage.py -v`

## Architecture

```
src/
  main.py              # FastAPI app factory + lifespan
  api/
    routes.py          # All API endpoints (prefix: /api/v1)
    schemas.py         # Pydantic request/response models
  agents/
    triage.py          # TriageAgent - email classification via Claude
  services/
    autopilot.py       # Auto-send decisions
    drafting.py        # Email draft generation
    guardrail.py       # Content safety (PII, injection, policy checks)
    metrics.py         # Dashboard metrics
    prospecting.py     # Outreach campaign management
    rag.py             # RAG retrieval (SOPs, templates, history)
    workflow.py        # State machine engine (Redis-persisted)
    audit.py           # Audit logging
  adapters/
    base.py            # BaseAdapter ABC (initialize/health_check/close)
    gmail.py           # Gmail API adapter
    slack.py           # Slack adapter
    hubspot.py         # HubSpot CRM adapter
    notion.py          # Notion SOP sync adapter
  core/
    config.py          # Settings via pydantic-settings (.env)
    database.py        # Async SQLAlchemy engine + session
    logging.py         # structlog setup
    redis.py           # Redis connection manager
  models/
    base.py            # DeclarativeBase (UUID pk, created_at, updated_at)
    influencer.py      # Influencer model
    conversation.py    # Conversation model
    message.py         # Message model
    task.py            # Task model
    audit_log.py       # AuditLog model
  workers/
    celery_app.py      # Celery configuration
    tasks.py           # Async tasks (sync, follow-up, etc.)
tests/
  conftest.py          # Fixtures: in-memory SQLite async engine + session
  test_triage.py       # TriageAgent tests
```

## Code Conventions

- **Formatting**: black (line-length=100), ruff (E/F/I/N/W/UP rules)
- **Types**: mypy strict mode. Always add type annotations to function signatures
- **Imports**: Use `from __future__ import annotations` at top of every module
- **Models**: SQLAlchemy 2.0 `Mapped[T]` + `mapped_column()`. All PKs are `UUID`
- **Schemas**: Pydantic v2 `BaseModel`. Use `from_attributes = True` for ORM models
- **Adapters**: Extend `BaseAdapter` ABC. Must implement `initialize()`, `health_check()`, `close()`
- **Services**: Stateless classes. Get logger via `get_logger(self.__class__.__name__)`
- **Config**: All settings via `src.core.config.settings`. Secrets use `SecretStr`
- **Async**: All DB and Redis ops are async. Use `async/await` consistently
- **Logging**: Use `structlog` via `get_logger()`. Pass structured kwargs, not f-strings

## Security

- **NEVER** commit `.env`, `*_token.json`, `*_credentials.json`
- Guardrail checks (PII, prompt injection, financial promises) must run before any outbound content
- Use `GuardrailService.sanitize_for_prompt()` when including user content in LLM prompts
- Use `SecretStr` for all API keys and secrets in Settings

## Workflow States

Email conversations follow this state machine (defined in `src/services/workflow.py`):
`NEW -> TRIAGING -> DRAFTING -> REVIEWING -> APPROVED -> SENDING -> SENT -> WAITING_REPLY -> CLOSED`

Key branch: TRIAGING filters spam/out_of_scope to CLOSED. FAILED state retries up to 3x.

## Testing

- Framework: pytest + pytest-asyncio (auto mode)
- DB fixtures use in-memory SQLite (`aiosqlite`)
- Mock external APIs (Gmail, Slack, HubSpot, Notion, Anthropic) - never call real services in tests
- Test file naming: `tests/test_<module>.py`
