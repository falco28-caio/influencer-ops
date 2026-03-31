#!/bin/bash
# Pre-commit hook: lint-check only staged/changed files, run tests if available
# Receives JSON on stdin with tool_input.command

INPUT=$(cat)
COMMAND=$(echo "$INPUT" | jq -r '.tool_input.command // empty')

# Only intercept git commit commands
if [[ "$COMMAND" != *"git commit"* ]]; then
  exit 0
fi

# Get staged Python files only (not entire codebase)
STAGED_FILES=$(git diff --cached --name-only --diff-filter=ACM -- '*.py' 2>/dev/null)

if [ -n "$STAGED_FILES" ]; then
  # Lint only staged files (if ruff is available)
  if command -v ruff &>/dev/null; then
    LINT_RESULT=$(echo "$STAGED_FILES" | xargs ruff check 2>&1)
    if [ $? -ne 0 ]; then
      jq -n \
        --arg reason "Lint errors in staged files:\n$(echo "$LINT_RESULT" | head -15)" \
        '{
          hookSpecificOutput: {
            hookEventName: "PreToolUse",
            permissionDecision: "deny",
            permissionDecisionReason: $reason
          }
        }'
      exit 0
    fi
  fi
fi

# Run tests only if pytest and dependencies are available
if command -v pytest &>/dev/null && python3 -c "import pytest_asyncio" 2>/dev/null; then
  TEST_RESULT=$(pytest tests/ -x -q --tb=short 2>&1)
  if [ $? -ne 0 ]; then
    jq -n \
      --arg reason "Tests must pass before committing:\n$(echo "$TEST_RESULT" | tail -20)" \
      '{
        hookSpecificOutput: {
          hookEventName: "PreToolUse",
          permissionDecision: "deny",
          permissionDecisionReason: $reason
        }
      }'
    exit 0
  fi
fi

exit 0
