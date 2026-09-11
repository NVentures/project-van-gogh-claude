---
name: kpi-scorecard
description: >-
  Show the weekly scorecard on demand: how many threads are waiting on you, how
  fast things close, what got filed, and which measures are on target. Reads the
  counts Van Gogh has been recording as it works, and renders the same email
  that arrives every week. Use when the user types /van-gogh:kpi-scorecard or
  asks how Van Gogh has been doing, how their week went by the numbers, or to
  see the scorecard early.
allowed-tools:
  - Bash("$HOME/.config/van-gogh/venv/bin/python" *)
  - Bash(& "$HOME\.config\van-gogh\venv\Scripts\python.exe" *)
  - Bash(cd $env:CLAUDE_PLUGIN_ROOT)
---

# The scorecard

Six measures, counted from what Van Gogh already did. Nothing is asked of the
reader and nothing new is fetched: the numbers come from a log the briefings
write as they run.

**How this reads.** The person reading it runs a business. Short sentences,
lead with the number, no vocabulary from the data model. The full rules are the
"Written Voice" section of `DESIGN.md`.

---

## Python runtime

**Python runtime:** before the first script invocation, run the ensure-venv
guard in [`_shared/python-runtime.md`](../_shared/python-runtime.md).

---

## Run it

macOS / Linux (bash/zsh):
```bash
"$HOME/.config/van-gogh/venv/bin/python" "${CLAUDE_PLUGIN_ROOT}/app/kpi_report.py" --json
```

Windows (PowerShell):
```powershell
& "$HOME\.config\van-gogh\venv\Scripts\python.exe" "$env:CLAUDE_PLUGIN_ROOT\app\kpi_report.py" --json
```

To also write the email to a file the user can open, add
`--html "<path>/scorecard.html"`.

---

## What comes back

- `kpis`: six rows, each with `name`, `value_text`, `direction_text`,
  `prior_text`, `target_text`, `met`, `n_text`, `detail`, and `measured`.
- `opening`: the summary sentences, already chosen by code.
- `window_start` and `window_end`: the fortnight it counted.
- `measured_count`: how many of the six had enough behind them.
- `malformed_events`: unreadable log lines that were skipped, usually zero.

---

## Render it

Print the `opening` lines first, verbatim. Then one line per KPI:

- Measured: the name, the `value_text`, the movement (`direction_text` plus
  `prior_text` when both are present), and `Met.` or `Not yet.` from `met`.
- Not measured: the name and the `reason`, in the reason's own words.

Do not compute anything. Do not re-rank the rows, re-word a target, or turn a
"not enough yet" into a zero. Every number and every verdict is already decided
by code, and a KPI that says it has too little behind it is giving the honest
answer.

Close with one line saying the fortnight it covered. If `--html` was used, say
where the file is.

When `measured_count` is zero, say so plainly in one sentence: Van Gogh has not
been running long enough to score anything yet, and the numbers start once a
full fortnight of briefings is behind it.

---

## The weekly email

This is the same content that arrives by email when the scorecard is switched
on. It ships off; `/van-gogh:update-settings` turns it on, sets the day and
time, and can point it at a different address from the briefings. The first
email waits for a full fortnight rather than sending a page of empty cards.
