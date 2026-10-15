# How to run every verify step (for the evaluator and the proof collector)

Project root for every command: the `project-van-gogh/` folder of this
checkout (branch `nobeljchang/Orchestrator`). Base pin and target version are
in the header of `punchlist.md`.

This checkout has no `.venv` of its own. Two interpreters are used:

- **Tests and scans:** `~/Documents/Van Gogh Full Repo/project-van-gogh/.venv/bin/python`
  (the sibling checkout's dev venv, Python 3.14, written `$PY` below). It is
  not the managed venv, so nothing it runs rewrites the vault or the pointer.
- **Anything a user would run:** `~/.config/van-gogh/venv/bin/python`, the
  managed venv every skill and agent file names. `live_check.py` uses it for
  `app/vincent.py` and for the Workbench server.

Hermetic suite:

```
PYTHONDONTWRITEBYTECODE=1 "$PY" -m pytest -q -p no:cacheprovider -rs
```

Baseline at the pin: 3686 passed, 5 skipped. The skipped set is in the access
audit and in `evidence/baseline-pytest.txt`.

## The live harness

`objectives/vincent/live_check.py <check>` runs one live check through the
real entry point and its control, and writes transcripts to
`~/Downloads/proof-vincent/live/` (outside the repository on purpose: live
runs quote the real vault). `summary.json` there holds every assertion.

```
"$PY" objectives/vincent/live_check.py catalog schedule readonly sentinel
"$PY" objectives/vincent/live_check.py memory confirm route plant status
"$PY" objectives/vincent/live_check.py nosend workbench opening
"$PY" objectives/vincent/live_check.py dispatch        # runs a real meeting prep, several minutes
```

Each agent turn is `claude -p <prompt> --agent vincent --plugin-dir <root>
--output-format stream-json --verbose`, run from the vault. What Vincent said
and which tools he called are read from the CLI's own event stream, never
from his account of it. Use the harness or run the same commands by hand; the
`COMMAND:` line of each transcript is the exact argv.

Four things to know before running anything live:

1. **The plugin-root pointer.** The agent file finds `app/vincent.py` through
   `~/.config/van-gogh/plugin-root`. On this Mac that pointer names the
   sibling checkout, which has no Vincent, and the installed jobs rewrite it
   on every run (the watcher fires every 30 minutes). The harness points it
   here for the length of a check, confirms it is still here after each
   turn, re-runs the turn once if a scheduled job moved it, and restores it.
   A hand-run turn that prints "No such file" hit this: set the pointer and
   run it again.
2. **`CLAUDE_PLUGIN_ROOT` is empty inside an agent session** (probed from the
   tool result of a real run). That is why the agent file uses the pointer.
3. **Two checks need a job to have failed** (`workbench`, `opening`). They
   run in a scratch world: a throwaway state folder and vault with a planted
   ledger, reached through `VAN_GOGH_STATE_DIR`, with the real OS scheduler.
   A failure planted in the real ledger would make the real watcher restart
   a real job, and today's real successful rows would mask it anyway.
4. **The owner's own global instructions load into every session on this Mac.**
   A transcript may show a `Skill` call to `task-observer` or a closing
   status sentence. Neither is Vincent's; grade the hand-off line and the
   tool calls that touch Van Gogh.

## Item by item

| Item | Command or check |
|---|---|
| P1 | `"$PY" -m pytest tests/test_vincent.py -q -k "catalog or plugin or description or frontmatter or shape or installed"`; live: `live_check.py catalog` (each kind's count against an independent count on disk, all above zero) |
| P2 | `pytest tests/test_vincent.py -q -k two_skills_with_one_name` |
| P3 | `pytest tests/test_vincent.py -q -k added_a_moment_ago` |
| P4 | `pytest tests/test_vincent.py -q -k "one_row or rendered or stamp"`; live: `live_check.py schedule` (17 rows; the Morning Coffee row against the ledger tail and `launchctl`) |
| P5 | `pytest tests/test_vincent.py -q -k "starts_nothing or watcher_itself_act or is_a_copy"` (the second is the mutation proof: the same patches stop the real tick at the restart); live: `live_check.py readonly` |
| P6 | `pytest tests/test_vincent.py -q -k "leads_with or quiet_day or cannot_be_read or needs_the_reader or no_ledger"` |
| P7 | `pytest tests/test_vincent.py -q -k "memory_carries or secret_in_memory or missing_memory"`; live: `"$HOME/.config/van-gogh/venv/bin/python" app/vincent.py --situation` and grep one line of the real `MEMORY.md` under `~/.claude/projects/<vault slug>/memory/` |
| P8 | `pytest tests/test_vincent.py -q -k "remembering or same_fact or cleaned"`; live: first two assertions of `live_check.py memory` |
| P9 | `live_check.py memory` (the fact is written, asked about, removed, asked about again; the page is restored byte for byte) |
| P10 | `pytest tests/test_vincent.py -q -k "cli_can_load or old_name or exactly_one"`; `grep -rn "chief-of-staff\.md\|name: chief-of-staff" agents app skills tests README.md FOUR-CS.md CLAUDE.md \| wc -l` prints 0; voice: P25 |
| P11 | `live_check.py sentinel` (a sentinel planted in a copy of the agent file comes back with `--agent vincent` and not without) |
| P12 | `pytest tests/test_vincent.py tests/test_plugin_manifest.py tests/test_four_cs.py tests/test_personal_skill_surface.py -q` (the last forks every shipped skill, Vincent included) |
| P13 | `pytest tests/test_vincent.py -q -k "hand_off or markup or names_nothing"` |
| P14 | `pytest tests/test_vincent.py -q -k "change_things or personal_copy or needs_a_yes"`; live: `live_check.py confirm`; control: the `route-next-call` and `dispatch` turns hand off with no question |
| P15 | `live_check.py route` (four transcripts, graded on the line code reads; the mechanic leg also lists `logs/repairs/`) |
| P16 | `live_check.py plant` |
| P17 | `live_check.py dispatch`. Evidence is the files meeting prep writes (`van-gogh/meeting-prep.md`, `van-gogh/logs/meeting_prep*`); a hand-run prep writes no ledger row. The control removes `Skill` from `--tools` |
| P18 | `live_check.py status`. Read the access audit note on this item first |
| P19 | `pytest tests/test_vincent.py -q -k "fails_is_never or printed_nothing"` |
| P20 | `pytest tests/test_vincent.py -q -k "can_send or never_send or argv or not_on_the_list"`; live: `live_check.py nosend` (waits 60 seconds between counts) |
| P21 | `pytest tests/test_vincent.py tests/test_page_actions.py -q -k "chat or conversation or model or resumed or read_only"`; live: `live_check.py workbench` (HTTP against this checkout's server in a scratch world) |
| P22 | `live_check.py opening` |
| P23 | `pytest tests/test_four_cs.py tests/test_plugin_manifest.py -q`; `grep -n "vincent" README.md FOUR-CS.md CLAUDE.md vault/CLAUDE.md CHANGELOG.md`; `grep -n version pyproject.toml .claude-plugin/plugin.json` |
| P24 | the full suite, twice; compare the `SKIPPED` lines with `evidence/baseline-pytest.txt` |
| P25 | `"$PY" objectives/fifteen-minute-install/scan_dashes.py --selftest`, then `--since <pin>`; `"$PY" objectives/vincent/voice_scan.py --selftest`, then with no argument. Run both after the final commit |
| P26 | `pytest tests/test_safety_denylist.py -q`; the listing: `git diff <pin> -- . ':!objectives' \| grep '^+' \| grep -oE '\b[A-Z][a-z]+ [A-Z][a-z]+\b' \| sort \| uniq -c \| sort -rn` |
| P27 | `pytest tests/test_vincent.py -q -k "huge or out_of_time or turn_that_fails"` |

Every test in `tests/test_vincent.py` is mutation-proved:
`"$PY" objectives/vincent/mutation_check.py` reverts one guard at a time in a
copy of the tree and must print `24 of 24 mutations caught`.
