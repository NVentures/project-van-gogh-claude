#!/bin/sh
# Van Gogh setup (Mac). Run from the marketplace clone, in Terminal:
#   sh ~/.claude/plugins/marketplaces/van-gogh/setup.sh
# Flags: --prepare-only (stop before opening the client), --check (readiness
# only), --host claude|codex (which client this computer uses; the choice is
# kept in the state dir and every later run reads it), --from-hook (the
# Codex session-start hook preparing a computer whose plugin was installed
# from the Codex app: prepare only, and the in-session refusal does not
# apply, because the hook is not a session the person types into).
#
# Two lanes at once: the plugin install, and the Python environment (an
# existing Python 3.10+ on PATH, else a private uv under the state dir with a
# managed Python). Then app/setup_flow.py prepare, then Claude opens on the
# install skill. No sudo, brew, winget, choco, RunAs or admin dialog anywhere.
# POSIX sh on purpose: the pasted line says "sh", which is dash on Linux.
set -eu

ROOT=$(cd "$(dirname "$0")" && pwd)
S="${VAN_GOGH_STATE_DIR:-$HOME/.config/van-gogh}"
case "$S" in "~"*) S="$HOME${S#"~"}" ;; esac
LOGS="${VAN_GOGH_SETUP_LOGS:-$HOME/Library/Logs/VanGogh}"
LOG="$LOGS/setup-bootstrap.log"
VPY="$S/venv/bin/python"
MODE=run
FROM_HOOK=""
HOST="${VAN_GOGH_HOST:-}"
NEXT=""
for arg in "$@"; do
  if [ "$NEXT" = host ]; then HOST="$arg"; NEXT=""; continue; fi
  case "$arg" in
    --prepare-only) MODE=prepare ;;
    --check) MODE=check ;;
    --from-hook) FROM_HOOK=1; MODE=prepare ;;
    --host) NEXT=host ;;
    --host=*) HOST="${arg#--host=}" ;;
    *) printf 'Unknown option: %s (use --prepare-only, --check, --from-hook or --host claude|codex)\n' "$arg"; exit 2 ;;
  esac
done
# The client: the flag, else the environment, else what an earlier run kept,
# else Claude, which is what every install before 0.89.0 was.
if [ -z "$HOST" ] && [ -f "$S/host" ]; then
  HOST="$(tr -d '[:space:]' < "$S/host" 2>/dev/null || true)"
fi
case "$HOST" in
  claude|codex) ;;
  "") HOST=claude ;;
  *) printf 'Unknown host: %s (claude or codex)\n' "$HOST"; exit 2 ;;
esac
export VAN_GOGH_HOST="$HOST"
HOST_WORD=Claude
[ "$HOST" = codex ] && HOST_WORD=Codex

if [ -z "$FROM_HOOK" ] && { [ -n "${CLAUDECODE:-}" ] || [ -n "${CLAUDE_CODE_ENTRYPOINT:-}" ] || [ -n "${CODEX_THREAD_ID:-}" ]; }; then
  INSIDE=Claude
  [ -n "${CODEX_THREAD_ID:-}" ] && INSIDE=Codex
  printf 'This line goes in the Terminal app, not in %s. Press Command-Space, type Terminal, press Return, then paste it there.\n' "$INSIDE"
  exit 2
fi

if [ "$MODE" = check ]; then
  exec sh "$ROOT/check.sh" --host "$HOST"
fi

# Where a Codex binary lives when it is not on PATH: the standalone
# installer's place, then the ChatGPT app, then any other app that bundles
# one (the standalone Codex app).
find_codex() {
  if [ -n "${VAN_GOGH_CODEX_BIN:-}" ] && [ -x "${VAN_GOGH_CODEX_BIN}" ]; then
    printf '%s\n' "$VAN_GOGH_CODEX_BIN"; return 0
  fi
  p="$(command -v codex 2>/dev/null || true)"
  if [ -n "$p" ]; then printf '%s\n' "$p"; return 0; fi
  for c in "$HOME/.codex/packages/standalone/current/bin/codex" "/Applications/ChatGPT.app/Contents/Resources/codex" /Applications/*.app/Contents/Resources/codex "$HOME"/Applications/*.app/Contents/Resources/codex; do
    if [ -x "$c" ]; then printf '%s\n' "$c"; return 0; fi
  done
  return 1
}

fail() { printf '%s\n' "$1"; printf 'Details are in %s\n' "$LOG"; exit 1; }
log() { printf '%s %s\n' "$(date +%H:%M:%S)" "$1" >> "$LOG"; }

# From the hook or a chat, never take over a computer that already runs Van
# Gogh under Claude: an install with a venv whose host record is missing (every
# install before 0.89.0) or says claude. Moving it is the Terminal line's job.
if [ -n "$FROM_HOOK" ] && [ -x "$VPY" ]; then
  PRIOR=""
  [ -f "$S/host" ] && PRIOR="$(tr -d '[:space:]' < "$S/host" 2>/dev/null || true)"
  if [ "$PRIOR" != codex ]; then
    printf 'This computer already runs Van Gogh under Claude, so nothing was changed. To move it to Codex, paste the README'"'"'s Codex install line in Terminal.\n'
    exit 3
  fi
fi

mkdir -p "$S/setup" "$S/tools" "$LOGS"
printf '\n==== setup.sh %s (host %s) ====\n' "$(date)" "$HOST" >> "$LOG"
# The host record, first: the hooks, the readiness line and every script
# read it, and config.json does not exist until the vault step.
printf '%s\n' "$HOST" > "$S/host"

# The clock starts here. The run id names this attempt in every ledger row.
RUN_ID="$(date +%Y%m%d-%H%M%S)-$(od -An -N2 -tx1 /dev/urandom | tr -d ' \n')"
printf '%s\n' "$RUN_ID" > "$S/setup/run-id"
TS="$(date +%Y-%m-%dT%H:%M:%S%z | sed 's/\(..\)$/:\1/')"
printf '{"ts": "%s", "run_id": "%s", "event": "start", "class": "machine"}\n' "$TS" "$RUN_ID" >> "$S/setup/timing.jsonl"
printf 'Setting up Van Gogh (run %s)...\n' "$RUN_ID"

# ---- lane 1: the plugin ----------------------------------------------------
# Under Codex the marketplace is added here too (a Codex install has no
# marketplace clone to run from). VAN_GOGH_MARKETPLACE names a local checkout
# for a rehearsal; clients get the public repository.
MARKET="${VAN_GOGH_MARKETPLACE:-https://github.com/NVentures/project-van-gogh-claude.git}"
plugin_lane_codex() {
  CODEX="$(find_codex || true)"
  if [ -z "$CODEX" ]; then
    log "lane 1: codex not found, plugin install skipped"
    printf 'The Codex command was not found, so the plugin is not installed yet.\n'
    return 0
  fi
  # The listing names every plugin a marketplace offers; only `installed`
  # true means it is on this machine.
  if "$CODEX" plugin list --json </dev/null 2>>"$LOG" | grep -A6 '"pluginId": *"van-gogh@van-gogh"' | grep -q '"installed": *true'; then
    log "lane 1: plugin already installed (codex)"
    printf 'Plugin already installed.\n'
    return 0
  fi
  "$CODEX" plugin marketplace add "$MARKET" </dev/null >>"$LOG" 2>&1 || log "lane 1: marketplace add returned nonzero (may already exist)"
  if "$CODEX" plugin add van-gogh@van-gogh </dev/null >>"$LOG" 2>&1; then
    log "lane 1: plugin installed (codex)"
    printf 'Plugin installed.\n'
    return 0
  fi
  printf 'The plugin could not be installed. Run this first, then try again: codex plugin marketplace add NVentures/project-van-gogh-claude\n'
  return 1
}

plugin_lane() {
  if [ "$HOST" = codex ]; then plugin_lane_codex; return $?; fi
  if ! command -v claude >/dev/null 2>&1; then
    log "lane 1: claude not on PATH, plugin install skipped"
    printf 'The Claude command was not found, so the plugin is not installed yet.\n'
    return 0
  fi
  if claude plugin list --json </dev/null 2>>"$LOG" | grep -q '"id": *"van-gogh@van-gogh"'; then
    log "lane 1: plugin already installed"
    printf 'Plugin already installed.\n'
    return 0
  fi
  if claude plugin install van-gogh@van-gogh </dev/null >>"$LOG" 2>&1; then
    log "lane 1: plugin installed"
    printf 'Plugin installed.\n'
    return 0
  fi
  printf 'The plugin could not be installed. Run this first, then try again: claude plugin marketplace add https://github.com/NVentures/project-van-gogh-claude.git\n'
  return 1
}

# ---- lane 2: the Python environment ---------------------------------------
venv_ok() {
  [ -x "$VPY" ] && "$VPY" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)' </dev/null 2>/dev/null
}

# A Python 3.10+ already on PATH. Apple's /usr/bin/python3 is a stub that pops
# the developer-tools dialog until those are installed, so it is only asked
# once xcode-select says they are.
find_python() {
  for c in python3 python; do
    p="$(command -v "$c" 2>/dev/null || true)"
    [ -n "$p" ] || continue
    case "$p" in
      /usr/bin/python*) xcode-select -p </dev/null >/dev/null 2>&1 || continue ;;
      *WindowsApps*) continue ;;
    esac
    if "$p" -c 'import sys, venv; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)' </dev/null 2>/dev/null; then
      printf '%s\n' "$p"
      return 0
    fi
  done
  return 1
}

# No usable Python: a private uv under the state dir brings a managed one.
# UV_UNMANAGED_INSTALL puts only the binary where we say and touches no shell
# profile; the managed interpreters and cache stay under the state dir too.
uv_python_venv() {
  UV="$S/tools/uv"
  export UV_PYTHON_INSTALL_DIR="$S/tools/python" UV_PYTHON_BIN_DIR="$S/tools/bin" UV_CACHE_DIR="$S/tools/cache" UV_NO_MODIFY_PATH=1
  if [ ! -x "$UV" ]; then
    printf 'Downloading a private Python installer...\n'
    if ! curl -fsSL --max-time 120 https://astral.sh/uv/install.sh </dev/null 2>>"$LOG" \
        | env UV_UNMANAGED_INSTALL="$S/tools" sh >>"$LOG" 2>&1 || [ ! -x "$UV" ]; then
      fail 'Could not download the Python installer from astral.sh. A corporate proxy may be blocking it: check with the readiness check (setup.sh --check) and run again.'
    fi
  fi
  printf 'Installing Python 3.12 (private copy, a minute or two)...\n'
  "$UV" python install 3.12 </dev/null >>"$LOG" 2>&1 \
    || fail 'Could not download Python 3.12. Check the connection (github.com must be reachable) and run again.'
  rm -rf "$S/venv"
  "$UV" venv "$S/venv" --python 3.12 --seed </dev/null >>"$LOG" 2>&1 \
    || fail 'Could not create the Python environment under the state dir.'
}

python_lane() {
  if venv_ok; then
    log "lane 2: venv present"
  else
    PY="$(find_python || true)"
    if [ -n "$PY" ]; then
      log "lane 2: using $PY"
      rm -rf "$S/venv"
      if ! "$PY" -m venv "$S/venv" </dev/null >>"$LOG" 2>&1; then
        log "lane 2: venv creation failed with $PY, falling back to uv"
        uv_python_venv
      fi
    else
      log "lane 2: no Python 3.10+ on PATH, using uv"
      uv_python_venv
    fi
  fi
  "$VPY" -m pip install -q -r "$ROOT/requirements.txt" </dev/null >>"$LOG" 2>&1 \
    || fail 'Could not install the Python packages (pypi.org must be reachable).'
  "$VPY" -c 'import hashlib, sys; print(hashlib.sha256(open(sys.argv[1], "rb").read()).hexdigest())' \
    "$ROOT/requirements.txt" > "$S/requirements.sha256"
  printf 'Python ready.\n'
}

printf 'Installing the plugin and preparing Python at the same time...\n'
plugin_lane &
PLUGIN_PID=$!
python_lane
PLUGIN_RC=0
wait "$PLUGIN_PID" || PLUGIN_RC=$?
[ "$PLUGIN_RC" -eq 0 ] || fail 'Setup stopped at the plugin step.'

# ---- prepare: the orchestrator ticks the machine boxes ----------------------
if [ -f "$ROOT/app/setup_flow.py" ]; then
  PREP_ERR="$S/setup/prepare.err"
  if ! "$VPY" "$ROOT/app/setup_flow.py" prepare --run-id "$RUN_ID" </dev/null >>"$LOG" 2>"$PREP_ERR"; then
    cat "$PREP_ERR" >>"$LOG" 2>/dev/null
    fail 'Setup could not prepare your install (the key file or plugin step did not finish).'
  fi
  cat "$PREP_ERR" >>"$LOG" 2>/dev/null
  # A line prepare wrote for the person (a refused config block) is said
  # here, once, not buried in the log.
  grep '^van-gogh: ' "$PREP_ERR" 2>/dev/null | sort -u >&2 || true
  printf 'Prepared.\n'
else
  log "prepare: app/setup_flow.py not in this build, skipped"
fi

if [ "$MODE" = prepare ]; then
  printf 'Prepared only, as asked. %s was not opened.\n' "$HOST_WORD"
  exit 0
fi

# ---- launch ------------------------------------------------------------------
# Vincent, the chief of staff, runs setup. He is handed the situation and the
# setup rules in a file, so his first turn fetches nothing and no command he
# runs first needs a permission question. If that file cannot be written, the
# install skill runs the same loop without him.
SETUP_MESSAGE='Set up Van Gogh on this computer.'
BRIEF="$S/setup/vincent-brief.txt"
USE_VINCENT=""
if [ -f "$ROOT/agents/vincent.md" ] && [ -f "$ROOT/app/vincent.py" ]; then
  mkdir -p "$S/setup"
  if "$VPY" "$ROOT/app/vincent.py" --setup-brief </dev/null >"$BRIEF" 2>>"$LOG" && [ -s "$BRIEF" ]; then
    USE_VINCENT=1
  else
    log "launch: the brief for Vincent could not be written, opening the install skill"
  fi
fi

if [ "$HOST" = codex ]; then
  # Codex has no agent flag and no system-prompt file: the brief (which under
  # Codex opens with Vincent's own rules) is read as the first thing done.
  # The state dir is the working directory, so setup's own files are inside
  # the sandbox; a write anywhere else (the vault) asks once (-a on-request).
  if [ -n "$USE_VINCENT" ]; then
    MESSAGE="Read the file $BRIEF in full and follow it. $SETUP_MESSAGE"
  else
    MESSAGE='$van-gogh:install-van-gogh'
  fi
  CODEX="$(find_codex || true)"
  if [ -z "$CODEX" ]; then
    printf 'The Codex command was not found. Install Codex, open a new Terminal window, then paste:\n'
    printf '  codex -C '"'"'%s'"'"' -s workspace-write -a on-request -c sandbox_workspace_write.network_access=true "%s"\n' "$S" "$MESSAGE"
    exit 0
  fi
  printf 'Opening Codex...\n'
  exec "$CODEX" -C "$S" -s workspace-write -a on-request -c sandbox_workspace_write.network_access=true "$MESSAGE"
fi

if ! command -v claude >/dev/null 2>&1; then
  printf 'The Claude command was not found. Install Claude Code, open a new Terminal window, then paste:\n'
  if [ -n "$USE_VINCENT" ]; then
    printf '  claude --agent van-gogh:vincent --append-system-prompt-file '"'"'%s'"'"' --add-dir '"'"'%s/setup'"'"' --permission-mode acceptEdits --allowedTools '"'"'Bash(%s/venv/bin/python *)'"'"' '"'"'Agent'"'"' -- "%s"\n' "$BRIEF" "$S" "$S" "$SETUP_MESSAGE"
  else
    printf '  claude --add-dir '"'"'%s/setup'"'"' --permission-mode acceptEdits --allowedTools '"'"'Bash(%s/venv/bin/python *)'"'"' '"'"'Agent'"'"' -- "/van-gogh:install-van-gogh"\n' "$S" "$S"
  fi
  exit 0
fi
printf 'Opening Claude...\n'
# The message comes after `--`: placed straight after --allowedTools it is read
# as one more tool name and Claude opens with nothing asked.
if [ -n "$USE_VINCENT" ]; then
  exec claude --agent van-gogh:vincent --append-system-prompt-file "$BRIEF" --add-dir "$S/setup" --permission-mode acceptEdits --allowedTools "Bash($S/venv/bin/python *)" "Agent" -- "$SETUP_MESSAGE"
fi
exec claude --add-dir "$S/setup" --permission-mode acceptEdits --allowedTools "Bash($S/venv/bin/python *)" "Agent" -- "/van-gogh:install-van-gogh"
