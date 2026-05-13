#!/bin/bash
# Post-edit hook: auto-format Python files after Edit/Write
# Receives JSON on stdin with tool_input.file_path
# Dependencies: jq (graceful fallback), black (optional), ruff (optional)

INPUT=$(cat)

# Extract file path — use jq if available, fallback to grep
if command -v jq &>/dev/null; then
  FILE_PATH=$(echo "$INPUT" | jq -r '.tool_input.file_path // empty')
else
  FILE_PATH=$(echo "$INPUT" | grep -o '"file_path"[[:space:]]*:[[:space:]]*"[^"]*"' | head -1 | sed 's/.*"file_path"[[:space:]]*:[[:space:]]*"//;s/"$//')
fi

if [ -z "$FILE_PATH" ]; then
  exit 0
fi

# Only process Python files
if [[ "$FILE_PATH" != *.py ]]; then
  exit 0
fi

# Auto-format with black (if available)
if command -v black &>/dev/null; then
  black -q -l 100 "$FILE_PATH" 2>/dev/null
fi

# Auto-fix with ruff (if available)
if command -v ruff &>/dev/null; then
  ruff check --fix --quiet "$FILE_PATH" 2>/dev/null

  # Run ruff check (lint only) to surface remaining issues
  LINT_OUTPUT=$(ruff check "$FILE_PATH" 2>&1)
  if [ $? -ne 0 ]; then
    if command -v jq &>/dev/null; then
      ESCAPED=$(echo "$LINT_OUTPUT" | head -20 | jq -Rs .)
      echo "{\"continue\": true, \"systemMessage\": \"Lint issues after formatting:\\n${ESCAPED}\"}" | jq -c '.'
    else
      echo "{\"continue\": true, \"systemMessage\": \"Lint issues found after formatting. Run: ruff check ${FILE_PATH}\"}"
    fi
    exit 0
  fi
fi

exit 0
