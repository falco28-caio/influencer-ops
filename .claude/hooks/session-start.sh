#!/bin/bash
# Session start hook: verify dev environment is ready
# Returns warnings as systemMessage if issues found
# Dependencies: none (all checks use portable POSIX constructs)

ISSUES=""

# Check Python version (portable — no grep -P or bc)
if command -v python3 &>/dev/null; then
  PYTHON_VERSION=$(python3 -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')" 2>/dev/null)
  PYTHON_MINOR=$(python3 -c "import sys; print(sys.version_info.minor)" 2>/dev/null)
  if [ -n "$PYTHON_MINOR" ] && [ "$PYTHON_MINOR" -lt 11 ] 2>/dev/null; then
    ISSUES="${ISSUES}Python ${PYTHON_VERSION} detected, 3.11+ required. "
  fi
else
  ISSUES="${ISSUES}Python 3 not found. "
fi

# Check if .env exists
if [ ! -f ".env" ]; then
  ISSUES="${ISSUES}.env file missing (copy from .env.example). "
fi

# Check if Docker services are running (only if docker is available)
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

# Check jq (required by other hooks)
if ! command -v jq &>/dev/null; then
  ISSUES="${ISSUES}jq not installed (required by hooks). "
fi

if [ -n "$ISSUES" ]; then
  # Use jq if available, otherwise output raw JSON
  if command -v jq &>/dev/null; then
    jq -n --arg issues "$ISSUES" '{
      "continue": true,
      "systemMessage": ("Environment issues detected: " + $issues)
    }'
  else
    echo "{\"continue\": true, \"systemMessage\": \"Environment issues detected: ${ISSUES}\"}"
  fi
else
  exit 0
fi
