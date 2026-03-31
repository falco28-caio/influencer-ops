# InfluencerOps — Claude Code Guide

## Project Overview

InfluencerOps is an AI-powered influencer relationship management system. It automates email triage, draft generation, human-in-the-loop approvals via Slack, and outreach campaigns.

**Stack:** FastAPI · Celery · PostgreSQL · Redis · Gmail API · HubSpot · Slack · Notion

## Key Entry Points

| File | Purpose |
|------|---------|
| `src/main.py` | FastAPI application |
| `src/workers/tasks.py` | All Celery tasks |
| `src/workers/celery_app.py` | Celery beat schedule |
| `src/agents/triage.py` | Email triage agent |
| `src/services/drafting.py` | Draft generation service |
| `src/services/autopilot.py` | Autopilot decision engine |

## Development

```bash
# Start all services
docker-compose up

# Run tests
pytest tests/

# Run linter
ruff check src/
```

## Remote Control — Scheduled Triggers

The following Claude Code remote triggers should be set up via `/schedule`:

### 1. Daily Health Check
- **Schedule:** `0 9 * * *` (09:00 UTC daily)
- **Prompt:** Check the influencer-ops system health: review failed tasks in the DB, summarize any stuck conversations, and post a Slack digest if issues are found.
- **Branch:** `main`

### 2. Weekly Analytics Report
- **Schedule:** `0 8 * * 1` (Monday 08:00 UTC)
- **Prompt:** Generate a weekly analytics report: count emails processed, approval rates, autopilot hit rate, and top influencer activity. Post summary to Slack.
- **Branch:** `main`

### 3. SOP Sync Monitor
- **Schedule:** `0 6 * * *` (06:00 UTC daily)
- **Prompt:** Verify the Notion SOP sync ran successfully (check `sync_notion_task` last run). If it hasn't run in 25 hours, trigger a re-sync and alert Slack.
- **Branch:** `main`

### Creating Triggers

Once authenticated with claude.ai, run:

```bash
# List existing triggers
/schedule list

# Create a new trigger
/schedule
```

Or use the RemoteTrigger API directly (requires org UUID resolution).

## Celery Beat Schedule (Always-On)

These run automatically via `celery beat` — no Claude trigger needed:

| Task | Frequency |
|------|-----------|
| `poll_gmail` | Every 60s |
| `check_pending_reminders` | Every 5 min |
| `sync_hubspot_task` | Every hour |
| `run_active_campaigns` | Every 30 min |

## Kill Switch

Set `KILL_SWITCH=1` in Redis to halt all automated processing immediately. All tasks check this before executing.

```bash
redis-cli set kill_switch 1
```
