#!/bin/bash
# Van Gogh stop hook: a reply that handed work over carries its hand-off line.
#
# The work is in app/handoff_hook.py. This wrapper keeps the cost at nothing
# for every session that is not Vincent: no Python unless the transcript of
# the turn that is ending holds the hand-off command. Exit 0 with nothing on
# stdout in every other case, including any doubt.

INPUT="$(cat)"
case "$INPUT" in
  *'"stop_hook_active":true'*|*'"stop_hook_active": true'*) exit 0 ;;
esac

T="$(printf '%s' "$INPUT" | sed -n 's/.*"transcript_path"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p' | head -n 1)"
[ -n "$T" ] && [ -f "$T" ] || exit 0
grep -q -- '--handoff' "$T" 2>/dev/null || exit 0

STATE="${VAN_GOGH_STATE_DIR:-$HOME/.config/van-gogh}"
ROOT="${CLAUDE_PLUGIN_ROOT:-}"
if [ -z "$ROOT" ]; then
  ROOT="$(cd "$(dirname "$0")/.." 2>/dev/null && pwd)"
fi

PY=""
if [ -x "$STATE/venv/bin/python" ]; then
  PY="$STATE/venv/bin/python"
elif [ -x "$STATE/venv/Scripts/python.exe" ]; then
  PY="$STATE/venv/Scripts/python.exe"
fi
[ -n "$PY" ] || exit 0

printf '%s' "$INPUT" | "$PY" "$ROOT/app/handoff_hook.py" 2>/dev/null
exit 0
