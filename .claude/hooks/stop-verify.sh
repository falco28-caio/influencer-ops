#!/bin/bash
# Stop hook: verify task completion quality
# Checks if there are uncommitted changes that should have been tested

INPUT=$(cat)

# Check for modified Python files that haven't been committed
MODIFIED_PY=$(git diff --name-only -- '*.py' 2>/dev/null)

if [ -n "$MODIFIED_PY" ]; then
  # Check if tests were run in this session (look for pytest cache)
  RECENT_TEST_CACHE=$(find .pytest_cache -maxdepth 1 -mmin -10 2>/dev/null | head -1)

  if [ -z "$RECENT_TEST_CACHE" ]; then
    echo '{"continue": true, "systemMessage": "Python files were modified but tests may not have been run. Consider running `make test` before finishing."}'
    exit 0
  fi
fi

# Check for security-sensitive file changes
SECURITY_FILES=$(git diff --name-only -- 'src/services/guardrail.py' 'src/agents/*.py' 'src/services/drafting.py' 'src/services/autopilot.py' 'src/workers/*.py' 2>/dev/null)

if [ -n "$SECURITY_FILES" ]; then
  echo '{"continue": true, "systemMessage": "Security-sensitive files were modified: '"$(echo "$SECURITY_FILES" | tr '\n' ', ')"'. Consider running /security-audit before finishing."}'
  exit 0
fi

exit 0
