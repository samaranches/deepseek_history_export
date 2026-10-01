#!/usr/bin/env bash
# Wrapper: all Obsidian export logic lives in deepseek_obsidian_export.py
# (python3, stdlib only: no jq or curl needed). Arguments are passed through
# as-is:
#   ./deepseek_obsidian_export.sh [outdir] [--source PATH] [--limit N]
#                                 [--tags] [--tag-model NAME] [--tag-url URL]
#                                 [--max-kb KB]
# Default source is $DEEPSEEK_CONVERSATIONS_JSON (the dump path changes
# between exports; the user sets the variable before each run), default
# notes directory is $DEEPSEEK_OBSIDIAN_VAULT.
set -euo pipefail
command -v python3 >/dev/null 2>&1 || { echo "ERROR: python3 is required" >&2; exit 1; }
dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
exec python3 "$dir/deepseek_obsidian_export.py" "$@"
