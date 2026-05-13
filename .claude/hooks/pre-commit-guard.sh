#!/bin/bash
# Pre-commit hook: lint staged files and run tests before git commit
# Receives JSON on stdin with tool_input.command
# Dependencies: jq (graceful fallback), ruff (optional), pytest (optional)

INPUT=$(cat)

# Extract command — use jq if available, fallback to grep
if command -v jq &>/dev/null; then
  COMMAND=$(echo "$INPUT" | jq -r '.tool_input.command // empty')
else
  COMMAND=$(echo "$INPUT" | grep -o '"command"[[:space:]]*:[[:space:]]*"[^"]*"' | head -1 | sed 's/.*"command"[[:space:]]*:[[:space:]]*"//;s/"$//')
fi

# Only intercept git commit commands
if [[ "$COMMAND" != *"git commit"* ]]; then
  exit 0
fi

# Helper: emit deny JSON
deny() {
  local reason="$1"
  if command -v jq &>/dev/null; then
    jq -n --arg reason "$reason" '{
      hookSpecificOutput: {
        hookEventName: "PreToolUse",
        permissionDecision: "deny",
        permissionDecisionReason: $reason
      }
    }'
  else
    echo "{\"hookSpecificOutput\":{\"hookEventName\":\"PreToolUse\",\"permissionDecision\":\"deny\",\"permissionDecisionReason\":\"${reason}\"}}"
  fi
}

# Lint staged Python files (if ruff is available)
STAGED_FILES=$(git diff --cached --name-only --diff-filter=ACM -- '*.py' 2>/dev/null)

if [ -n "$STAGED_FILES" ] && command -v ruff &>/dev/null; then
  LINT_RESULT=$(echo "$STAGED_FILES" | xargs ruff check 2>&1)
  if [ $? -ne 0 ]; then
    deny "Lint errors in staged files:\n$(echo "$LINT_RESULT" | head -15)"
    exit 0
  fi
fi

# Run tests (if pytest + pytest-asyncio are available)
if command -v pytest &>/dev/null && python3 -c "import pytest_asyncio" 2>/dev/null; then
  TEST_RESULT=$(pytest tests/ -x -q --tb=short 2>&1)
  if [ $? -ne 0 ]; then
    deny "Tests must pass before committing:\n$(echo "$TEST_RESULT" | tail -20)"
    exit 0
  fi
fi

exit 0
