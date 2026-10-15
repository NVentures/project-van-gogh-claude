#!/bin/bash
# Van Gogh session-start hook: one line while an install is unfinished.
#
# Gated on the checklist file existing. Exit 0 with nothing on stdout or
# stderr when it is absent, or inside one of Van Gogh's own unattended runs.
# When it is present: print {"systemMessage": "<line>"}, the same line
# `setup_state.one_line()` builds, and start the nudge job detached so a
# client who opens Claude is nudged even if the daily job never fired.
# Budget: under one second. No Python unless the checklist exists.

[ -n "$VAN_GOGH_UNATTENDED" ] && exit 0
[ -n "$VAN_GOGH_SCHEDULED" ] && exit 0

# --codex: the Codex form of the same hook (hooks/codex-hooks.json). Codex
# hands whatever a SessionStart hook prints to the model as a developer
# message, so the line goes out plain, spelled the way Codex names a skill.
HOST_WORD="Claude"
INSTALL_CMD="/van-gogh:install-van-gogh"
PLAIN=""
STATE_EARLY="${VAN_GOGH_STATE_DIR:-$HOME/.config/van-gogh}"
# --first-chat (after --codex): the same first-run logic run by a chat whose
# hook never ran (Codex did not ask to trust it). Setup runs in the
# foreground so Codex can ask the person to approve it, and the one line
# printed is the whole answer the chat relays.
FIRST_CHAT=""
[ "${2:-}" = "--first-chat" ] && FIRST_CHAT=1
if [ "${1:-}" = "--codex" ]; then
  PLAIN=1
  export VAN_GOGH_HOST=codex
elif [ -z "${VAN_GOGH_HOST:-}" ] && [ -f "$STATE_EARLY/host" ]; then
  VAN_GOGH_HOST="$(tr -d '[:space:]' < "$STATE_EARLY/host" 2>/dev/null || true)"
  export VAN_GOGH_HOST
fi
if [ "${VAN_GOGH_HOST:-}" = "codex" ]; then
  HOST_WORD="Codex"
  INSTALL_CMD='$van-gogh:install-van-gogh'
fi

STATE="${VAN_GOGH_STATE_DIR:-$HOME/.config/van-gogh}"
CHECK="$STATE/setup/checklist.json"

# First run under Codex with the plugin installed from the Codex app's plugin
# screen: no install line has prepared this computer, so the hook (which runs
# outside the model's sandbox) starts the same preparation in the background:
# setup.sh --from-hook, i.e. the Terminal install line's prepare-only half.
# Only on a computer with nothing on it (no host record, no venv, no
# checklist; an install before 0.89.0 has a venv and no host record and is
# never touched), or one this preparation started and did not finish.
if [ -n "$PLAIN" ]; then
  B="$STATE/setup"
  HOSTREC=""
  [ -f "$STATE/host" ] && HOSTREC="$(tr -d '[:space:]' < "$STATE/host" 2>/dev/null || true)"
  VENV_OK=""
  { [ -x "$STATE/venv/bin/python" ] || [ -x "$STATE/venv/Scripts/python.exe" ]; } && VENV_OK=1
  PREP=""
  [ -z "$HOSTREC" ] && [ -z "$VENV_OK" ] && [ ! -f "$CHECK" ] && PREP=1
  [ "$HOSTREC" = codex ] && [ -z "$VENV_OK" ] && PREP=1
  READY="Van Gogh is ready on this computer. Open a new chat and type $INSTALL_CMD (Codex reads Van Gogh's settings only when a chat starts)."
  if [ -n "$FIRST_CHAT" ] && [ -z "$PREP" ]; then
    if [ -n "$VENV_OK" ] && [ "$HOSTREC" != codex ]; then
      printf '%s\n' "This computer already runs Van Gogh under Claude, so nothing was changed. To move it to Codex, paste the README's Codex install line in Terminal (PowerShell on Windows)."
    else
      printf '%s\n' "$READY"
    fi
    exit 0
  fi
  if [ -n "$PREP" ]; then
    WHEN="When it is done, open a new chat and type $INSTALL_CMD (Codex reads Van Gogh's settings only when a chat starts)."
    BPID="$(cat "$B/bootstrap.pid" 2>/dev/null || true)"
    if [ -n "$BPID" ] && kill -0 "$BPID" 2>/dev/null; then
      printf '%s\n' "Van Gogh is still getting this computer ready (Python and its settings, a minute or two). $WHEN"
      exit 0
    fi
    BROOT="${CLAUDE_PLUGIN_ROOT:-$(cd "$(dirname "$0")/.." 2>/dev/null && pwd)}"
    [ -f "$BROOT/setup.sh" ] && [ -f "$BROOT/hooks/codex_bootstrap.sh" ] || exit 0
    LAST=""
    if [ -f "$B/bootstrap.rc" ] && [ "$(cat "$B/bootstrap.rc" 2>/dev/null)" != 0 ]; then
      LAST="$(grep -v '^[[:space:]]*$' "$B/bootstrap.out" 2>/dev/null | tail -n 1)"
    fi
    mkdir -p "$B" 2>/dev/null
    rm -f "$B/bootstrap.rc"
    if [ -n "$FIRST_CHAT" ]; then
      if bash "$BROOT/hooks/codex_bootstrap.sh" "$STATE" "$BROOT" >/dev/null 2>&1 </dev/null; then
        printf '%s\n' "$READY"
      else
        LAST="$(grep -v '^[[:space:]]*$' "$B/bootstrap.out" 2>/dev/null | tail -n 1)"
        printf '%s\n' "Van Gogh could not get this computer ready: ${LAST:-no reason was printed}. Open a new chat and type $INSTALL_CMD to try again."
      fi
      exit 0
    fi
    ( nohup bash "$BROOT/hooks/codex_bootstrap.sh" "$STATE" "$BROOT" >/dev/null 2>&1 </dev/null & ) 2>/dev/null
    MSG="Van Gogh is getting this computer ready in the background (Python and its settings, about two minutes). $WHEN"
    [ -n "$LAST" ] && MSG="Van Gogh's last try at getting ready stopped with: $LAST It is trying again now. $MSG"
    printf '%s\n' "$MSG"
    exit 0
  fi
fi

[ -f "$CHECK" ] || exit 0

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

LINE=""
if [ -n "$PY" ] && [ -f "$ROOT/app/setup_state.py" ]; then
  LINE="$("$PY" "$ROOT/app/setup_state.py" line 2>/dev/null)"
fi

if [ -z "$LINE" ]; then
  # No venv yet, or it did not answer: build the same line in bash from the
  # JSON. The file is written with one key per line, so a line parser is
  # enough: count the boxes whose status is unresolved, and find the first.
  COUNTS="$(awk '
    /"status":/ {
      s = $0; sub(/.*"status": *"/, "", s); sub(/".*/, "", s)
      total++
      if (s != "done" && s != "skipped" && s != "not_applicable") {
        open++
        if (first == "") first = key
      }
    }
    /"key":/ { key = $0; sub(/.*"key": *"/, "", key); sub(/".*/, "", key) }
    END { printf "%d %d %s", open + 0, total + 0, first }
  ' "$CHECK")"
  OPEN="${COUNTS%% *}"
  REST="${COUNTS#* }"
  TOTAL="${REST%% *}"
  FIRST="${REST#* }"
  [ "$OPEN" -gt 0 ] 2>/dev/null || exit 0
  # The same words as setup_state.BOXES; tests/test_setup_notice_hook.py
  # holds the two in step.
  case "$FIRST" in
    plugin) ACTION="install the plugin" ;;
    python) ACTION="set up Python" ;;
    credentials) ACTION="install your key file" ;;
    accounts) ACTION="sign in to your accounts" ;;
    vault) ACTION="confirm your settings" ;;
    jobs) ACTION="schedule your briefings" ;;
    deals) ACTION="tick your real deals" ;;
    meetings) ACTION="file your meetings" ;;
    briefings) ACTION="see your first briefings" ;;
    *) ACTION="finish setting up" ;;
  esac
  case "$FIRST" in
    # No /van-gogh command exists before the plugin and Python are in place.
    plugin|python) LINE="Van Gogh setup: $OPEN of $TOTAL install steps left. Next: paste your checklist's setup line in Terminal (PowerShell on Windows)." ;;
    *) LINE="Van Gogh setup: $OPEN of $TOTAL install steps left. Next: $ACTION. In $HOST_WORD, type $INSTALL_CMD" ;;
  esac
fi

[ -z "$LINE" ] && exit 0

if [ -n "$PLAIN" ]; then
  printf '%s\n' "$LINE"
else
  ESCAPED="$(printf '%s' "$LINE" | sed -e 's/\\/\\\\/g' -e 's/"/\\"/g')"
  printf '{"systemMessage": "%s"}\n' "$ESCAPED"
fi

# The nudge, detached, with its own stdio: the hook must return at once and
# a session start must never wait on a token refresh.
if [ -n "$PY" ] && [ -f "$ROOT/app/setup_nudge.py" ]; then
  if [ -n "$VAN_GOGH_SETUP_LOGS" ]; then
    LOGS="$VAN_GOGH_SETUP_LOGS"
  elif [ -n "$LOCALAPPDATA" ] && [ -d "$LOCALAPPDATA" ]; then
    LOGS="$LOCALAPPDATA/VanGogh/logs"
  else
    LOGS="$HOME/Library/Logs/VanGogh"
  fi
  mkdir -p "$LOGS" 2>/dev/null
  ( nohup "$PY" "$ROOT/app/setup_nudge.py" run --maybe \
      >>"$LOGS/setup-nudge.log" 2>>"$LOGS/setup-nudge.err" </dev/null & ) 2>/dev/null
fi
exit 0
