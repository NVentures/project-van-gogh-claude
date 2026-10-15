#!/bin/sh
# Van Gogh readiness check (Mac). Runs before anything is installed:
#   curl -fsSL https://raw.githubusercontent.com/NVentures/project-van-gogh-claude/main/check.sh | sh
# Prints READY when every check passes, otherwise one plain line per missing
# item. Self-contained on purpose: this file is served from the repo root and
# must work with no plugin, no Python and no state dir present.
# Every command reads stdin from /dev/null: under "curl | sh" the script
# itself IS stdin, and a child that reads it would swallow the rest of it.
set -u
missing=0
miss() { missing=$((missing + 1)); printf 'MISSING: %s\n' "$1"; }
ok() { printf 'ok: %s\n' "$1"; }

# Which client: --host claude|codex, else VAN_GOGH_HOST, else Claude.
HOST="${VAN_GOGH_HOST:-claude}"
NEXT=""
for arg in "$@"; do
  if [ "$NEXT" = host ]; then HOST="$arg"; NEXT=""; continue; fi
  case "$arg" in --host) NEXT=host ;; --host=*) HOST="${arg#--host=}" ;; esac
done

# 1. The client's command, present and signed in.
if [ "$HOST" = codex ]; then
  codex_bin=""
  for c in "$(command -v codex 2>/dev/null || true)" "$HOME/.codex/packages/standalone/current/bin/codex" "/Applications/ChatGPT.app/Contents/Resources/codex" /Applications/*.app/Contents/Resources/codex "$HOME"/Applications/*.app/Contents/Resources/codex; do
    if [ -n "$c" ] && [ -x "$c" ]; then codex_bin="$c"; break; fi
  done
  if [ -n "$codex_bin" ]; then
    # A status line, not a real call: enough for a readiness check, and the
    # install's first real call is what proves the sign-in works.
    if "$codex_bin" login status </dev/null 2>&1 | grep -q 'Logged in'; then
      ok "Codex is installed and signed in"
    else
      miss "Codex is installed but not signed in. Open Terminal, type codex login, press Return and follow the sign-in."
    fi
  else
    miss "The Codex command is not installed. Install it with OpenAI's official installer: paste this line into Terminal and press Return: curl -fsSL https://chatgpt.com/codex/install.sh | sh   Then open a new Terminal window and run this check again."
  fi
elif command -v claude >/dev/null 2>&1; then
  if claude auth status --json </dev/null 2>/dev/null | grep -q '"loggedIn": *true'; then
    ok "Claude is installed and signed in"
  else
    miss "Claude is installed but not signed in. Open Terminal, type claude, press Return and follow the sign-in."
  fi
elif [ -x "$HOME/.local/bin/claude" ]; then
  # The installer's own location (code.claude.com/docs/en/troubleshoot-install,
  # "Verify your PATH"). Installed there but not on this window's PATH is a
  # different fault from not installed, and the fix is the documented line:
  # ~/.zshrc for zsh (the Mac default), ~/.bash_profile for Bash on a Mac.
  case "${SHELL:-}" in */bash) rc="~/.bash_profile" ;; *) rc="~/.zshrc" ;; esac
  miss "Claude is installed but this window cannot find it yet. Paste this line into Terminal and press Return: echo 'export PATH=\"\$HOME/.local/bin:\$PATH\"' >> $rc   Then open a new window."
else
  miss "The Claude command is not installed. Install Claude with Anthropic's official installer: paste this line into Terminal and press Return: curl -fsSL https://claude.ai/install.sh | bash   Then open a new Terminal window and run this check again."
fi

# 2. git and Apple's developer tools (the stub git in /usr/bin pops a dialog
#    until the tools are installed, so ask xcode-select first).
if command -v xcode-select >/dev/null 2>&1 && xcode-select -p </dev/null >/dev/null 2>&1; then
  ok "developer tools are installed"
elif [ "$(command -v git 2>/dev/null)" != "" ] && [ "$(command -v git)" != "/usr/bin/git" ] \
    && git --version </dev/null >/dev/null 2>&1; then
  ok "git is installed"
else
  miss "Apple's developer tools are not installed. Open Terminal, type xcode-select --install, press Return and click Install (about five minutes)."
fi

# 3. The key file, in Downloads, Desktop, the current folder or the private
#    folder a previous attempt moved it to. Renamed variants count.
state="${VAN_GOGH_STATE_DIR:-$HOME/.config/van-gogh}"
found=""
for dir in "$HOME/Downloads" "$HOME/Desktop" "." "$state/keyfile"; do
  for f in "$dir"/van-gogh-key*.json; do
    [ -f "$f" ] && found="$f"
  done
done
if [ -n "$found" ]; then
  ok "key file found ($found)"
else
  miss "No key file found. Save the van-gogh-key.json file you were sent into your Downloads folder (if your Mac asks to allow Terminal to read Downloads, click Allow)."
fi

# 4. The five hosts the install talks to, over HTTPS, five seconds each.
#    Any answer counts as reachable; a timeout or a refused connection is the
#    shape of a corporate proxy or firewall.
hosts="github.com astral.sh pypi.org accounts.google.com login.microsoftonline.com"
[ "$HOST" = codex ] && hosts="$hosts chatgpt.com"
for host in $hosts; do
  if curl -sS -o /dev/null --max-time 5 "https://$host/" </dev/null 2>/dev/null; then
    ok "$host is reachable"
  else
    miss "Cannot reach $host over HTTPS. A corporate proxy or firewall is probably blocking it: ask IT to allow $host, or try from a home network."
  fi
done

if [ "$missing" -eq 0 ]; then
  printf 'READY\n'
  exit 0
fi
printf 'NOT READY: %s item(s) to fix, listed above.\n' "$missing"
exit 1
