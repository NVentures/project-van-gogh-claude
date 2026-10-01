# Punch List: Unattended MCP connector fetch and the QuickBooks finance brief

Created: 2026-09-10
Status: in-progress
Contract-SHA256: 8e265c0f847d62761a42fa437ce2081678bd021ffcbdc561ce44f53401cc2fb0
Source plan: ~/.claude/plans/make-what-we-discussed-rosy-sedgewick.md
Approval: Nobel approved the plan and said "I've pulled the latest version of GH. Build" (2026-09-10).

Scope: `project-van-gogh/` only. New modules `app/connector_fetch.py`,
`app/finance_brief.py`, `app/finance_html.py`, `app/finance_send.py`. New
`skills/finance-brief/SKILL.md`, `skills/_shared/connectors.md`. Changes to
`app/failure_class.py`, `app/job_watch.py`, `app/scheduler_setup.py`,
`app/config_loader.py`, `app/data_sources.py`, `config.template.json`,
`agents/bookkeeper-controller.md`, `skills/install-van-gogh/SKILL.md`,
`skills/update-settings/SKILL.md`, `CLAUDE.md`, `README.md`, `PRODUCT.md`,
`CHANGELOG.md`, `pyproject.toml`, `.claude-plugin/plugin.json`, and tests.

Red-team pass ran before approval (fresh Plan agent, objective plus draft
criteria only, no design context). It found the failure class the draft would
have shipped: every criterion could pass while the Monday brief silently never
arrives, because the connector token dies about an hour after a human touches
it and nothing demanded a fetch 24 hours later. Five more findings changed the
contract. Argument blindness (a fiscal-year-to-date P&L rendered under a
"month to date" label passes every plumbing check) forced P2 and P13. No realm
pinning for a multi-company owner forced P14. Basis and cash definition
unstated forced P13 and P15. Financial data persisting in CLI transcripts,
proof artifacts and Slack forced P8, P24 and P42. A write-tool denylist that
misses send, void, email and refund forced the positive-allowlist rule in P7.
Deltas computed against an on-demand run forced the weekly-baseline rule in P21.

## Decisions taken at interview

- Token: re-authorize, then prove viability at T+90 minutes and T+24 hours with
  no human touch in between. If the 24 hour check fails, the feature ships as
  on-demand plus a reconnect reminder email, and the docs say so plainly.
- Content: cash, AR aging, AP aging, P&L month to date beside the prior full
  month. Every number carries its as-of date, its basis, and a direction word.
- Delivery: weekly Monday 07:00 email through the existing digest sender, a
  vault copy, and an on-demand skill. Ships off; the install wizard asks.
- Voice: code renders every number; 3 to 5 sentences of bookkeeper-controller
  commentary underneath, adding no numbers, enforced on the live output.
- Email is live-tested here after a Gmail sender is connected.
- Real figures in the proof folder, redacted in the Slack receipt, CLI
  transcripts deleted after every fetch.
- five-fifteen's Slack, Teams and HubSpot stay out of scope; the mechanism is
  generic and proven live against a second server, with a how-to for authors.
- One QuickBooks company, pinned by id in config.

## Out of scope

- five-fifteen unattended fetch (Slack, Teams, HubSpot); Google Drive; Meridian.
- Any QuickBooks write tool: payroll, lending, money onboarding, invoicing.
- Multi-company briefs; any cadence other than weekly plus on demand.
- Publishing to the public repo (`/base-publish` is Nobel's call afterwards).
- Fixing this machine's stale 0.27.0 marketplace install (reported, not fixed).
- Moving vault logs out of `~/Documents` (pre-existing condition).
- A live Windows run (unit coverage only).

## Contract

### A. The shared connector fetch

- [ ] P1. `connector_fetch.fetch` spawns the CLI with argv containing exactly: `-p`, `--output-format stream-json`, `--verbose`, `--mcp-config` followed by an empty non-strict config, `--tools ""`, `--allowedTools` followed by every requested tool as a full `mcp__<server>__<tool>` name, `--permission-prompts none`, `--disable-slash-commands`, `--model <HAIKU_MODEL>`; never `--strict-mcp-config`, never `--permission-mode`; prompt on stdin with encoding utf-8; env drops `ANTHROPIC_API_KEY`, sets `MCP_TIMEOUT` (60000 unless already set) and `VAN_GOGH_UNATTENDED=1`; cwd a private 0700 temp dir; `creationflags=NO_WINDOW`; `claude_update.ensure_current` called before the spawn. | verify: `tests/test_connector_fetch.py` with stubbed `subprocess.run` AND stubbed `claude_bin`, plus `tests/test_no_console_window.py` and `tests/test_claude_cli_invocation.py` with the module in the exempt set and a comment saying why. | proof: test output.
- [ ] P2. Code owns the arguments and the verdict: the prompt is built from a per-tool argument table, and `ok` is true only when every requested tool has exactly one `tool_use` whose `input` equals its table entry AND one non-error `tool_result`. A duplicate call, a call with different arguments, a call outside the list, or a final text with no `tool_use` each yield not-ok with a named reason. | verify: stream fixtures including the captured NOT_READY and token-expired streams and a synthetic fiscal-year-to-date-instead-of-month-to-date stream; mutation: delete the input-equality check and the year-to-date test must fail. | proof: test output plus mutation log.
- [ ] P3. Raw results verbatim: `fetch` returns each `tool_result` content byte for byte, and the model's final text is never parsed for data. | verify: fixture equality test; mutation: return the model text instead and the test must fail. | proof: test output.
- [ ] P4. The connect race is closed and measured: 10 live fetches with the wait flag report init status `connected` and carry a tool_result in 10 of 10; the control, 10 fetches identical but without `--mcp-config`, shows `pending` or no tool_result in at least 1 of 10, proving the flag is load-bearing. When init still reports pending or absent, the fetch sleeps 15 seconds and retries once, then fails NEEDS-AUTH or ABSENT. | verify: `race_measure.py` output with both arms; unit test for the retry path. | proof: counts table.
- [ ] P5. Failure class `connector` exists and is tested first: `failure_class.classify` matches the literal token `CONNECTOR-UNAVAILABLE:` and the CLI phrasings (`requires re-authorization`, `needs-auth`, `unavailable until authorized`); the class is in `NO_RETRY`; `REMEDY` names the fix; `job_watch.actionable` holds such a row with a sentence starting with what the reader must do, recognised by `_NEEDS_YOU`; `_escalate` never spawns the mechanic for it. ABSENT (server missing from init) and NEEDS-AUTH (present, token expired) carry distinct messages and remedies. Near misses stay where they were: Google `401 Unauthorized` and `invalid_grant` stay `auth`, `usage limit exceeded` stays `quota`, `Connection reset by peer` stays `network`, and a QuickBooks-side tool error prefixed `CONNECTOR-ERROR:` stays `unknown` and retries. | verify: `tests/test_failure_class.py`, `tests/test_job_watch.py`, `tests/test_self_anneal.py`; LIVE: `finance_brief.py --json` against the real expired token exits 1 and writes a run-ledger row whose error_class is `connector`. | proof: ledger row and stderr capture.
- [ ] P6. Confinement proven live: a session carrying the P1 flags with only `company_info` allowlisted, asked to call a second read tool, run a shell command, and write a file, reports denied for the first and absent for the other two, and the file does not exist afterwards. Static: every string in any allowlist constant under `app/` matches `^mcp__[A-Za-z0-9_]+__[a-z0-9_]+$`. | verify: `confinement_probe.py` transcript and the static test. | proof: transcript.
- [ ] P7. The allowlist is read-only by construction: `finance_brief.QUICKBOOKS_TOOLS` is a frozen tuple whose every name matches `^(company_info|qbo_accounting_get_[a-z_]+|profit_loss_quickbooks_account)$` and none matches `(create|update|delete|submit|save|assign|duplicate|send|void|email|apply|refund|transfer|deposit|upload|attach|payment)`. Mutation: appending `qbo_sales_create_invoice`, and separately `qbo_sales_send_invoice_email`, each fail the test. | verify: `tests/test_finance_brief.py`. | proof: mutation log.
- [ ] P8. Transcript hygiene: `fetch` deletes the headless session's transcript after parsing, best effort, and reports whether it managed to. Live: the transcript file exists before cleanup and is gone after `fetch` returns. | verify: live listing before and after; unit test with a fake projects dir. | proof: listings.
- [ ] P9. Cost and duration: across the P4 treatment runs, p95 duration is under 3 minutes and p95 `total_cost_usd` is under $0.25 per fetch, both recorded in the sidecar under `meta.fetch`. | verify: table stating n and p95. | proof: table.
- [ ] P10. Generality on a second server needing no auth: `fetch` against `context7` with `resolve-library-id` allowlisted returns a non-error result; the control, server name `nonexistent`, fails ABSENT. | verify: live run and unit test. | proof: outputs.

### B. The finance brief

- [ ] P11. Tool set and arguments: exactly company_info; AR aging summary as of today; AR aging detail for the top overdue names; AP aging summary as of today; balance sheet as of today on an explicit basis; P&L month to date with explicit start, end and accounting method; P&L for the prior full month with the same. Every argument lives in the code table P2 verifies. | verify: test asserting the seven entries and their argument shapes. | proof: test output.
- [ ] P12. `--json` emits: `company{name,id}`, `as_of`, `basis`, `currency`, `cash{total,accounts[{name,balance}],credit_cards_total}`, `ar{total,current,buckets{1_30,31_60,61_90,over_90},overdue_total,overdue_count,top_overdue[{name,amount,days}]}`, `ap` (same shape without top_overdue), `pl_mtd{start,end,income,expenses,net}`, `pl_prior{same}`, `deltas{measure:{value,prior,prior_date,direction_word,good}}`, `sections{name:{measured,reason}}`, `errors[]`, `meta{output_path,sidecar_path,history_path,fetch{status,cost_usd,duration_s,transcript_deleted}}`, and `finance_md`. | verify: unit test on a synthetic raw fixture, plus a key-contract test that every key the SKILL.md tells the model to paste is actually emitted. | proof: test output.
- [ ] P13. Dates and basis come from the response, never the request: each report's period and as-of are parsed from its own header, and a response whose period differs from what was asked marks that section not measured with reason "period mismatch"; the basis printed beside each section is the one the response reports, with aging stated as accrual by nature. | verify: a fixture whose response is year-to-date against a month-to-date request is not measured; mutation: echo the request dates instead and the test must fail. | proof: test output.
- [ ] P14. Realm pinning: `finance.company_id` and `finance.company_name` live in config; a `company_info` id that differs marks every section not measured, makes the first line name the company actually found, and exits 1 with a WRONG-COMPANY message naming both. | verify: two-realm fixture test. | proof: test output.
- [ ] P15. Cash is defined and shown: cash is the sum of balance-sheet accounts of type Bank plus Undeposited Funds when present, listed by name beneath the total; credit card balances appear as a separate line and are never netted against it. | verify: fixture carrying a bank account, a credit card and undeposited funds; mutation: net the card and the test must fail. | proof: test output.
- [ ] P16. Currency: the home currency from company_info is named once at the top, and any report in another currency marks its section not measured with that reason. | verify: fixture. | proof: test output.
- [ ] P17. Independent recomputation: `tests/test_finance_brief_independent.py` recomputes every number straight from the raw report JSON, importing none of the aggregation code and sharing only the fixture reader, and asserts equality; mutations aimed at the aggregator (drop the 31_60 bucket; swap current and overdue) each make it fail. | verify: the test plus a recorded mutation run. | proof: mutation log.
- [ ] P18. Polarity is a table the renderer reads (cash up good, net income up good, AR overdue up bad, AP overdue up bad, AR and AP totals neutral), and no line in `finance_md` carrying a currency amount lacks a direction word or the phrase "no comparison yet". | verify: regex scan over the rendered markdown; mutation: invert one row and the test must fail. | proof: test output.
- [ ] P19. Zero is not a measurement: a report total of exactly 0.00 with no underlying rows renders as not measured with a reason, never as $0.00. | verify: empty-AR fixture. | proof: test output.
- [ ] P20. Degraded honestly: a section whose tool errored, was skipped, or mismatched reads "not measured" with its reason; the first line of `finance_md` says what is missing whenever anything is; the script exits 0 when at least one section measured and exits 1 with the connector class when the fetch itself failed. | verify: one test per branch. | proof: test output.
- [ ] P21. Deltas compare to the previous weekly run: history rows carry kind `weekly` or `manual`, deltas are computed against the most recent `weekly` row and labelled "vs <that date>", a manual run never becomes the baseline, and with no weekly row the brief says "no comparison yet". | verify: tests with zero weekly rows, and with one weekly row plus a newer manual one. | proof: test output.
- [ ] P22. Idempotency and file placement: two runs on the same day write two sidecars under `logs/finance/`, overwrite the one `{vault}/van-gogh/finance-brief.md`, keep one history row per day and kind, append two run-ledger rows, and write nothing anywhere else (a temp-vault test asserts the exact set of paths touched). | verify: unit test and a live double run. | proof: file listing.
- [ ] P23. Voice and dashes: no U+2014 or U+2013 in any emitted JSON, markdown or HTML string, and none of the words `measured, bucket, sidecar, null, None, degraded, tool_result, connector_fetch` in `finance_md` prose; both scans are proven against a planted instance first. | verify: tests using escaped codepoints. | proof: test output.
- [ ] P24. Repo hygiene: no string from the live sidecar (company name, realm id, customer names, amounts) appears anywhere under `project-van-gogh/`, because every committed fixture is synthetic. | verify: scan script using the live sidecar values as the needle list. | proof: scan output.

### C. Skill and agent

- [ ] P25. `skills/finance-brief/SKILL.md` carries house frontmatter with the triggers (`/van-gogh:finance-brief`, "how are the books", "cash position", "who owes us", "AR aging"), the three allowed-tools lines, the runtime guard, both shell forms, runs `finance_brief.py --json`, pastes `finance_md` verbatim, adds 3 to 5 sentences of bookkeeper-controller commentary under a heading, writes the whole thing to `meta.output_path`, and asks nothing when `VAN_GOGH_UNATTENDED=1`. | verify: `tests/test_finance_skill.py`. | proof: test output.
- [ ] P26. The commentary introduces no numbers: every numeric token in it (digits with separators, or a percentage) is a whole token already present in `finance_md`; `commentary_check.py` enforces this against the live output and is proven against a planted foreign number first. | verify: checker output on the live file. | proof: checker log.
- [ ] P27. Live end to end with controls: the real headless skill run writes `finance-brief.md` whose every currency amount equals the sidecar's and leaves a run-ledger row. Control A: the same script against an empty `--input` payload renders every section not measured and the identical amount check fails. Control B: the ABSENT run from P10. | verify: `live_check.py`. | proof: captured runs.
- [ ] P28. `agents/bookkeeper-controller.md` names tracing a number in the finance brief as a routing situation in its description. | verify: grep test. | proof: diff.

### D. Scheduling, email, install

- [ ] P29. Config: the `finance` block in `config.template.json` equals `config_loader.FINANCE_DEFAULTS` (`enabled` false, `day` monday, `time` 07:00, `recipient_email` "", `company_id` "", `company_name` "", `commentary` true), the feature ships off, junk day and time values fall back, and an accessor exists for each key. | verify: `tests/test_finance_wiring.py`. | proof: test output.
- [ ] P30. Scheduler: `com.monet.finance-email` and `VanGogh-finance-email` install only when the feature is enabled and are removed when it is off with a message saying so, the plist is valid XML using the shared launchd weekday table with no `StartInterval`, it runs `finance_send.py` with the absolute `--claude` path embedded, and uninstall plus `job_states` cover it, with Windows going through `_register_optional_win_task`. | verify: tests mirroring `test_kpi_wiring.py`. | proof: test output.
- [ ] P31. Watcher: `job_watch.roster()` grades `finance-email` when the feature is on, taking days, hour and minute from config, using ledger job `finance.email`, deliverable `{vault}/van-gogh/finance/latest.html` written only after the send, and title "your weekly finance brief"; it ignores the job when the feature is off. | verify: tests. | proof: test output.
- [ ] P32. `finance_send.py`: computes first; on a fetch failure records the run with its class and does NOT claim the week; claims the ISO week only after a successful compute and immediately before the send; stamps after; writes `latest.html` and the vault markdown after the send; records a run-ledger row on every exit path; puts the as-of date in the subject; refuses and records a send whose `as_of` is older than 3 days rather than claiming it; resolves the recipient as `finance.recipient_email` else the digest recipient, and the sender as the digest sender account; builds HTML from the same report with inline styles and the house palette and no amber; respects the same MIME bound as kpi_send. | verify: tests mirroring the kpi_send suite: claim and stamp order, deliverable written after the send, no double send, stale refusal. | proof: test output.
- [ ] P33. Reconnect reminder: a scheduled run that fails NEEDS-AUTH with a sender configured mails exactly one "Your finance brief needs QuickBooks reconnected" to the recipient, naming where to do it, at most once per 7 days by a cooldown in the finance ledger, and mails nothing for ABSENT. | verify: cooldown unit test; LIVE: the real expired state produces exactly one reminder in the inbox. | proof: inbox read-back.
- [ ] P34. Live email with control: after a Gmail sender is connected here, one real brief is sent, and the received message text read back by subject contains the same amounts as the vault markdown; the control, a second forced send in the same ISO week, is refused as already handled and the inbox count for that subject stays 1. | verify: the email section of `live_check.py`. | proof: read-back output.
- [ ] P35. Scheduled viability: (a) 90 minutes after re-authorization, with no human touch in between, `connector_fetch.py --check quickbooks` prints `OK <company>`; (b) 24 hours after, a launchd-fired one-shot probe runs the same check with no human present and its log shows `OK <company>`. If (b) fails, README and CLAUDE.md state the observed re-authorization interval and that the reminder email is the contract, and this item records the interval. The probe is removed afterwards. | verify: timestamped logs. | proof: logs.
- [ ] P36. Install skill: a new Step 5b "Connect QuickBooks (optional)" says exactly where to authorize, runs `connector_fetch.py --check quickbooks` (a real `company_info` call printing `OK <company> (id ...)`, `NEEDS-AUTH`, or `ABSENT`), writes the company id and name into `finance` on OK, and never treats the health-only Connected line from `claude mcp list` as readiness; Step 8 asks a fourth question that writes `finance.enabled`; `update-settings` documents the `finance` block. | verify: skill text tests and a live `--check` in both states. | proof: outputs.
- [ ] P37. Client path: README gains a Connectors section a non-developer can follow (connect, verify, what the reminder email means, how to reconnect), and `skills/_shared/connectors.md` tells a skill author how to add another connector: server name, read-tool allowlist, argument table, the fetch call, and the failure class. | verify: both files carry those headings; cold read. | proof: files.

### E. Docs, version, hygiene

- [ ] P38. Doctrine amended in CLAUDE.md (both the tier section and the fetching-script list), README's "How it connects to your mail", PRODUCT.md, and the `data_sources.py` docstring: mail and calendar stay Tier 1 OAuth, a third mode named unattended connector fetch is defined with its confinement rules and the token-expiry caveat, and the phrases "cannot be scheduled" and "needs you present in a chat session every single time" appear nowhere in repo docs. | verify: grep control proven on a planted instance. | proof: grep output.
- [ ] P39. README skill-table row added; CHANGELOG 0.69.0 entry written for a non-technical reader with `New:` and `Improved:` bullets and no dashes; `pyproject.toml` and `.claude-plugin/plugin.json` both at 0.69.0. | verify: `tests/test_plugin_manifest.py`. | proof: test output.
- [ ] P40. The suite is green with the CLI absent from PATH; new tests carry no POSIX mode-bit assertions and compare paths through `.as_posix()`; `claude plugin validate .` passes. | verify: command outputs. | proof: outputs.
- [ ] P41. Cold read: a fresh agent given only the rendered live brief, plus a doctored copy carrying two plants (a template sentence and a contradiction), catches both plants and reports the real brief clean on one writer, no template text, no contradictions, and every count naming its direction and subject. | verify: agent transcript. | proof: transcript.
- [ ] P42. Data placement: the Slack receipt carries counts and verdicts only with amounts redacted, no currency amount appears in `runs.jsonl` or the failure ledger, and the proof folder may hold real figures. | verify: scan plus the posted receipt. | proof: scan output.

## Access audit

Run 2026-09-10 before the freeze. Every verify step's tooling was exercised
once on this machine, under the shell the evaluator uses.

- Repo venv `.venv/bin/python` is Python 3.14.7; `PATH=/usr/bin:/bin
  .venv/bin/python -m pytest -q --collect-only` collects 2156 tests, so the
  hermetic run P40 demands works here.
- `claude plugin validate .` passes today, so P39 and P40 have a baseline.
- The user venv `~/.config/van-gogh/venv/bin/python` exists (the interpreter
  every SKILL.md command names).
- `gws` is on PATH at `/opt/homebrew/bin/gws`, which is how P33 and P34 read
  the inbox back.
- Playwright 1.58.0 is present, though no punch item needs it.
- QuickBooks: `claude mcp list` reports Connected, but a real `company_info`
  call returns `requires re-authorization (token expired)`. That is the P5
  live evidence, captured before any build. The P4, P9, P27, P34, P35 and
  P41 success paths are blocked until Nobel re-authorizes, and P35's clock
  starts at that moment.
- context7 answers a confined fetch (`resolve-library-id`, 2069 byte result),
  so P10's generality target and control are both runnable. Its success
  stream also supplied a fact the parser depends on: a successful
  `tool_result` carries no `is_error` key at all rather than `is_error:
  false`, so the verdict must treat absent as success.
- Mail sender: `~/.config/van-gogh/.env` holds no `GOOGLE_REFRESH_TOKEN_*` or
  `MS_GRAPH_REFRESH_TOKEN_*`, so P34 waits on `/van-gogh:add-account`. P32's
  unit coverage does not.
- Note, not a blocker: the installed plugin cache on this machine is a stale
  0.27.0 scoped to a former path, so a live skill run needs
  `--plugin-dir <repo>` (proven to load the repo's skills headlessly). The
  scheduled path is pure Python and does not depend on the cache.

## Evaluation log

### Build progress, 2026-09-10 (self-reported, not an evaluation)

Built and green: 2390 tests pass, 3 skipped, with `PATH=/usr/bin:/bin` so the
run is hermetic. `claude plugin validate .` passes. Version 0.69.0 in both
manifests with a CHANGELOG entry.

Proven live already, because they do not need a working token: the confined
fetch against context7 returns real data (2004 bytes, 9.5s, $0.099); a
nonexistent server is ABSENT rather than NEEDS-AUTH; the real expired
QuickBooks token produces the `connector` class end to end; the session
transcript is deleted after every fetch.

Proven end to end through the `--input` path: the whole brief renders, two runs
leave one history row and two ledger rows, nothing is written outside
`van-gogh/logs/finance/` and the brief itself, and a planted prior week makes
cash read "the good direction" while overdue reads "the wrong direction".

Four defects were found by the tests and fixed, each a real one rather than a
test artifact: an aging detail's due date was reported as the customer name; a
figure was rendered with no direction word; the overdue bucket list was a
second definition that could disagree with the bucket table; and the basis line
read "on a accrual basis".

Blocked, all on the same thing: P4, P9, P27, P34, P35 and P41 need a working
QuickBooks token. Checked six times over the build; still expired.


## Lessons

(Phase 6 appends here)
