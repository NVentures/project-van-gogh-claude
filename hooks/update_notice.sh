#!/bin/bash
# Van Gogh session-start hook: ask about a newer Van Gogh at the start of a
# chat. Checks live first (plugin_update.py --session-notice: one GET with a
# two-second timeout, skipped when a check ran in the last five minutes), so a
# release published this hour is asked about now. Silent when
# nothing is pending, when there is no venv yet, and in Van Gogh's own
# unattended runs. Claude gets the text as context for the model
# (additionalContext); Codex hands plain stdout to the model as is (--codex).

[ -n "$VAN_GOGH_UNATTENDED" ] && exit 0
[ -n "$VAN_GOGH_SCHEDULED" ] && exit 0

PLAIN=""
if [ "${1:-}" = "--codex" ]; then
  PLAIN=1
  export VAN_GOGH_HOST=codex
fi

STATE="${VAN_GOGH_STATE_DIR:-$HOME/.config/van-gogh}"
PY=""
if [ -x "$STATE/venv/bin/python" ]; then
  PY="$STATE/venv/bin/python"
elif [ -x "$STATE/venv/Scripts/python.exe" ]; then
  PY="$STATE/venv/Scripts/python.exe"
fi
[ -n "$PY" ] || exit 0

ROOT="${CLAUDE_PLUGIN_ROOT:-$(cd "$(dirname "$0")/.." 2>/dev/null && pwd)}"
[ -f "$ROOT/app/plugin_update.py" ] || exit 0

TEXT="$("$PY" "$ROOT/app/plugin_update.py" --session-notice 2>/dev/null)"
[ -n "$TEXT" ] || exit 0

if [ -n "$PLAIN" ]; then
  printf '%s\n' "$TEXT"
else
  ESCAPED="$(printf '%s' "$TEXT" | sed -e 's/\\/\\\\/g' -e 's/"/\\"/g')"
  printf '{"hookSpecificOutput": {"hookEventName": "SessionStart", "additionalContext": "%s"}}\n' "$ESCAPED"
fi
exit 0
