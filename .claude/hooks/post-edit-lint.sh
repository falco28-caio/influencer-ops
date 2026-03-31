#!/bin/bash
# Post-edit hook: auto-format Python files after Edit/Write
# Receives JSON on stdin with tool_input.file_path

INPUT=$(cat)
FILE_PATH=$(echo "$INPUT" | jq -r '.tool_input.file_path // empty')

if [ -z "$FILE_PATH" ]; then
  exit 0
fi

# Only process Python files
if [[ "$FILE_PATH" != *.py ]]; then
  exit 0
fi

# Auto-format with black and ruff fix
black -q -l 100 "$FILE_PATH" 2>/dev/null
ruff check --fix --quiet "$FILE_PATH" 2>/dev/null

# Run ruff check (lint only, no fix) to surface remaining issues
LINT_OUTPUT=$(ruff check "$FILE_PATH" 2>&1)
if [ $? -ne 0 ]; then
  echo '{"continue": true, "systemMessage": "Lint issues found after formatting:\n'"$(echo "$LINT_OUTPUT" | head -20 | jq -Rs .)"'"}' | jq -c '.'
  exit 0
fi

exit 0
