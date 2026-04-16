# InfluencerOps Agent - Development Guide

## Project Overview

InfluencerOps is a communication efficiency and new influencer acquisition agent built with Python/FastAPI.
It automates influencer outreach workflows via Gmail, Slack, HubSpot, and Notion integrations.

## Tech Stack

- **Language**: Python 3.11+
- **Framework**: FastAPI + Uvicorn
- **Database**: PostgreSQL (asyncpg) + SQLAlchemy 2.0 + Alembic
- **Task Queue**: Celery + Redis
- **LLM**: Anthropic Claude (primary), OpenAI (embeddings)
- **Vector Store**: ChromaDB (RAG)
- **Adapters**: Gmail, Slack, HubSpot, Notion

## Project Structure

```
src/
  adapters/    # External service integrations (Gmail, Slack, HubSpot, Notion)
  agents/      # AI agent logic (triage)
  api/         # FastAPI routes and schemas
  core/        # Config, database, logging, Redis setup
  models/      # SQLAlchemy ORM models
  services/    # Business logic (drafting, guardrail, prospecting, RAG, workflow)
  workers/     # Celery tasks and app config
  main.py      # FastAPI application entry point
```

## Development Commands

```bash
make dev          # Start dev server (uvicorn, port 8000)
make test         # Run tests with coverage
make lint         # ruff check + mypy
make format       # black + ruff fix
make migrate      # Run DB migrations
make docker-up    # Start PostgreSQL + Redis
make worker       # Start Celery worker
```

## Code Style

- Formatter: **black** (line-length 100)
- Linter: **ruff** (rules: E, F, I, N, W, UP)
- Type checker: **mypy** (strict mode)
- Target: Python 3.11+
- Use `from __future__ import annotations` for forward references
- All async functions use `async/await` — no blocking I/O in async context
- Pydantic v2 for all schemas and settings

## Conventions

- Adapters inherit from `src/adapters/base.py` base class
- Models inherit from `src/models/base.py` (declarative base with common fields)
- All API routes are in `src/api/routes.py`, request/response schemas in `src/api/schemas.py`
- Environment variables via `pydantic-settings` in `src/core/config.py`
- Structured logging via `structlog`
- Retry logic via `tenacity`

## Agent Modes

- **copilot**: Human-in-the-loop (default) — drafts require approval
- **autopilot**: Autonomous execution when confidence >= threshold (0.9)
- Mode is set via `AGENT_MODE` env var

## Guardrails

- PII masking is enabled by default (`PII_MASKING_ENABLED=true`)
- Max draft length: 5000 chars
- All actions are logged to `audit_log` table

## Design System

When a `DESIGN.md` file exists at the project root, treat it as the single source of truth for all UI/frontend work.
All UI components must use tokens defined in DESIGN.md (colors, typography, spacing).

## MCP Integrations

See `.mcp.json` for configured MCP servers. Available integrations:
- **Figma**: Design-to-code via Figma Dev Mode MCP
- **Notion**: Database and page operations
- **Slack**: Channel messaging and search
- **GitHub**: Repository and PR management
