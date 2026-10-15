#!/bin/bash
# Van Gogh first-run preparation under Codex, started in the background by
# setup_notice.sh --codex when the plugin was installed from the Codex app and
# nothing has prepared this computer. Runs setup.sh --from-hook (the Terminal
# install line's prepare-only half) and leaves three files the hook and the
# install skill read: bootstrap.pid while it runs, bootstrap.out (what setup
# printed) and bootstrap.rc (its exit code) when it is done.
S="$1"
ROOT="$2"
B="$S/setup"
mkdir -p "$B" || exit 1
printf '%s\n' "$$" > "$B/bootstrap.pid"
sh "$ROOT/setup.sh" --host codex --from-hook > "$B/bootstrap.out" 2>&1 </dev/null
RC=$?
printf '%s\n' "$RC" > "$B/bootstrap.rc"
rm -f "$B/bootstrap.pid"
exit "$RC"
