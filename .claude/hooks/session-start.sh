#!/bin/bash
# Session start hook: verify dev environment is ready
# Returns warnings as systemMessage if issues found

ISSUES=""

# Check Python version
PYTHON_VERSION=$(python3 --version 2>/dev/null | grep -oP '\d+\.\d+')
if [ -z "$PYTHON_VERSION" ]; then
  ISSUES="${ISSUES}Python 3 not found. "
elif [ "$(echo "$PYTHON_VERSION < 3.11" | bc -l 2>/dev/null)" = "1" ]; then
  ISSUES="${ISSUES}Python ${PYTHON_VERSION} detected, 3.11+ required. "
fi

# Check if .env exists
if [ ! -f ".env" ]; then
  ISSUES="${ISSUES}.env file missing (copy from .env.example). "
fi

# Check if Docker services are running
if command -v docker &>/dev/null; then
  DB_RUNNING=$(docker compose ps db --format json 2>/dev/null | grep -c '"running"')
  REDIS_RUNNING=$(docker compose ps redis --format json 2>/dev/null | grep -c '"running"')
  if [ "$DB_RUNNING" = "0" ] 2>/dev/null; then
    ISSUES="${ISSUES}PostgreSQL not running (run: make docker-up). "
  fi
  if [ "$REDIS_RUNNING" = "0" ] 2>/dev/null; then
    ISSUES="${ISSUES}Redis not running (run: make docker-up). "
  fi
fi

# Check key tools
for tool in black ruff mypy pytest; do
  if ! command -v "$tool" &>/dev/null; then
    ISSUES="${ISSUES}${tool} not installed. "
  fi
done

if [ -n "$ISSUES" ]; then
  jq -n --arg issues "$ISSUES" '{
    "continue": true,
    "systemMessage": ("Environment issues detected: " + $issues)
  }'
else
  exit 0
fi
