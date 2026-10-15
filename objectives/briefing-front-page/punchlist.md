# Punch List: briefings that fit on one screen

Created: 2026-09-04
Status: evaluated-pass
Contract-SHA256: aa0a4ff49d74857f195c88561412b912e1b757c786fd784c44de0d4493ded2cf

Source plan: ~/.claude/plans/i-want-to-take-cozy-wadler.md
Approval: Nobel typed "build <plan> do it well, test, then send a PR". That is the human gate.
Red-team: the plan was red-teamed once during planning (noted in the plan's contract header).

## Contract

Shape
- P1. On a 4-business, 120-item fixture the terminal render above the rows is under 45 lines; the opening paragraph is under 60 words with at most 3 named items; a 300-item fixture meets the same bound. verify: `test_fits_one_screen`.
- P2. Conservation: front count plus the sum of row counts equals open items on 20 seeded random fixtures. verify: `test_conservation`.
- P3. One style: front page, rows and folded bodies share `_BAND`, `_LABEL_COL` and the three labels; no fourth label anywhere; the front page's column geometry is byte-identical to a bucket section's on the same fixture. verify: `test_one_render_style`.
- P4. The draft continuation line never collides with its tag at the wrap width on the longest configured business and function names. verify: `test_draft_line_geometry`.
- P5. Rendering twice from the same input is byte-identical. verify: `test_render_idempotent`.

Ranking
- P6. A late item is never folded even past the cap, and the opening sentence states the late count. verify: `test_late_never_folded`.
- P7. Rank is deterministic: late by days late, dated by due, judged-relevant waiting by age, other waiting by age; shuffled 10 times, same output. verify: `test_rank_order`.
- P8. Stale never reaches the page; a dated item due within 3 days beats every undated item even when the judgment is degraded, and the degraded notice survives. verify: `test_stale_never_front`, `test_degraded_keeps_dated_first`.
- P9. No item appears under a heading the user did not configure; unsorted items only in the "Couldn't place" row; "file X under <bucket>" appends the keyword and the next render places it. verify: `test_no_invented_headings`, `test_file_under_sticks`.

Folds and function
- P10. Row flags: "nothing moving" and "quiet N days" each fire on a planted fixture and not on its control; never more than two flags. verify: `test_fold_flags`.
- P11. Every folded item carries exactly one function from the configured list; a classifier reply outside the list lands in Other; with no model the keyword fallback files a planted invoice under Accounting, a term sheet under Legal, an RFP under Sales, an unmatched item under Other. verify: `test_assign_function`.
- P12. Per-function totals in a row sum to that business's folded count. verify: `test_fold_functions_conserve`.
- P13. `briefing.functions[]` overrides the defaults and a renamed list renders its own names only. verify: `test_functions_override`.
- P14. Each folded business body is byte-equal to the pre-change `render_buckets_md` slice, then grouped by function under the priorities. verify: `test_fold_body_unchanged` against the frozen snapshot.
- P15. "Open <bucket> <function>" pastes exactly that group's slice and nothing else. verify: `test_open_function_slice` plus one captured run in P25.
- P16. Workbench renders a closed callout as `<details>` without `open`; digest HTML renders it as a shaded block after the front page. verify: `test_callout_details`, `test_callout_block`.

Drafts
- P17. The draft CLI prints `{no_token}` with a token-less state dir, and `{ok, existing}` with zero API calls when the ledger already holds the key. verify: `test_draft_email.py`.
- P18. No draft for a counterparty on a dead or terminal hotcache deal; a rerun over the same input creates 0 new ledger rows. verify: `test_no_draft_for_dead_deal`, `test_draft_idempotent`.
- P19. No send path: the AST scan passes and a python scan of `draft_email.py` finds no send call. verify: test plus scan.

The page
- P20. Each briefing stores one artifact URL; a second run republishes to the same URL and creates 0 new artifacts. verify: `test_url_reused`.
- P21. The skill reads the stored page before republishing. verify: `test_reads_before_publish` (mock records call order).
- P22. Fail open with control: with publishing unavailable the briefing renders in full, exits 0, and carries one line naming why the page did not update; with publishing available the same run carries the link line and no failure line. verify: two captured runs.
- P23. Staleness shows on both surfaces: a page older than the newest briefing says so in its header; the digest carries one line naming the reason and the date the page still shows. verify: `test_stale_banner`, `test_digest_stale_line`.
- P24. The page defines every color token in the bare `:root`, folds are closed `<details>`, and `voice_check.py` passes on its text. verify: `test_page_tokens`, script run.

Safety and voice
- P25. Live end to end with control, Tier 2 path: the real `morning_coffee.py` on a 4-business, 100-item `--input` fixture, then the real SKILL.md through `claude -p`; DO TODAY has at most 7 non-late items and one row per business. Control: the same run with `front_page_cap` absent and no front-page block; the identical check fails. Real Drafts-folder creation is NEEDS_HUMAN until `/van-gogh:install-van-gogh` has run here, with the exact command and the check to read. verify: captured runs in `~/Downloads/proof-briefing-front-page/`.
- P26. Privacy negative: a page built from a fixture planting an OAuth token, an API key and a local path contains none of them; the planted control fails the scan first. verify: `test_page_redaction`.
- P27. Zero em or en dashes in every added diff line and every rendered fixture string, scanned in python with a planted-dash self-test first. verify: scan output 0.
- P28. `voice_check.py` passes on the rendered front page; the banned list includes unassigned, bucket_tag, judged, sidecar, degraded, rank, fold. verify: script run.
- P29. Cold read: a fresh agent given only the rendered front page names the item it would do first (rank 1 or 2), names one thing it would skip (a folded item), and reports 0 contradictions. verify: transcript in proof.

Delivery
- P30. All four briefings ship the shape: each SKILL.md has the render order and each script emits the five keys on success and failure branches; `skill_contract.py` finds every referenced key in the real Tier 2 output. verify: grep plus key diff per script.
- P31. Existing consumers unchanged: `buckets_md` for week and tea and `digest_html` output for all four are byte-equal to the frozen snapshots; the suite is green with the test count higher than before. verify: snapshot tests plus pytest counts.
- P32. Version 0.32.0 in both manifests. verify: `test_plugin_manifest.py`.

## Out of scope

- The Workbench ticket UI and nod column.
- A new chat runtime for "open X" (the skill handles it in-session).
- Sending mail, ever.
- A public page. Artifacts stay private; sharing is the user's action from the page menu.
- The spam filter.

## Access audit

Run 2026-09-04 before any edit.

- pytest 9.1.1 in `.venv`: reachable, 797 tests green as the baseline.
- git remote `NVentures/project-van-gogh-claude`, `gh` authenticated: reachable.
- Playwright 1.58.0 and `screencapture`: reachable.
- `claude` CLI at /opt/homebrew/bin/claude: reachable, used for the live P25 run.
- `~/.config/van-gogh/`: holds only the two OAuth app-credential files. No
  vault pointer and no refresh tokens, so this machine has NO live Van Gogh
  install. Consequence, recorded rather than worked around: every live check
  runs through a throwaway Tier 2 vault built by
  `objectives/briefing-front-page/tier2_env.py` (a real config.json, a real
  week file, a real hotcache, a real `--input` payload, a real subprocess).
  Real Drafts-folder creation cannot be checked here and stays NEEDS_HUMAN, as
  P25 already says.
- Snapshots for P14 and P31 were frozen from the pre-change commit by
  `freeze_snapshots.py` before the first edit, so they grade this work against
  code that predates it.

## Evaluation log

## NEEDS_HUMAN

P25, the drafts half. This machine has no vault pointer and no OAuth refresh
tokens, so no draft can reach a real Drafts folder here. The exact check, once
`/van-gogh:install-van-gogh` has run and one account is authorized:

```bash
printf 'Test body.\n' > /tmp/vg-draft-test.txt
"$HOME/.config/van-gogh/venv/bin/python" "$CLAUDE_PLUGIN_ROOT/app/draft_email.py" \
  --provider microsoft --label Outlook --to "<your own address>" \
  --subject "Van Gogh draft test" --body-file /tmp/vg-draft-test.txt \
  --counterparty "Draft Test"
```

Read for: `{"ok": true, "web_link": ...}` on the first run, one new UNSENT
message in that account's Drafts folder, and nothing in Sent. Then run the
identical command again and read for `{"ok": true, "existing": true}` with no
second draft created. Substitute `--provider google --label Gmail` to check the
Gmail path; its `web_link` is the drafts folder rather than the draft, which is
the closest honest equivalent Gmail exposes.

## Deviations from the contract, stated rather than buried

One. P14 says a folded business body is the pre-change render "then grouped by
function under the priorities", which reads as: priority headings first, then
function headings for the leftover. That is what shipped in round one, and the
cold read caught what it does to a reader: the business row counts every folded
item by function (P12 requires those totals to sum to the folded count), while
the fold showed only the leftover under each function heading. A row read
"Sales 3" and the heading behind it held one item. Two different partitions of
one set can never show the same numbers, so the row and the fold contradicted
each other on every business that has priorities set.

What shipped instead: a fold is grouped by function and nothing else, exactly
the partition the row counts, and a priority-matched item leads inside its
function group and names its priority on its own line ("on Close Meridian").
Every number in a row now equals the number behind the heading it opens, which
is checked by `test_fold_headings_match_the_row_counts` and by 32 captured live
slices in `p15_slices.txt`. The stated priorities still lead and are still
named; they are no longer headings.

The verify step P14 actually names, `test_fold_body_unchanged` against the
frozen snapshot, passes: the pre-change ledger render is byte-identical. It is
the sentence's other half that this departs from, and it is Nobel's call
whether to keep the partition as shipped or take the row and fold back to
disagreeing.

## Two decisions Nobel took, 2026-09-04

**The third label is now "Waiting on them".** It read "Older, still open" and
was not about age: it is assigned by which section an item came from, and those
sections mean "you sent last and got no reply", so a ten day old item carried
it while a twenty one day old item beside it read "Waiting on you", and two
items of identical age could carry opposite labels. Five cold reads found it.

This deliberately re-baselines P31 for that one string. Five of the eight
frozen snapshots held it. They were re-baselined by substituting ONLY that
label, never by regenerating them from the code being graded, so every other
byte is still frozen from commit 5beb8cf and the diff proves exactly one string
moved. Terminal padding is unchanged: "Older, still open:" is 18 characters
padded to 20, "Waiting on them:" is 16 padded to 20, so no other column shifts.
P3 still holds: three labels, same column, same geometry.

**The "Say open Harbor Solar" line is gone.** Every cold read from round three
on called it a chatbot menu bolted onto a briefing. The commands still work
whenever the reader asks; they are simply no longer printed at the reader every
morning. `_shared/front-page.md` still documents all four of them.

The third thing they raised, the "THE REST" header, stays as the plan wrote it.

### Evaluation 2026-09-04 13:13, round 5
Contract hash: verified (recomputed aa0a4ff49d74857f195c88561412b912e1b757c786fd784c44de0d4493ded2cf = stored)
Graded from scratch, nothing carried forward. Suite re-run: 877 passed.

| Item | Verdict | Evidence |
| P1 | PASS | Ran the render myself, not the test. 120 items: front page 27 lines, opening 24 words, 0 named items, 4 fold rows. 300 items: 26 lines, 24 words. Both under 45 / 60 / 3. |
| P2 | PASS | My own loop over seeds 0-19 at 90 items: front + sum(row folded) == open on all 20. |
| P3 | PASS | Read `_BAND`, `_LABEL_COL`=20, `_WRAP`=78 out of the module. Regexed every "Label: DOT" head across front_page_md, fold_rows_md, folded_md and buckets_md on one fixture: exactly 3 distinct labels (Late, Waiting on you, Waiting on them), no fourth. Label-column width 22 on both the front page and a bucket section, same band string in both. |
| P4 | PASS | `test_draft_line_geometry` green, and mutation-proven: deleting the `len(right) > width - indent` wrap branch in `_right_align` fails it (98 > 78 = _WRAP). Restored, worktree clean. |
| P5 | PASS | Built the page twice from one bucket set: front_page_md, fold_rows_md and folded_md all byte-identical. |
| P6 | PASS | `test_late_never_folded` plants 12 late items against cap 7 and all 12 reach the page; opening reads "12 of your 12 open items are late". My own run at cap 3 with 2 late: both on the page, opening names the count. |
| P7 | PASS | Shuffled all four sections 10 times with different seeds and rebuilt: front_page_md + fold_rows_md identical every time. |
| P8 | PASS | `test_stale_never_front` (90-day item off the page, counted in its row) and `test_degraded_keeps_dated_first` (dated item ranks first with judged_relevant None, "Heads up" survives) both green. |
| P9 | PASS | `test_no_invented_headings` and `test_file_under_sticks` green; the unassigned item lands in "Couldn't place", widening a business's keywords moves it to that tag. `_shared/front-page.md:92` documents the append through /van-gogh:update-settings. |
| P10 | PASS | Ran both flags and both controls myself. Planted: 'Close the widget deal: nothing moving' with alerts, 'quiet 31 days' from newly_cold. Control with both priorities matched: [] flags. Control with alerts={}: no quiet flag. Cap of 2 holds. |
| P11 | PASS | tests/test_assign_function.py green, 11 tests. The four the contract names: invoice->Accounting, term sheet->Legal, RFP->Sales, unmatched->Other, asserted twice. A reply of "Vibes" lands in Other. |
| P12 | PASS | My own check on seeds 1-5 at 120 items: every row's per-function counts sum to its folded count. |
| P13 | PASS | `test_functions_override`: ["Deals","Paper","Other"] renders only those names, and a retired default name cannot come back. |
| P14 | PASS | Independently reproduced. Built a worktree at the pre-change commit 5beb8cf and re-ran freeze_snapshots.py there: buckets_week_md.txt is byte-identical to the committed snapshot except the one declared label substitution. `test_fold_body_unchanged` green. The punch list's stated deviation on the sentence's second half stands as declared; it is Nobel's call. |
| P15 | PASS | Three ways. `test_open_function_slice` green; mutation-proven (disabling the function filter fails it); and 35 live slices captured by live_check.py, every one pasting exactly its row's count, zero mismatches. |
| P16 | PASS | `test_callout_details` (closed `<details>`, no `<details open>`) and `test_callout_block` (no `<details>`, shaded `background:#f6f8fa`, ordered after the front page) green. |
| P17 | PASS | Ran the real CLI against a fresh empty VAN_GOGH_STATE_DIR: printed `{"no_token": true, "reason": "Missing refresh token: env var MS_GRAPH_REFRESH_TOKEN_OUTLOOK is not set...", "key": ...}` and wrote no ledger. `test_existing_ledger_entry_makes_zero_api_calls` proves the second call returns existing with the call count still 1. |
| P18 | PASS | `test_no_draft_for_dead_deal` (stage=dead skips, stage=closing drafts) and `test_draft_idempotent` green; live_check rerun over the same input left the seeded ledger byte-equal at 1 row. |
| P19 | PASS | Ran my own AST walk over app/draft_email.py: zero send-shaped calls. Every literal "send" in the file is a docstring or the `digest_send` import of clean_address/build_gmail_raw. tests/test_draft_email.py carries both the AST and source-text scans. |
| P20 | PASS | `test_url_reused` green; live_check's second run left briefing_pages.json byte-equal with exactly one entry. |
| P21 | PASS, with a note | `test_reads_before_publish` green, and I re-checked all four SKILL.md by hand: `action: "read"` precedes "publish the page" in every one (morning-coffee 341/343, afternoon-tea 278/280, week 324/326, week-retro 189/191), and the prose reads "and only then publish". The contract's parenthetical "(mock records call order)" does not describe the test: there is no mock, because the read and publish are model artifact actions, not script calls. The criterion is verified; the parenthetical is wrong. |
| P22 | PASS | Both captured runs, fresh. Unavailable: rc 0, full render, note "The web page did not update: this session has no publishing surface. It still shows 2026-09-03: <url>". Available: "The page is up to date: <url>", no failure line. Mutation-proven: hardcoding the reason to a generic string makes live_check report NO on "carries one line naming why". |
| P23 | PASS | `test_stale_banner` (page dated 09-03 against newest 09-04 says so; the current page does not) and `test_digest_stale_line` green; the live P22 line carries both the reason and 2026-09-03. |
| P24 | PASS | `test_page_tokens` (every `var(--x)` used is defined in the bare `:root`), `test_folds_are_closed_details`, and voice_check.py PASS over front/page, front/rows, front/folded. |
| P25 | PASS on both gradeable halves | Script half: live_check on a 4-business 100-item Tier 2 `--input` fixture, rc 0, 7 non-late items, 4 rows; control (buckets_md) fails the identical check on 'has DO TODAY' and 'at most 7 non-late'. Model half: skill_live_run put the real skills/morning-coffee/SKILL.md through `claude -p`, 7 items / 4 businesses; the control skill produced 100 items and failed the identical check. Drafts-folder creation stays NEEDS_HUMAN (verified: ~/.config/van-gogh has no .env and no vault-pointer). |
| P26 | PASS | `test_page_redaction` green and mutation-proven twice: dropping the first redaction rule fails it, and making redact a no-op fails it plus `test_redaction_covers_every_platform_this_ships_to`. |
| P27 | PASS | dash_scan: 4809 added diff lines, 0 dashes; 5 rendered strings clean. Mutation-proven beyond its own self-test: I planted a real em dash in app/week_review.py in a worktree and the scan found it both as an uncommitted working-tree line and as a committed branch-diff line. |
| P28 | PASS | voice_check.py PASS. The banned list at voice_check.py:24-32 contains all seven the contract names (unassigned, bucket_tag, judged, sidecar, degraded, rank, fold) and the renders include the front page, the rows and the folds. |
| P29 | FAIL | Regenerated p29_full_briefing.txt with cold_read_input.py (259 lines, front page + rows + folds) and ran three fresh cold reads on that text alone. Clauses 1 and 2 pass: all three named "Lumen redline v4 98" (rank 2), two named the folded "Harbor site walk 28". Clause 3 fails: no reader reported 0 contradictions, and two of the three independently reported the same one, which I verified against the file myself. Line 3 reads "7 of your 100 open items need you first." and the block is headed "DO TODAY". Four of the seven rows inside it are labelled "Waiting on them": lines 9, 14, 22 and 24 (Cedarworks Q3 invoice 57, Cedarworks Q3 invoice 10, Summit term sheet 29, Summit term sheet 27). I counted the block directly: 4 "Waiting on them" against 3 "Waiting on you". The document's own third label means the counterparty owes the next move, so four sevenths of a block that says it needs the reader first is work the reader cannot do. This is render logic, not fixture noise: any corpus with dated waiting-on-them items produces it. Reader 3 named a different contradiction (that "N more" plus the 7 implies 107) which I checked and disproved, and reader 2 claimed the Harbor and Northwind folds hold 18 and 26 items, which I counted as 19 and 27; neither is counted. All other arithmetic I checked by script is clean: rows sum to 93, 93 + 7 = 100, every fold header equals its item count, and all 31 per-function row brackets match their fold sections exactly, including the new "(N on you)" direction. |
| P30 | PASS | skill_contract.py PASS: four scripts emit the five keys on the success and failure branches, 22 key references across four SKILL.md all exist in the real Tier 2 output, the render order is present in all four, and _shared/front-page.md parses as one document naming all five keys. |
| P31 | PASS | Independently verified the provenance, not just the test. Worktree at 5beb8cf, re-ran freeze_snapshots.py: 3 of 8 snapshots byte-identical, 5 differ by exactly the one declared label substitution and nothing else. Terminal padding unchanged, so no column moved. Snapshot tests green inside a 877-test run against a stated baseline of 797. |
| P32 | PASS | pyproject.toml:3 `version = "0.32.0"`, .claude-plugin/plugin.json:4 `"version": "0.32.0"`, test_plugin_manifest.py green. |

Overall: FAIL (31 of 32 gradeable items PASS, 1 FAIL; P25's Drafts-folder half remains NEEDS_HUMAN)

Mutations run this round, each restored and the tree left clean at 40c74e7: P4 wrap branch deleted (fails), P15 function filter disabled (fails), P22 page-note reason hardcoded (live check reports NO), P26 first redaction rule dropped and redact made a no-op (both fail), P27 real em dash planted in both the working tree and a commit (scan finds both), row-direction test broken two ways, restoring "(N waiting)" and always printing the bracket (both fail).

### Evaluation 2026-09-04 13:30, round 6
Contract hash: verified (recomputed aa0a4ff49d74857f195c88561412b912e1b757c786fd784c44de0d4493ded2cf = stored)
Graded from scratch, nothing carried forward. Suite re-run: 881 passed; pre-change commit 5beb8cf re-run in a throwaway worktree: 797 passed.

| Item | Verdict | Evidence |
| P1 | PASS | My own render, not the test. 120 items: 28 lines above the rows, opening 35 words, 0 named items. 300 items: 27 lines, 18 words. Under 45 / 60 / 3 both. |
| P2 | PASS | My own loop, seeds 0-19 at 90 items: front + sum(row folded) == open on all 20, zero mismatches. |
| P3 | PASS | Measured the label geometry on one fixture: front page {(indent 2, text col 22)}, bucket section {(2, 22)}, equal. Labels found anywhere in front page, rows and folds: Late, Waiting on you, Waiting on them. No fourth. |
| P4 | PASS | test_draft_line_geometry green; mutation (delete the wrap branch in _right_align) fails it 1/1. |
| P5 | PASS | Built twice from one bucket list: front_page_md, fold_rows_md and folded_md all byte-identical. |
| P6 | PASS | 20 planted late items with cap 7: front 20, folded 0, opening reads "20 of your 20 open items are late". |
| P7 | PASS | Ten shuffles of a seeded 80-item fixture, front-page key order identical every time. |
| P8 | PASS | 400-day item never reaches the page while a 2-day dated one does; stale counted in its row. Degraded ledger still carries "Heads up" and the reason "the usage limit was reached". |
| P9 | PASS | Fold headings on a real fixture are exactly the four configured display names. test_file_under_sticks moves a Zamboni item from unassigned to cedar on the next render. |
| P10 | PASS | My own plant and control: planted fixture emits "Close the widget deal: nothing moving" and "Ray quiet 31 days"; the control where both priorities move and alerts are empty emits []. Never more than two per row. |
| P11 | PASS | Keyword fallback with no model: "Q3 invoice" -> Accounting, "Term sheet redline" -> Legal, "RFP response" -> Sales, junk -> Other. |
| P12 | PASS | My own sum over seeds 1-5 at 100 items: per-function counts equal each row's folded count, zero mismatches. |
| P13 | PASS | briefing.functions ["Deals","Paper","Other"] renders only those names; an override omitting Other has Other appended by briefing_functions(), which is the documented always-available bucket. |
| P14 | PASS | Independent provenance check: regenerated the eight snapshots from commit 5beb8cf's app/ in a detached worktree. Six are byte-identical to the committed files; the other two differ on exactly the disclosed label substitution ("Older, still open:" -> "Waiting on them:") and nothing else. test_fold_body_unchanged green. |
| P15 | PASS | test_open_function_slice green; live_check pastes 35 slices with no mismatch; mutation (fold_slice ignores the function filter) fails the test. |
| P16 | PASS | Rendered a callout myself: page HTML gives `<details>` with no `open`; digest HTML gives a shaded div (background:#f6f8fa, border, radius) and no details tag. |
| P17 | PASS | Ran the real CLI with VAN_GOGH_STATE_DIR pointed at an empty dir: prints {"no_token": true, ...} and exits 0. test_draft_idempotent covers the {ok, existing} branch with no API call. |
| P18 | PASS | test_no_draft_for_dead_deal and test_draft_idempotent green; live_check reruns morning_coffee over a seeded ledger and reads back 1 row, unchanged. |
| P19 | PASS | My own AST walk of draft_email.py: zero calls named send/sendMail/send_mail/send_email/send_message. The only textual hit is the docstring that forbids them. |
| P20 | PASS | Live: the stored sidecar after a second run is one entry with the same URL. test_url_reused green. |
| P21 | PASS | All four SKILL.md order the read step before the publish step (morning-coffee SKILL.md:341-343 reads the stored URL with action: "read", then publishes to that same URL). Note: the check is document order, not the "mock records call order" the contract parenthesis describes; the skill is prose executed by Claude, so there is no runtime call to mock. |
| P22 | PASS | Two captured live runs: unavailable -> rc 0, full render, page_note "The web page did not update: this session has no publishing surface. It still shows 2026-09-03: <url>"; available -> "The page is up to date: <url>" and no failure line. Mutation (page note stops naming why) makes live_check report NO. |
| P23 | PASS | test_stale_banner and test_digest_stale_line green: a page dated 2026-09-03 against newest 2026-09-04 carries "did not update"; failure_line names the reason and the date the page still shows. |
| P24 | PASS | My own scan of a rendered page: 13 var() tokens used, all 13 declared in the bare :root, none undeclared; folds are `<details>` with no open. voice_check.py passes on the page text. |
| P25 | PASS | Live end to end, both halves with controls. Script: rc 0, DO TODAY 7 items / 7 non-late / 4 businesses named, control (no front_page_cap) fails "has DO TODAY" and "at most 7 non-late". Model: the real SKILL.md through claude -p gives 7 items and 4 business rows, the control skill gives 101. The Drafts-folder half stays NEEDS_HUMAN, see below. |
| P26 | PASS | test_page_redaction green (control leaks first, redacted page clean). Two mutations fail it: redact() made a no-op, and the Windows path branch narrowed back to /Users and /home. |
| P27 | PASS | dash_scan: self-test finds a planted em and en dash, 4887 added diff lines scanned, 0 dashes, 5 rendered strings clean. Two mutations detected: a planted em dash on an uncommitted added line, and an em dash inside _BAND (found in front_page_md, fold_rows_md and page_html). |
| P28 | PASS | voice_check.py PASS over 12 rendered outputs including front/page, front/rows, front/folded, front/quiet and front/slice. DEVELOPER_WORDS holds all seven required strings (unassigned, bucket_tag, judged, sidecar, degraded, rank, fold) and the match is substring, so "folded" would hit "fold". |
| P29 | FAIL | Regenerated the briefing with cold_read_input.py (259 lines) and ran five fresh cold reads on that text alone. All five named "Lumen redline v4 98", which is rank 2, so that half holds. Four of five named a folded item to skip; read 4 named "Cedar grid study 4", which is on the front page, not folded. Contradictions reported: 2, 2, 3, 0, 2. No single read met all three requirements. I verified every claim: no line pair genuinely disagrees. Detail below. |
| P30 | PASS | skill_contract.py PASS: 22 key references across four skills all resolve against real Tier 2 output, every SKILL.md carries the render order, the shared contract file names all five keys. |
| P31 | PASS | Snapshots byte-equal to the pre-change render except the one disclosed label string (verified by regenerating from 5beb8cf, see P14). Suite 881 passed against 797 at the pre-change commit. |
| P32 | PASS | pyproject.toml version = "0.32.0", .claude-plugin/plugin.json "version": "0.32.0", test_plugin_manifest.py green. |

Overall: FAIL (31 of 32 gradeable items PASS, 1 FAIL; P25's Drafts-folder half remains NEEDS_HUMAN)

P29 detail. Every number in the regenerated briefing closes, checked by hand and
by script: 3 "Waiting on you" and 4 "Waiting on them" in DO TODAY, matching the
new sentence; 19 + 27 + 18 + 29 = 93 = 100 - 7; each row's per-function counts
sum to that row's total and equal the checkbox lines actually printed in its
fold (19, 27, 18, 29); none of the seven promoted items appears again below.
Two reads failed on their own arithmetic: read 3 called the 3/4 split
"backwards" while its own enumeration reads 3 on you and 4 on them, and read 5
said the Cedar fold prints 17 items while its own list sums to 18 (it prints
18). Those are reader errors, not defects.

Two findings are not arithmetic and recur across readers:

1. Three of five read "CEDAR 18" as Cedar's total open count rather than its
   share of the remainder, then computed a "real" total of 22 and called the
   page inconsistent. The disclaimer "The 7 above are not repeated here." is
   printed once, eight lines above the first row, and did not reach them.

2. Two of five flagged the demotion note as inconsistently applied. In the
   rendered file, "Qx redline v4 48" carries no note and sits directly above
   "Qx lease exhibit 58", which carries "(dropped down, since this is not one
   of your Harbor Solar priorities: Close Meridian, Land the QX diligence)".
   Both are Waiting on them, both Legal, neither matches a priority. The cause
   is in app/week_review.py:1455-1465: demoted_because is set only for an item
   demoted out of cold_urgent, so an equally non-priority item that was already
   in cold_monitor gets no note. A reader cannot see that distinction. The same
   note also tells a QX item it is not one of a priority list that names "Land
   the QX diligence".

Mutations run this round, each restored, both scratch worktrees removed and the
repo left clean at d02a24e: P4 wrap branch deleted (test fails), row count
prints "(N waiting)" again (fails), on_them forced to 0 (2 tests fail), on_you
and on_them swapped (fails), the split clause dropped (2 fail), fold_slice
ignores its function filter (fails), redact made a no-op (2 fail), the Windows
path pattern narrowed (fails), an em dash planted on an added diff line
(dash_scan FAIL), an em dash planted in _BAND (dash_scan FAIL), the publish
failure note stops naming why (live_check FAIL). One mutation did not bite:
removing sub["priorities"] = [] from fold_slice leaves test_open_function_slice
green, because the item set is already filtered by then; the targeted mutation
above does fail it, so P15's test is not vacuous.

## Lessons

- A cold read finds a class of defect no gate can, and one round is not enough.
  Seven rounds each found something real, and the last three found nothing the
  gates could have caught: they were all a true statement that misleads. Run
  the loop until a round is clean, not until one round happens to pass.
- The recurring shape in this build, three times over: a bare word beside a
  number with the direction left to the reader. "Older, still open" that was
  not about age, "(3 waiting)" that never said which way, and an opening that
  claimed seven items needed the reader when four were on someone else. Any
  count that has a direction must say it in the same breath.
- A test written for a boundary has to sit past it. The wrap test used a
  62-character tag that renders at exactly the 78-character limit, so it passed
  with the fix deleted. Same class: the slice test pinned the one fixture
  business with no priorities set, the only case where its bug cannot appear,
  and the redaction test planted only POSIX paths so a dead Windows branch
  shipped. Pick the fixture that fails when the fix is removed, then prove it
  by removing the fix.
- A scan on a stacked branch must diff against the branch it is stacked on. The
  dash scan diffed against main and read thirty-one commits this change did not
  write. It also counted the evaluation log quoting a dash as a violation,
  which is the prohibition-as-violation trap in a new place.
- Two partitions of one set can never show the same numbers. When a summary
  counts one way and the thing it opens groups another, the reader is right
  and the design is wrong; collapse to one partition rather than explaining the
  difference.
- A patch script that writes as it goes leaves half the files changed when a
  later match fails. Buffer every edit and write at the end. This happened once
  in this build and was caught only because the next command re-read the files.


### Evaluation 2026-09-04 11:38, round 1
Contract hash: verified (aa0a4ff49d74857f195c88561412b912e1b757c786fd784c44de0d4493ded2cf, recomputed = stored)

| Item | Verdict | Evidence |
| P1 | PASS | Ran the render myself, not the test: 120 items renders 27 lines / 24 opening words / 0 named items; 300 items renders 27 lines / 18 words / 0 named items. Fixture is 4 businesses (fold_rows carry 4 tags). `test_fits_one_screen` green. |
| P2 | PASS | `test_conservation` green; front + sum(row folded) == open on seeds 0-19. Re-checked by hand on a 90-item fixture: harbor 42, northwind 19, cedar 12, personal 10, all equal to the actual folded item counts. |
| P3 | PASS | My own measure, not the test: front page and `render_buckets_md` both put the item bullet at column 22 exactly; `_BAND` in both; labels are the three in `_BUCKET_LABELS["morning-coffee"]`. `test_one_render_style` green. |
| P4 | FAIL | `test_draft_line_geometry` passes vacuously on the half it names. Its fixture configures `functions: ["Operations And Facilities", "Other"]` but the item is assigned "Other" (assign_function's keyword table only fires for names in `_FUNCTION_KEYWORDS`, so a custom name is unreachable without a classifier). I put the long function on the page the real way (entry `function` = "Operations And Facilities", as the classifier returns) and the tag line renders 88 chars against `_WRAP` = 78: `'                          NORTHWIND DATA CENTERS HOLDINGS II · Operations And Facilities'`. With a draft note added, both continuation lines overflow (83 and 88). `_right_align` (app/week_review.py:2214) clamps at `max(indent, width - len(right))`, so a long right half always overruns the wrap. |
| P5 | PASS | `test_render_idempotent` green; two builds from fresh `sample_sections()` byte-equal on front_page_md, fold_rows_md, folded_md. |
| P6 | PASS | `test_late_never_folded` green: 12 late with cap 7 puts all 12 on the page and the opening reads "12 of your 12 open items are late". |
| P7 | PASS | `test_rank_order` green over 10 shuffles of an 80-item fixture; `test_rank_tiers` pins late > dated > judged-relevant waiting > other waiting. |
| P8 | PASS | `test_stale_never_front` and `test_degraded_keeps_dated_first` green; the 90-day undated item is folded and counted, the dated one leads, and the degraded ledger still carries "Heads up". |
| P9 | PASS | `test_no_invented_headings` and `test_file_under_sticks` green; every row and item name is a configured display name or "COULDN'T PLACE THESE", and adding "zamboni" to a business moves the item from unassigned to cedar. |
| P10 | PASS | `test_fold_flags` green. Its control only disproves "quiet", so I built the missing control myself: with every configured priority matched by an item, `fold_rows` flags come back `[[]]`, so "nothing moving" does not fire on a control either. Flag cap of two holds. |
| P11 | PASS | `test_assign_function_keyword_table` files invoice -> Accounting, term sheet -> Legal, RFP -> Sales, unmatched -> Other; a classifier reply of "Vibes" lands in Other; 150-item and 90-item fixtures show every item carrying exactly one allowed name. |
| P12 | PASS | `test_fold_functions_conserve` green. My own count on seed 9: harbor 12+2+9+3+7+2+7 = 42 = row folded; same for the other three businesses. |
| P13 | PASS | `test_functions_override` green: `["Deals","Paper","Other"]` renders only those, and "Kestrel term sheet" no longer reaches the now-unconfigured Legal. |
| P14 | PASS | Independently re-derived, not trusted: extracted the pre-change commit 5beb8cf with `git archive`, dropped in only the new fixture data, and re-rendered. All four `buckets_*` snapshots byte-match the frozen files, so the snapshots really do predate this work. `test_fold_body_unchanged` and `test_fold_body_keeps_every_item` green against them. |
| P15 | FAIL | The slice is a strict subset of the row it opens. Ran the SKILL.md's own documented one-liner over a real Tier 2 sidecar and compared each row's per-function count to what `fold_slice` pastes: harbor Sales row says 12, slice pastes 5; harbor Legal 9 -> 3; northwind Sales 2 -> 0 (the reader is told 2 and gets an empty answer); harbor Accounting 2 -> 1, People 3 -> 2, Other 7 -> 6; northwind Legal 5 -> 2, Admin 3 -> 2. Cause: `fold_slice` (app/week_review.py:2406) filters `_group_items(..., by_function=True)` by name, but an item that matched a priority is in the priority group, not a function group, so it is dropped. `test_open_function_slice` misses it because it hardcodes `r["tag"] == "cedar"`, the one fixture business with no priorities; cedar and personal match exactly, harbor and northwind do not. The "one captured run in P25" the item also asks for does not exist in ~/Downloads/proof-briefing-front-page/. |
| P16 | PASS | `test_callout_details` (workbench: `<details><summary>` with no `open`, `+` opens it) and `test_callout_block` (digest: no `<details>`, `background:#f6f8fa`, ordered after the front page) both green. |
| P17 | PASS | Did not rely on the test's monkeypatch: ran the real CLI with `VAN_GOGH_STATE_DIR` at an empty dir and got `{"no_token": true, "reason": "Missing refresh token: env var MS_GRAPH_REFRESH_TOKEN_OUTLOOK is not set..."}` rc 1. `test_existing_ledger_entry_makes_zero_api_calls` shows the second call returns `existing` with the call counter still at 1. |
| P18 | PASS | `test_no_draft_for_dead_deal` (stage=dead -> `{"skipped": "terminal_deal"}`, zero draft calls; stage=closing drafts) and `test_draft_idempotent` (3 rows before, 3 after a rerun with RE: prefixes) green. `live_check.py` P18 section: rerun over the same input, 0 new ledger rows, 1 row total. |
| P19 | PASS | My own AST walk over app/draft_email.py: 0 hits for send/sendMail/send_mail/send_email/sendmail as attribute, name or import. My own text scan (self-tested against a planted `client.sendMail(x)` and `api . send ( x )`) hits only lines 6-7, which are the docstring explaining there is no send path. The only API calls are `client.create_draft(...)` and `drafts().create(...)`. |
| P20 | PASS | `test_url_reused` green (one key, same URL, new briefing_date). `live_check.py` P20: after a second run the sidecar holds exactly one morning-coffee record with the original URL. |
| P21 | PASS | `test_reads_before_publish` green and non-vacuous: it would fail if the order flipped or the read step vanished. Read the four files myself, all say "read the stored URL, read that artifact with `action: \"read\"` ... and only then publish the page to that same URL" (morning-coffee SKILL.md:343-345 and the three twins). Note: the contract's parenthetical "(mock records call order)" is not what was built; the check is a static order check on the skill prose, which is the only static surface a prose skill has. |
| P22 | PASS | `live_check.py` captured both runs. Unavailable: rc 0, full render, one line "The web page did not update: this session has no publishing surface. It still shows 2026-09-03: https://claude.ai/public/artifacts/example-page-id". Available: `failure_line` returns "". Artifacts at ~/Downloads/proof-briefing-front-page/live/p22_{available,unavailable}.txt. |
| P23 | PASS | `test_stale_banner` (page older than newest says "did not update" and names 2026-09-04; current page does not) and `test_digest_stale_line` (line starts "The web page did not update" and carries 2026-09-03) green. Wired for real at digest_send.py:231-232 plus morning_coffee.py:659, afternoon_tea.py:834, week_retro.py:442. |
| P24 | PASS | Measured the bare `:root` myself rather than the test's pre-@media split: 13 `var()` tokens used, all 13 defined in the first `:root{...}` block, 0 undefined. `test_folds_are_closed_details` green. voice_check's own word list run against the page's visible text: 0 hits (the two raw-HTML hits, "None" and "fold", are the CSS `list-style:none` and `.foldbody` class, not reader text). |
| P25 | PASS | Ran both halves myself. `live_check.py`: rc 0, treatment 7 items / 7 non-late / 4 businesses named, control (front_page_cap absent, front-page keys stripped) fails the identical check on "has DO TODAY" and "at most 7 non-late items". `skill_live_run.py` spent a real `claude -p` on skills/morning-coffee/SKILL.md: treatment 7 items / 7 non-late / 4 businesses, control 100 items and fails the same two checks. Real Drafts-folder creation stays NEEDS_HUMAN per the contract's own wording; see the separate note below. |
| P26 | PASS | `test_page_redaction` green with a real control: the unredacted page leaks all 8 planted strings first, the redacted page leaks 0. The production path does redact, `skills/_shared/front-page.md:116` builds the page as `b.render_page(b.redact(md), ...)`. |
| P27 | FAIL | `dash_scan.py` exits 1. Self-test passes, 15293 added diff lines scanned, 1 carries a dash: `+✓ [TYPE]  {description} — {detail}`. It lives at skills/morning-coffee/SKILL.md:297 and the character is U+2014. Required output is 0. |
| P28 | PASS | `objectives/ship-ready-review/voice_check.py` rc 0, 12 rendered outputs including front/page, front/rows, front/folded, front/quiet and front/slice, 0 developer-word hits and 0 AI-tell hits. Banned list at voice_check.py:24-32 carries all seven named words: unassigned, bucket_tag, judged, sidecar, degraded, rank, fold. |
| P29 | FAIL | No transcript exists. ~/Downloads/proof-briefing-front-page/ holds 8 files, none of them a cold read, and there is none in the repo either. I ran the cold read myself on a real rendered front page: the agent named rank 1 as its first item (passes that clause), but the thing it said it would skip ("Cedar grid study 4") is a front-page item, not a folded one, and it reported 5 contradictions rather than 0. Two of the five are about the render and not the fixture: an item due today sits under "Older, still open" below two items due next week, and "(3 waiting)" in a business row reads against "Waiting on you" as the same word meaning two things. |
| P30 | PASS | `skill_contract.py` rc 0. Four scripts run against a real Tier 2 vault, all rc 0; all five keys present on the success and the fail-open branch for each of the four briefings; 19 key references across 4 SKILL.md files all exist in their script's output; render order present in all four; skills/_shared/front-page.md parses, 132 lines, names all five keys. |
| P31 | PASS | Snapshots independently re-derived from pre-change commit 5beb8cf (see P14): all 4 `buckets_*` and all 4 `digest_*.html` byte-match. Current code matches them too (`test_buckets_md_unchanged`, `test_digest_html_unchanged` green). Test count measured on both trees myself: 797 on 5beb8cf, 862 on HEAD, both fully green. |
| P32 | PASS | pyproject.toml:3 `version = "0.32.0"`, .claude-plugin/plugin.json:4 `"version": "0.32.0"`. `test_plugin_manifest.py` 10 passed. |

NEEDS_HUMAN (excluded from the counts above)
- P25, drafts half. Real Drafts-folder creation cannot be checked on this machine: ~/.config/van-gogh/ holds no vault pointer and no refresh tokens. Manual check: run `/van-gogh:install-van-gogh` and authorize at least one account, then run `"$HOME/.config/van-gogh/venv/bin/python" "$CLAUDE_PLUGIN_ROOT/app/draft_email.py" --provider microsoft --label Outlook --to <your own address> --subject "Van Gogh draft test" --body-file <a file with one line>` and read the JSON: `{"ok": true, "web_link": ...}` and a new unsent message in that account's Drafts folder, plus a second identical run returning `{"ok": true, "existing": true}` with no second draft created. The contract asks this be documented "with the exact command and the check to read"; the punch list records the NEEDS_HUMAN status but never gives the command, so that clause is unmet in the deliverable.

Overall: FAIL (4 of 32 gradeable items failing: P4, P15, P27, P29; 28 PASS; 1 NEEDS_HUMAN sub-check under P25)

### Evaluation 2026-09-04 12:06, round 2
Contract hash: verified (recomputed aa0a4ff49d74857f195c88561412b912e1b757c786fd784c44de0d4493ded2cf == stored)
Graded from scratch at HEAD ea1d6a6, clean working tree. Full suite: 867 passed.

| Item | Verdict | Evidence |
| P1 | PASS | My own render, not the test: 4-business fixture at 120 items gives 27 lines above the rows, 24 opening words, 0 named items; at 300 items 26 lines, 24 words, 0 named. Both under 45 and 60. `test_fits_one_screen` green. |
| P2 | PASS | My own count over seeds 0 to 19 at 120 items: front + sum(row folded) == open on all 20, 0 mismatches. `test_conservation` green. |
| P3 | PASS | Measured the geometry myself: the item bullet sits at column 22 in both `render_front_page_md` and `render_buckets_md` on the same fixture; `_BAND` in both; `_LABEL_COL` 20, `_WRAP` 78; morning-coffee labels are exactly the three in `_BUCKET_LABELS`, and an out-of-set label is clamped to `labels[-1]` at render. |
| P4 | PASS | Criterion holds under my own render at names longer than the test uses: a 73 char business name plus a 33 char function name gives 0 collisions and 0 lines over 78; the note takes its own line and the tag wraps below it. WARNING, the verify step is non-discriminating: I removed the new wrap branch at week_review.py:2241 and `test_draft_line_geometry` and `test_right_align_never_overruns` BOTH still passed, because their fixture tag is 62 chars and the old fallback renders it at exactly 78, the boundary. Under that same mutation my 73 char case rendered a 109 char line. The fix is real and load bearing; the test cannot fail. Lengthen the fixture name so it does. |
| P5 | PASS | Two fresh builds from seed 7 at 150 items are byte-identical on front_page_md, fold_rows_md and folded_md, and re-rendering the same buckets is identical too. |
| P6 | PASS | My own run: 12 late items with cap 7 puts all 12 on the page, 0 folded, and the opening reads "12 of your 12 open items are late." |
| P7 | PASS | My own shuffle: 10 shuffles of a 90-item fixture produce 1 distinct front_page_md. Tier order confirmed on a planted 4-item fixture: late, dated, judged-relevant waiting, other waiting. |
| P8 | PASS | My own run: a 90-day undated item is folded and counted stale while the dated item leads; with judgment degraded the item due in 2 days still leads a 40-day undated one; the degraded ledger still opens "Heads up". |
| P9 | PASS | My own run: with no matching keyword the item files under "Couldn't place these"; adding "zamboni" to Cedar's keywords moves it to cedar on the next render. Over a 90-item fixture every row and front-page bucket name is a configured display name, 0 outside. |
| P10 | PASS | My own planted and control fixtures, not the test's: a priority with nothing matching fires "Sign Lumen: nothing moving"; a `newly_cold` alert adds "Dana Ruiz quiet 21 days, a priority contact"; the control (every priority matched, no alerts) returns `[]`. With 4 empty priorities and 2 quiet contacts the row still carries exactly 2 flags. |
| P11 | PASS | Over 150-item and 90-item fixtures every item carries exactly one function and 0 fall outside the configured list. Keyword fallback with no model: invoice to Accounting, term sheet to Legal, RFP to Sales, unmatched to Other, all four correct. |
| P12 | PASS | My own count on seeds 0, 4, 9, 13 at 130 items: per-function totals equal the row's folded count on every business, 0 mismatches. |
| P13 | PASS | With `briefing.functions = ["Deals","Paper","Other"]` the rendered function names are only from that list; Legal and Sales are absent. |
| P14 | PASS | Snapshots independently re-derived, not trusted: I extracted pre-change commit 5beb8cf with `git archive`, dropped in only the fixture module, and re-rendered. All 8 frozen files byte-match by sha256, so they genuinely predate this work. `test_fold_body_unchanged` green and mutation-proven: reverting `_group_items(by_function=True)` to the two-partition shape makes it fail with "Close Meridian" appearing as a heading. On the stated deviation, I judge it justified and say so: P14's literal wording ("grouped by function under the priorities") cannot coexist with P12 (row totals partition by function) or P15 (a function slice pastes the row's count), because two partitions of one set cannot show the same numbers. The shipped single partition is the only shape that satisfies all three, the priorities still lead and still name themselves on the item line, and the deviation is disclosed in the punch list rather than buried. |
| P15 | PASS | Fixed and verified independently: over 8 seeds at 140 items I checked 208 (bucket, function) slices, comparing each slice against the exact folded item set for that function. 0 mismatches, 0 missing, 0 leaked, and each slice carries exactly one heading. Harbor and Northwind (the businesses with priorities, the prior failure) now match. The test is non-vacuous: making `fold_slice` drop priority-matched items reproduces the original bug and `test_open_function_slice` fails with "harbor Sales: row says 15, slice pastes 5". The captured P25 run exists at ~/Downloads/proof-briefing-front-page/live/p15_slices.txt, 32 slices, all row == slice. |
| P16 | PASS | My own render: `workbench_data.render_markdown` emits `<details>` with no `open` attribute; `digest_html.md_to_email_html` emits no `<details>` at all, a `background:#f6f8fa` block, and places the fold after the DO TODAY block (offset 434 < 737). |
| P17 | PASS | Ran the real CLI, not the monkeypatch: with `VAN_GOGH_STATE_DIR` at an empty temp dir it printed `{"no_token": true, "reason": "Missing refresh token: env var MS_GRAPH_REFRESH_TOKEN_OUTLOOK is not set...", "key": ...}` and exited 1. `test_existing_ledger_entry_makes_zero_api_calls` shows the second call returning `existing: true` with the call counter still at 1. |
| P18 | PASS | `test_no_draft_for_dead_deal` (stage=dead returns `{"skipped": "terminal_deal"}` with 0 draft calls, stage=closing drafts) and `test_draft_idempotent` green. `live_check.py` P18: a rerun over the same input adds 0 ledger rows, 1 row total. |
| P19 | PASS | My own AST walk over app/draft_email.py: 0 send-shaped attributes or names. My own text scan, self-tested first against a planted `client.sendMail(x)` and `api . send ( x )`: 0 hits in the file. The two `digest_send` imports at lines 62 and 96 are `clean_address` and `build_gmail_raw`, both pure helpers; the only API calls are `client.create_draft(...)` and `drafts().create(...)`. |
| P20 | PASS | `live_check.py` P20: after a second run the sidecar holds exactly one morning-coffee record, same URL, 0 new artifacts. `test_url_reused` green. |
| P21 | PASS | Read all four SKILL.md files myself: each says "read the stored URL, read that artifact with `action: \"read\"` ... and only then publish the page to that same URL" (morning-coffee:343 and 345, afternoon-tea:279/281, week:325/327, week-retro:190/192). Read strictly precedes publish in every one. Deviation, unchanged from round 1 and acceptable: the contract's parenthetical "(mock records call order)" was not built; a prose skill has no runtime to mock, and a static order check is the only surface it has. |
| P22 | PASS | `live_check.py` captured both runs. Unavailable: rc 0, full render, exactly one line "The web page did not update: this session has no publishing surface. It still shows 2026-09-03: https://claude.ai/public/artifacts/example-page-id". Available: `failure_line` returns "". Artifacts at ~/Downloads/proof-briefing-front-page/live/p22_{available,unavailable}.txt. |
| P23 | PASS | My own render: a page dated 2026-09-03 against newest 2026-09-04 carries "did not update" and names 2026-09-04; a current page carries neither. The digest line names both the reason and the date the page still shows (see the P22 capture). `test_stale_banner` and `test_digest_stale_line` green. |
| P24 | PASS | Measured the bare `:root` myself rather than the test's split: 13 `var()` tokens used on a real 120-item page, all 13 defined in the first `:root{...}` before any `@media`, 0 undefined. 4 `<details>` tags, none carrying `open`. voice_check's own banned list run against the page's visible text (tags stripped, entities unescaped): 0 hits. |
| P25 | PASS | Both halves run by me. `live_check.py`: rc 0, treatment 7 items / 7 non-late / 4 businesses named; control (front_page_cap absent, front-page keys stripped) fails the identical check on "has DO TODAY" and "at most 7 non-late items". `skill_live_run.py` spent a real `claude -p` on the shipped SKILL.md: treatment 7/7/4, control 101 items and fails the same two checks. The drafts half stays NEEDS_HUMAN by the contract's own wording, and the exact command plus what to read for is now written into the punch list's NEEDS_HUMAN section, which closes round 1's complaint that the clause was unmet. |
| P26 | FAIL | The redaction added by this branch does not redact a Windows local path, on a project whose CLAUDE.md states it ships to macOS and Windows 10/11. app/briefing_html.py:263 reads `re.compile(r"(?:/Users/|/home/|[A-Za-z]:\\\\Users\\\\)[^\s\"'<>\|]+")`. In a raw string `\\\\` is four literal characters, so the regex requires TWO backslashes and matches `C:\\Users\\...`, which no real path has; a real `C:\Users\nobel\AppData\Roaming\van-gogh\.env` does not match and survives `redact()` verbatim into the published page. I confirmed it directly: the same planted set gives 8 of 8 leaks in the control and, after `redact()`, still leaks the Windows path. POSIX and Linux paths and all six credential shapes redact correctly. `test_page_redaction` cannot catch this because PLANTED at tests/test_briefing_artifact.py:137 holds only a POSIX path. The author clearly intended Windows coverage, so this is an over-escaping bug, not an omission. Fix: `[A-Za-z]:\\Users\\` (one escaped backslash pair) and add a Windows path to PLANTED. |
| P27 | PASS | Fixed. `dash_scan.py` exits 0: self-test finds a planted em and en dash, 4529 added lines scanned, 0 carry a dash. My own independent scan of `git diff 5beb8cf...HEAD` counts 4707 added lines with exactly 1 dash, and that one line is the round-1 evaluation entry in this punch list quoting the dash it found, which is the file the scan legitimately exempts. The offending product line is now `+✓ [TYPE]  {description}: {detail}` at skills/morning-coffee/SKILL.md:297, a colon in place of U+2014. 5 rendered fixture strings scanned, 0. |
| P28 | PASS | `voice_check.py` rc 0 over 12 rendered outputs including front/page, front/rows, front/folded, front/quiet and front/slice: 0 developer-word hits, 0 AI-tell hits. The banned list carries all seven named words. Proved non-vacuous myself by planting "the rank of this fold is degraded, delve into it" as a 13th render: it fired on degraded, rank, fold and delve and exited 1. |
| P29 | FAIL | Still failing, and no transcript exists in proof (the directory holds the rendered INPUT, p29_full_briefing.txt, and no cold read). I ran the cold read twice myself with fresh `claude -p` agents given only the rendered briefing and no repo context. Both runs name rank 1 ("Lumen redline v4 98") as the item to do first, so that clause passes. Both fail the other two. Run 1: the thing it would skip is "Pricing Page Q3 invoice 10", and it says itself "It appeared in the DO TODAY list", not a folded item; CONTRADICTIONS: 8. Run 2, with contradictions scoped strictly to "only count things the page itself gets wrong": same skip item, again a DO TODAY item; CONTRADICTIONS: 4. Run 2's four share one root and are the actionable finding: the reader cannot tell whether a business row's count includes the promoted items. It reads "the other 93 are filed below by business and function", then looks up "Summit term sheet 29", tagged PERSONAL Legal, under Personal Legal 9 and does not find it, and reads that as the fold being short the items its count claims. Same for "Cedar grid study 4" against Cedar Other 1. Transcripts: /private/tmp/claude-501/-Users-nobelchang-Documents-Van-Gogh-Claude/e4098614-e7f1-43bc-8173-c3bbf247d4b1/scratchpad/coldread.txt and coldread2.txt. |
| P30 | PASS | `skill_contract.py` rc 0. Four scripts run against a real Tier 2 vault, all rc 0; five keys present on both the success and the fail-open branch of all four briefings; 19 key references across 4 SKILL.md files all exist in their script's output; render order present in all four; skills/_shared/front-page.md parses at 132 lines and names all five keys. |
| P31 | PASS | Snapshots independently re-derived from pre-change commit 5beb8cf by me (see P14): 8 of 8 byte-match by sha256, so they are not self-referential. Current code matches them (`test_buckets_md_unchanged`, `test_digest_html_unchanged` green). Test counts measured on both trees myself: 797 passed on 5beb8cf, 867 passed on HEAD, both fully green, count higher. |
| P32 | PASS | pyproject.toml:3 `version = "0.32.0"` and .claude-plugin/plugin.json:4 `"version": "0.32.0"`. `test_plugin_manifest.py` green. |

NEEDS_HUMAN (excluded from the counts)
- P25, the drafts half. No vault pointer and no refresh tokens on this machine, so no draft can reach a real Drafts folder. The exact command and what to read for are in the NEEDS_HUMAN section above; run it after `/van-gogh:install-van-gogh` authorizes one account.

Overall: FAIL (30 of 32 gradeable items PASS, 2 FAIL: P26 and P29; 1 NEEDS_HUMAN sub-check under P25).
Round 1's four failures: P4, P15 and P27 are genuinely fixed and I confirmed each independently. P29 is not fixed. P4's fix is real but its test cannot fail and should be strengthened. P26 is a new failure, not a carry-over.

### Evaluation 2026-09-04 12:35, round 3
Contract hash: verified (recomputed aa0a4ff49d74857f195c88561412b912e1b757c786fd784c44de0d4493ded2cf == stored line 5)
Graded from scratch at HEAD 8758234, clean working tree, no verdict carried forward. Full suite: 873 passed.

| Item | Verdict | Evidence |
| P1 | PASS | My own render: 4-business fixture at 120 items gives 27 front-page lines, 24 opening words, 0 item names in the opening; at 300 items 26 lines, 24 words, 0 names. Both under 45 and 60. |
| P2 | PASS | My own count over seeds 0 to 19 at 120 items: front + sum(row folded) == open on all 20, 0 mismatches. |
| P3 | PASS | Measured myself: the item bullet sits at column 22 in both render_front_page_md and render_buckets_md on the same fixture; _LABEL_COL 20, _WRAP 78, _BAND in both. Labels used are exactly the three in _BUCKET_LABELS["morning-coffee"]. Scanned every heading-shaped line across front page, rows and folds: 0 outside the configured function list. |
| P4 | PASS | Mutation-proven this round, which is what round 2 asked for. Removed the wrap branch at _right_align (app/week_review.py:2260-2262) and BOTH test_draft_line_geometry and test_right_align_never_overruns fail, with a 98-char line against _WRAP 78. Restored, both green. The fixture is now a 53-char business name plus a 41-char function name, past the boundary. |
| P5 | PASS | Two fresh builds from seed 7 at 150 items are byte-identical on front_page_md, fold_rows_md and folded_md. |
| P6 | PASS | My own run: 12 late items puts all 12 on the page, 0 folded, opening reads "12 of your 12 open items are late." |
| P7 | PASS | My own shuffle: 10 shuffles of a 90-item fixture produce 1 distinct front_page_md. Planted 4-item fixture gives tier order late, dated, judged-relevant waiting, other waiting. |
| P8 | PASS | My own run: a 90-day and a 40-day undated item are folded, the dated one leads alone; STALE_COUNTERPARTY_DAYS 30. Degraded ledger still opens "Heads up". |
| P9 | PASS | My own run: over a 90-item fixture, 0 row or front-page bucket names outside the configured display names. An unmatched item files under "Couldn't place these"; adding "zamboni" to Cedar's keywords moves it to Cedar on the next render. |
| P10 | PASS | My own planted and control fixtures: an unmatched priority fires "Sign Lumen: nothing moving"; a newly_cold alert with a bucket fires "Dana Ruiz quiet 21 days, a priority contact"; the control (priority matched, no alerts) returns []. With 4 empty priorities and 2 quiet contacts the row carries exactly 2 flags. |
| P11 | PASS | Keyword fallback with no model: invoice to Accounting, term sheet to Legal, RFP to Sales, unmatched to Other, 4 of 4. A classifier reply of "Vibes" lands in Other. Over 150-item and 90-item fixtures every item carries exactly one function, 0 outside the list. |
| P12 | PASS | My own count on seeds 0, 4, 9, 13 at 130 items: per-function totals equal the row's folded count on every business, 0 mismatches. |
| P13 | PASS | With briefing.functions = ["Deals","Paper","Other"], briefing_functions() returns exactly that and the rendered names are a subset of it. A default name no longer configured (Legal, from "Kestrel term sheet") cannot come back. |
| P14 | PASS, deviation judged JUSTIFIED | Snapshots independently re-derived, not trusted: extracted 5beb8cf with git archive, dropped in only the fixture module, re-ran freeze_snapshots.py into a temp tree. All 8 files byte-match by sha256, so they genuinely predate this work. test_fold_body_unchanged is mutation-proven: restoring the two-partition fold makes it fail on "Close Meridian" as a heading. On the deviation I measured the conflict rather than accepting the prose: under P14's literal "grouped by function under the priorities", harbor's row reads "Legal 13" while the fold's Legal heading holds 8, and "Sales 10" has no heading at all, on every business with priorities. That directly violates P12. The shipped single partition is the only shape that satisfies P12, P14's own named verify and P15 together; the priorities still lead inside their function group and still name themselves ("on Close Meridian"); and the deviation is disclosed in the punch list rather than buried. Justified. |
| P15 | PASS | Mutation-proven. live_check.py: 35 slices, 0 mismatches. Making fold_slice drop priority-matched items reproduces the round-1 bug exactly: test_open_function_slice fails with "harbor Sales: row says 8, slice pastes 0", and the live_check P15 half reports 9 mismatched slices including "northwind/Accounting: row 1, slice 0". Restored, both green. |
| P16 | PASS | My own render: workbench_data.render_markdown emits <details> with no open attribute; digest_html.md_to_email_html emits no <details>, a background:#f6f8fa block, and places the fold after the front page (offset 542 > 306). |
| P17 | PASS | Ran the real CLI, not the monkeypatch: VAN_GOGH_STATE_DIR at an empty dir printed {"no_token": true, "reason": "Missing refresh token: env var MS_GRAPH_REFRESH_TOKEN_OUTLOOK is not set..."} and exited 1. test_existing_ledger_entry_makes_zero_api_calls shows the second call returning existing: true with the call counter still at 1. |
| P18 | PASS | test_no_draft_for_dead_deal (stage=dead returns {"skipped": "terminal_deal"} with calls == [], stage=closing drafts) and test_draft_idempotent (3 rows before, 3 after a rerun with RE: prefixes) green. live_check.py P18: a rerun over the same input adds 0 ledger rows, 1 row total. |
| P19 | PASS | My own AST walk over app/draft_email.py, self-tested first against a planted client.sendMail(x): the only send-shaped hits are the module name in "from digest_send import clean_address" (line 62) and "from digest_send import build_gmail_raw" (line 96), both pure helpers, plus a local variable named sender. My own text scan hits only the docstring at lines 6-7 that explains there is no send path. A grep for .send(, sendMail, messages().send, send_message, .send_email( finds nothing but that docstring. The only API calls are client.create_draft(...) and drafts().create(...). |
| P20 | PASS | live_check.py P20: after a second run the sidecar holds exactly one morning-coffee record with the original URL, published true. |
| P21 | PASS, deviation noted | Read all four SKILL.md files: each says "read the stored URL, read that artifact with `action: \"read\"` ... and only then publish the page to that same URL" (morning-coffee:343-345 and the three twins). Mutation-proven: deleting the read clause from skills/week/SKILL.md makes test_reads_before_publish fail. The contract's parenthetical "(mock records call order)" was not built; a prose skill has no runtime to mock and a static order check is the only surface it has. The criterion's substance is met. |
| P22 | FAIL | The unavailable arm is fully proven: rc 0, full render, exactly one line "The web page did not update: this session has no publishing surface. It still shows 2026-09-03: https://claude.ai/public/artifacts/example-page-id" (p22_unavailable.txt:2). The available arm proves only half of what the criterion names. It requires "with publishing available the same run carries the link line and no failure line". No failure line: proven, page_note is ''. The link line does not exist. p22_available.txt contains zero occurrences of "http". briefing_html.py has failure_line() but no success counterpart, and page_url() (app/briefing_html.py:45) is called from exactly one place in the whole product, skills/_shared/front-page.md:109, which is step 1 reading the stored URL BEFORE republishing, not a line shown to the reader after. skills/_shared/front-page.md steps 4 and 5 say publish and record, and print a line only on failure. The four SKILL.md render orders (morning-coffee:387-391 and twins) list front_page_md, Drafts, meeting prep, folded_md, and no link. So the second captured run cannot verify a clause the product does not implement. |
| P23 | PASS | My own render: a page dated 2026-09-03 against newest 2026-09-04 carries "This page shows 2026-09-03. A newer briefing (2026-09-04) has been written since, and this page did not update."; a current page carries nothing. The digest line names both the reason and the date the page still shows, and is wired at digest_send.py:230-235 (failure_line appended to the body inside a try/except that fails open). |
| P24 | PASS | Measured the bare :root myself rather than the test's @media split: 13 var() tokens used on a real 120-item page, all 13 defined in the first :root block, which precedes any @media, 0 undefined. 4 <details> tags, 0 carrying open. voice_check's own 37 developer words and 24 AI tells run against the page's visible text (scripts and styles stripped, tags removed, entities unescaped): 0 hits. |
| P25 | PASS | Both halves run by me. live_check.py: rc 0, treatment 7 items / 7 non-late / 4 businesses named; the control (front_page_cap absent, front-page keys stripped) fails the identical check on "has DO TODAY" and "at most 7 non-late items". skill_live_run.py spent two real claude -p calls on the shipped SKILL.md: treatment 7/7/4 passes all three, control renders 101 items and fails the same two. The drafts half stays NEEDS_HUMAN by the contract's own wording, and the exact command plus what to read for is written into the punch list's NEEDS_HUMAN section, which is what that clause asks for. |
| P26 | PASS | Mutation-proven. Restoring the four-backslash over-escape at app/briefing_html.py:266 makes test_page_redaction AND test_redaction_covers_every_platform_this_ships_to both fail on C:\Users\someone\x\y.md. Restored, green. My own scan, not the test's: 14 planted strings (6 credential shapes, a JWT, an MS Graph refresh token, POSIX, Linux and two Windows paths) leak 14 of 14 in the control and 1 of 14 after redact(). The one residual is a lowercase c:\users\ path, which is outside the criterion's three named shapes and is not the casing Path.home() produces on Windows; noted as a hardening gap, not a failure. The production path does redact: skills/_shared/front-page.md:116 builds the page as b.render_page(b.redact(md), ...). |
| P27 | PASS | Mutation-proven. dash_scan.py exits 0: self-test finds a planted em and en dash, 4658 added lines scanned, 0 carry a dash. Planting an em dash in skills/morning-coffee/SKILL.md makes it print FAIL and name the line. My own scan with NO exemptions over git diff 5beb8cf...HEAD: 4897 added lines, exactly 1 carries a dash, and it is the round-1 evaluation row in this punch list quoting the dash it found. The digest_send.py:115 em dash is pre-existing, not in the added set. |
| P28 | PASS | voice_check.py rc 0 over 12 rendered outputs including front/page, front/rows, front/folded, front/quiet and front/slice. The banned list carries all seven named words (unassigned, bucket_tag, judged, sidecar, degraded, rank, fold) at voice_check.py:23-33. Proved non-vacuous myself by planting a 13th render reading "the rank of this fold is degraded, let me delve into the sidecar": rc 1, 4 developer hits and 2 AI-tell hits. |
| P29 | FAIL | The criterion has three clauses and the third is not met. I re-rendered the cold-read input myself and confirmed p29_full_briefing.txt is byte-identical to what HEAD produces, then ran two fresh claude -p agents given only that text, no repo, no tools. Both name "Lumen redline v4 98" first, which is rank 2, so clause 1 passes. Both name a folded item to skip ("Summit travel booking 17" and "Summit travel booking 8", both in the folds at briefing lines 257 and 256), so clause 2 passes. Clause 3 fails: run A reports CONTRADICTIONS: 4, run B writes CONTRADICTIONS: 2 in the required field before talking itself down to 0. The one that is real, and that the punch list itself concedes, is run A's second: "Cedarworks Q3 invoice 57 ... due 2026-09-04" is labelled "Older, still open" while items 4 to 23 days old sit under "Waiting on you". The label is assigned by source section, not age, so two items of identical age carry opposite labels. The proof package's own p29_cold_reads.md records the same finding at round 5 and states "it is NOT fixed". Separately, the contract's verify artifact is "transcript in proof"; what is there is a builder-written five-round summary, not a transcript of any agent run. My transcripts: <scratchpad>/coldread_A.txt and coldread_B.txt. On the deferral: it is honestly disclosed and the tension is real, since "Older, still open" is in 4 of the 8 frozen snapshots (buckets_week_md, buckets_week_terminal, digest_morning-coffee, digest_week, digest_week-retro all carry it), so renaming it does break P31 as written. That makes this a contract conflict for Nobel to resolve, but it does not make the criterion met, and I do not soften a failing criterion. |
| P30 | PASS | skill_contract.py rc 0: four scripts against a real Tier 2 vault all rc 0; five keys present on the success and the fail-open branch of all four; 19 key references across 4 SKILL.md all exist in their script's output; render order present in all four; skills/_shared/front-page.md 132 lines, all five keys named. Mutation-proven: renaming the fold_rows_md key in build_front_page makes it exit FAIL naming afternoon_tea.py and week_retro.py. |
| P31 | PASS | Snapshots independently re-derived from 5beb8cf by me (see P14): 8 of 8 byte-match by sha256, so the control is not self-referential. Current code matches them (test_buckets_md_unchanged, test_digest_html_unchanged green). Test counts measured on both trees myself: 797 passed on the extracted 5beb8cf tree, 873 passed on HEAD, both fully green, count higher. |
| P32 | PASS | pyproject.toml:3 version = "0.32.0" and .claude-plugin/plugin.json:4 "version": "0.32.0". test_plugin_manifest.py 10 passed. |

NEEDS_HUMAN (excluded from the counts)
- P25, the drafts half. No vault pointer and no refresh tokens in ~/.config/van-gogh/, so no draft can reach a real Drafts folder here. The exact command and what to read for are in the NEEDS_HUMAN section above; run it after /van-gogh:install-van-gogh authorizes one account.

Mutation audit requested for round 3: all four tests can fail. P4 (remove the _right_align wrap branch: 2 tests fail), P15 (make fold_slice drop priority-matched items: test_open_function_slice fails and live_check reports 9 bad slices), P26 (restore the four-backslash Windows escape: 2 tests fail), P27 (plant an em dash in a SKILL.md: dash_scan names it and exits nonzero). Also mutation-proved P14, P21 and P30 unprompted.

Overall: FAIL (30 of 32 gradeable items PASS, 2 FAIL: P22 and P29; 1 NEEDS_HUMAN sub-check under P25).
Round 2's two failures: P26 is genuinely fixed and mutation-proven. P29 is not fixed. P22 is a new failure, not a carry-over, and was passed in rounds 1 and 2 on the unavailable arm alone.

### Evaluation 2026-09-04 12:59, round 4
Contract hash: verified (aa0a4ff49d74857f195c88561412b912e1b757c786fd784c44de0d4493ded2cf, recomputed = stored). Graded from scratch; nothing carried forward from rounds 1 to 3.

| Item | Verdict | Evidence |
| P1 | PASS | Rendered it myself, not via the test. Fixture is 4 businesses (`harbor northwind cedar personal`). 120 items: 27 lines, 24 opening words, 0 named items. 300 items: 27 lines, 18 words, 0 named. Bound is 45 lines / 60 words / 3 names. `test_fits_one_screen` green. |
| P2 | PASS | Recounted conservation independently over seeds 0-19 at 60 items: front + sum(row folded) == open on all 20, and I re-derived `folded` from the buckets by key rather than trusting `counts`, plus re-derived `open` as sum(len(bucket items)). 0 mismatches. |
| P3 | PASS | My own measure: front page and `render_buckets_md` both put the item bullet at column 22 exactly; a terminal `fold_slice` body also lands at 22. `_BAND` in both. Label heads on the page are exactly `{Late, Waiting on you, Waiting on them}` = `_BUCKET_LABELS["morning-coffee"]`, no fourth. |
| P4 | PASS | Fixed and mutation-proven. The fixture now configures `functions: ["Operations, Facilities And Field Services", "Other"]` AND sets `entry["function"]` the way the classifier does, so the long name really reaches the page: I rendered it by hand and got a 98-char tag wrapping to two lines, longest rendered line 77 against `_WRAP` 78. Mutation: deleting the `len(right) > width - indent` wrap branch in `_right_align` (week_review.py:2268) fails both `test_draft_line_geometry` and `test_right_align_never_overruns`. |
| P5 | PASS | Two builds from fresh `sample_sections()` byte-equal on front_page_md, fold_rows_md, folded_md, run by hand. |
| P6 | PASS | Built it myself: 12 late items with cap 7 puts all 12 on the page, `counts["late"]` = 12, opening reads "12 of your 12 open items are late." |
| P7 | PASS | Ran 10 shuffles of an 80-item fixture myself: identical (subject, why) sequence every time. `test_rank_tiers` pins late > dated > judged-relevant waiting > other waiting. |
| P8 | PASS | My own render: the 90-day undated item is off the page and counted in its row (stale 1, folded 1), the 9-day one is on. `test_degraded_keeps_dated_first` green and the degraded ledger still carries "Heads up". |
| P9 | PASS | Over a 90-item fixture, every fold row display name and every front-page bucket name is a configured display name or "COULDN'T PLACE THESE": 0 outside. `test_file_under_sticks` green (adding "zamboni" moves the item from unassigned to cedar). |
| P10 | PASS | The shipped control only disproves "quiet", so I built the missing one: with every configured priority matched by an item, `fold_rows` flags come back `[[]]`, so "nothing moving" does not fire on its control either. Flag cap of two holds. |
| P11 | PASS | With the model off I ran the keyword table myself: invoice -> Accounting, term sheet -> Legal, RFP -> Sales, unmatched -> Other; a classifier reply of "Vibes" lands in Other. On seeds 1/2/3/9 at 100 items every folded item's function is inside `briefing_functions()`. |
| P12 | PASS | Recounted per-function totals straight from the bucket items on seeds 1, 2, 3, 9: the row's function dict equals my Counter exactly and sums to `row["folded"]` every time. 0 mismatches. |
| P13 | PASS | Set `briefing.functions = ["Deals","Paper","Other"]` myself over an 80-item fixture: rendered names are `{Other}`, a subset of the override, and "Legal" appears in neither `fold_rows_md` nor `folded_md`. |
| P14 | PASS on the verify step it names, with the deviation the punch list already states. I did not trust the snapshots: I extracted 5beb8cf with `git archive`, dropped in only `briefing_fixtures.py` and `freeze_snapshots.py`, and regenerated all 8. Every one matches the working copy after substituting only the third label, so the frozen files genuinely predate this work. `test_fold_body_unchanged` green. The stated departure (fold grouped by function only, priority named on the item line rather than as a heading) is real and is Nobel's call; I read it as satisfying "grouped by function under the priorities" but say plainly that the other reading is available. |
| P15 | PASS | Mutation-proven both ways. Live: `live_check.py` ran 35 real slices through the SKILL.md one-liner over a Tier 2 sidecar, all four businesses including the two with priorities, 0 mismatches between a row's count and what `fold_slice` pastes. Mutation: adding `not e.get("priority_matched")` to the `fold_slice` filter (the original bug) fails `test_open_function_slice` (`assert 0 == 8`) and makes the live check report 9 bad slices across harbor and northwind. The captured run P15 also asks for exists at live/p15_slices.txt. |
| P16 | PASS | `test_callout_details` (workbench: `<details><summary>` with no `open`, `+` opens it) and `test_callout_block` (digest: no `<details>`, `background:#f6f8fa`, ordered after the front page) green. I also rendered a real 140-item page: 4 closed `<details>`, 0 `<details open>`. |
| P17 | PASS | Not just the stub. I ran the real CLI against a genuinely empty state dir (`VAN_GOGH_STATE_DIR` at a fresh temp dir) and got `{"no_token": true, "reason": "Missing refresh token: env var MS_GRAPH_REFRESH_TOKEN_OUTLOOK is not set..."}` with no ledger written. The `{ok, existing}` half is proven by a call counter that stays at 1 across two `draft_once` calls on the same thread. |
| P18 | PASS | `test_no_draft_for_dead_deal` (stage=dead returns `{"skipped": "terminal_deal"}` with zero draft calls, stage=closing drafts) and `test_draft_idempotent` green. Live: `live_check.py` seeded the ledger, re-ran the real `morning_coffee.py` against the Tier 2 vault and read the ledger back byte-identical, 0 new rows. |
| P19 | PASS | AST walk of `draft_email.py` that I wrote myself finds 0 calls whose name contains "send". The `send_email`/`digest_send` strings live only in the module docstring, which the test's `_code_only` strips; the test's own scanner self-test (planting `client.sendMail(body)`) proves it can find one. |
| P20 | PASS | `test_url_reused` green. Live: a second real `morning_coffee.py` run left `briefing_pages.json` byte-identical with exactly one entry. |
| P21 | PASS | All four SKILL.md files order `action: "read"` before "publish the page", and `_shared/front-page.md` steps 1-4 read the stored URL, read the artifact, build, and only then publish. Mutation-adjacent check: `skill_contract.py` catches a dropped key (below), and the test asserts read index < publish index rather than mere presence. |
| P22 | PASS | Both arms captured live, and the available arm really carries a link. Unavailable: rc 0, full render, `page_note` = "The web page did not update: this session has no publishing surface. It still shows 2026-09-03: https://claude.ai/public/artifacts/example-page-id". Available: `page_note` = "The page is up to date: https://claude.ai/public/artifacts/example-page-id", no failure line. Mutation: changing `link_line` to return "The page is up to date." without the URL makes the live check report `NO control: carries the link line`. |
| P23 | PASS | `test_stale_banner` (page dated behind `newest_date` says "did not update", current page does not) and `test_digest_stale_line` green. I rendered a real page with `newest_date` ahead and confirmed the banner fires. `digest_send.py:233` appends `page_line` to the email body. |
| P24 | PASS | Over a real 140-item page: every `var(--token)` used is defined in the bare `:root` (0 undefined), 4 closed `<details>`, 0 open. Both themes and the toggle present. The only banned word anywhere in the HTML is `fold` inside the CSS class name `foldbody`, never in reader-visible text. |
| P25 | PASS on both gradeable halves, each with a control that fails. Script half: the real `morning_coffee.py` on a 4-business 100-item Tier 2 `--input` fixture, rc 0, DO TODAY has 7 non-late items and all 4 businesses named in the rows; the pre-change `buckets_md` control fails the identical check on 2 of 3 clauses. Model half: the real `skills/morning-coffee/SKILL.md` through `claude -p` renders 7 non-late items and 4 rows; the control (front-page sections stripped from the skill and the five keys stripped from the payload) renders 100 items and fails. Real Drafts-folder creation stays NEEDS_HUMAN, as the contract itself carves out. |
| P26 | PASS | Mutation-proven twice. The planted control leaks and the redacted page does not, across an OAuth token, four API-key shapes, a credential assignment and three platform paths. Mutation A: restoring the four-backslash Windows escape fails `test_page_redaction` and `test_redaction_covers_every_platform_this_ships_to`. Mutation B: deleting the `ya29|1//` rule fails `test_page_redaction` with the token leaked verbatim. |
| P27 | PASS | Mutation-proven twice. `dash_scan.py` self-tests, scans 4774 added lines against merge-base 5beb8cf (the real pre-change commit, 7 commits back) and 5 rendered strings: 0 dashes. My own independent scan of the same range found 5065 added lines and exactly 1 dash, in the punch list evaluation log, which is the one file the scanner exempts and legitimately so. Mutation A: an em dash planted on a working-tree line is named and exits 1. Mutation B: an en dash inside `"THE REST"` is caught as "fold_rows_md contains an en dash". |
| P28 | PASS | `voice_check.py` exits 0 over 11 rendered outputs including `front/page`, `front/rows`, `front/folded`, `front/quiet` and `front/slice`. The banned list contains all seven the contract names: unassigned, bucket_tag, judged, sidecar, degraded, rank, fold. |
| P29 | FAIL | Regenerated `p29_full_briefing.txt` with `cold_read_input.py` and ran two fresh cold reads over the current briefing, full folds included. Both named the rank-2 item first ("Lumen redline v4 98 ... due 2026-09-04") and both named a folded item to skip, so clauses 1 and 2 pass. Clause 3 fails: each read reported SIX contradictions, not zero. I checked every number myself and the briefing is internally consistent (19+27+18+29 = 93, actual fold bullets equal declared counts per business, every function count equals the items behind its heading), so most of what they reported is reader arithmetic error. But one finding is real, reproducible across both reads, and is what caused three of the six in read 1 and one in read 2: the business row prints a bare `(N waiting)` with no direction. `week_review.py:2419` renders `f" ({fn['waiting']} waiting)"` and `week_review.py:2209` fills it from `_is_waiting`, meaning waiting-on-YOU. Read 1 concluded "The parenthetical means the opposite of what it means everywhere else"; read 2 concluded "I cannot tell which direction it means, and the two readings imply completely different days." Both ended on "I don't trust the counts in this briefing." This is the same defect class as the "Older, still open" label just fixed: a bare word next to a number, with the direction left to the reader. |
| P30 | PASS | `skill_contract.py` exits 0: four real Tier 2 subprocess runs, all four scripts emit the five keys plus a fail-open branch, 22 key references across 4 skills all present in the emitting script's real output, all four SKILL.md files carry the render order. Mutation: deleting `"page_note": _page_note(),` from `week_retro.py` makes it exit 1 with "week-retro renders 'page_note', week_retro.py never emits it". |
| P31 | PASS, and the re-baseline is honest and minimal. I verified the claim rather than reading it: the snapshots are not in 5beb8cf (they were first committed in 6edf03c), so I rebuilt the control from scratch. `git archive 5beb8cf` into a temp tree, plus only `briefing_fixtures.py` and `freeze_snapshots.py`, regenerated all 8 files from pre-change code. Substituting only "Older, still open" -> "Waiting on them" (with "Older, still open:  " -> "Waiting on them:    " for the terminal form) makes all 8 byte-equal to the working copy. `git diff 6edf03c b183054 -- tests/snapshots/` is 5 files, 10 lines, every one of them that single string and nothing else. Padding confirmed in code: `_LABEL_COL` = 20, "Older, still open:" is 18 + 2, "Waiting on them:" is 16 + 4, both land on 20, so no column shifted. Suite is 876 green against the 797 baseline. |
| P32 | PASS | `pyproject.toml` version = 0.32.0, `.claude-plugin/plugin.json` version = 0.32.0, `test_plugin_manifest.py` 10 passed. |

Mutation audit, as requested. All five named tests can fail: P4 (delete the `_right_align` wrap branch -> 2 tests fail), P15 (drop priority-matched items in `fold_slice` -> unit test fails and the live check reports 9 bad slices), P22 (strip the URL from `link_line` -> the live control arm fails), P26 (two independent mutations, each fails the redaction test), P27 (two independent mutations, each named and exit 1). Also mutation-proved P30 unprompted.

Overall: FAIL (31 of 32 gradeable items PASS, 1 FAIL: P29; 1 NEEDS_HUMAN sub-check under P25).
P22 is genuinely fixed: both arms are captured and the available arm carries a real link, mutation-proven. P31's re-baseline is honest, minimal, and I reproduced the pre-change control from source rather than trusting the claim. P29 remains open, and the cause has moved: the "Older, still open" label is fixed and no cold read raised it again, but `(N waiting)` in the business rows is the same defect one column over.

### Evaluation 2026-09-04 13:43, round 7
Contract hash: verified (recomputed aa0a4ff49d74857f195c88561412b912e1b757c786fd784c44de0d4493ded2cf = stored line 5)
Graded from scratch, nothing carried forward. Suite: 882 passed. Pre-change tree re-extracted from 5beb8cf: 797 passed.

| Item | Verdict | Evidence |
| P1 | PASS | My own render, not the test. seed 7, 120 items: front_page_md 27 lines, opening 35 words, 0 named items. 300 items: 28 lines, 35 words, 0 named. Under 45 / 60 / 3 both. |
| P2 | PASS | My own loop, seeds 0-19 at 90 items: front + sum(row folded) == open on all 20, 0 mismatches. |
| P3 | PASS | Measured myself: the item bullet sits at column 22 in both render_front_page_md and render_buckets_md on one fixture. _LABEL_COL 20, _WRAP 78, _BAND present in front page, rows and ledger. Every label head across all four renders: exactly {Late, Waiting on you, Waiting on them} = _BUCKET_LABELS["morning-coffee"]. No fourth. |
| P4 | PASS | test_draft_line_geometry green and mutation-proven: deleting the `len(right) > width - indent` wrap branch in _right_align fails it. Restored. |
| P5 | PASS | Built twice from one bucket list: front_page_md, fold_rows_md, folded_md all byte-identical. |
| P6 | PASS | 12 planted late items against cap 7: all 12 reach the page (front=12, folded=48), opening reads "12 of your 60 open items are late". |
| P7 | PASS | Ten shuffles of a seeded 80-item fixture with different shuffle seeds: front_page_md + fold_rows_md identical every time. |
| P8 | PASS | A 400-day item never reaches the page while a 2-day dated one does. test_stale_never_front and test_degraded_keeps_dated_first green. |
| P9 | PASS | Row headings on a real fixture are exactly the four configured display names. An unmatched "Zamboni resurfacing quote" lands under tag `unassigned` / "Couldn't place these". test_file_under_sticks green. |
| P10 | PASS | My own plant and two controls. Planted: "Close the widget deal: nothing moving" and "Ray Okafor quiet 31 days, a priority contact". Control alerts={}: the quiet flag is gone. Control with both priorities moving: [] flags. Max flags per row 2. |
| P11 | PASS | tests/test_assign_function.py green. The four the contract names asserted twice (invoice->Accounting, term sheet->Legal, RFP->Sales, unmatched->Other); a reply of "Vibes" lands in Other. |
| P12 | PASS | My own check, seeds 1-5 at 120 items: every row's per-function counts sum to its folded count, 0 mismatches. |
| P13 | PASS | ["Deals","Paper","Other"] renders only those names and a retired default cannot come back (test_functions_override, test_other_is_always_available). |
| P14 | PASS | Provenance reproduced, not read. git archive 5beb8cf into a temp tree plus only briefing_fixtures.py and freeze_snapshots.py, regenerated all 8 snapshots from pre-change code: 3 byte-identical, 5 differ by 20 lines, and 0 of those 20 is anything but the disclosed "Older, still open" -> "Waiting on them" substitution. test_fold_body_unchanged green. The declared deviation on the sentence's second half stands as declared; it is Nobel's call. |
| P15 | PASS | Three ways. test_open_function_slice green; mutation-proven (replacing the function filter with `list(folded)` fails it); live_check captured 35 slices, every one pasting exactly its row's count, 0 mismatches. |
| P16 | PASS | My own render: page HTML has 4 `<details>`, 0 carrying `open`. The digest render has no `<details>` and a shaded block, and the front page precedes it. |
| P17 | PASS | Ran the real CLI against a fresh empty VAN_GOGH_STATE_DIR: printed `{"no_token": true, "reason": "Missing refresh token: env var MS_GRAPH_REFRESH_TOKEN_OUTLOOK is not set...", "key": "outlook|test co|x"}` and wrote no ledger. test_existing_ledger_entry_makes_zero_api_calls covers the second half. |
| P18 | PASS | test_no_draft_for_dead_deal and test_draft_idempotent green; live_check's rerun over the same input left the ledger at 1 row. |
| P19 | PASS | My own AST walk over app/draft_email.py: 0 send-shaped calls. Every literal "send" in the file is a docstring or the digest_send import of clean_address/build_gmail_raw. |
| P20 | PASS | test_url_reused green; live_check's second run left briefing_pages.json with exactly one entry and the same URL. |
| P21 | PASS, with a note | I checked all four SKILL.md by hand: `action: "read"` precedes the publish step in every one (morning-coffee 341/343, afternoon-tea 278/280, week 324/326, week-retro 189/191), and _shared/front-page.md step 2 reads "read that artifact first" ahead of step 4 "Only now publish". The contract's parenthetical "(mock records call order)" does not describe the test: read and publish are model artifact actions, not script calls, so there is no mock. The criterion is verified; the parenthetical is wrong. |
| P22 | PASS | Both arms captured live. Unavailable: rc 0, full render, one line "The web page did not update: this session has no publishing surface. It still shows 2026-09-03: https://claude.ai/public/artifacts/example-page-id". Available: "The page is up to date: <url>", no failure line. Mutation-proven twice: link_line stripped of the URL makes the live check report NO on "control: carries the link line"; failure_line stripped of the reason makes it report NO on "carries one line naming why". |
| P23 | PASS | My own render: a page dated 2026-09-03 against newest_date 2026-09-04 carries "did not update" and names 2026-09-03; a current page carries neither. test_stale_banner and test_digest_stale_line green. |
| P24 | PASS | My own scan of a real 140-item page: 13 var() tokens used, all 13 defined in the bare :root, 0 undefined. 4 `<details>`, 0 open. voice_check.py passes over front/page. |
| P25 | PASS on both gradeable halves, each with a control that fails. Script half: live_check on a 4-business 100-item Tier 2 `--input` fixture, rc 0, DO TODAY 7 items / 7 non-late / 4 businesses; control (front_page_cap absent) fails the identical check on "has DO TODAY" and "at most 7 non-late items". Model half: skill_live_run spent two real `claude -p` calls on the shipped skills/morning-coffee/SKILL.md, treatment 7/7/4 passes all three, control renders 100 items and fails the same two. Drafts-folder creation stays NEEDS_HUMAN: I confirmed ~/.config/van-gogh holds only claude-cli-update.json and the two OAuth app-credential files, no .env and no vault-pointer. |
| P26 | PASS | Mutation-proven three ways, each restored. redact() made a no-op: test_page_redaction and test_redaction_covers_every_platform_this_ships_to both fail. The four-backslash Windows over-escape restored: both fail. The first `ya29|1//` rule deleted: test_page_redaction fails with the token leaked. |
| P27 | PASS | dash_scan.py exits 0: self-test finds a planted em and en dash, 4954 added diff lines scanned, 0 carry a dash, 5 rendered strings clean. Mutation-proven twice: an em dash planted on an uncommitted added line is named and the scan exits FAIL; an en dash planted inside _BAND is caught both as a diff line and inside front_page_md, fold_rows_md and page_html. |
| P28 | PASS | objectives/ship-ready-review/voice_check.py rc 0. The banned list carries all seven the contract names (unassigned, bucket_tag, judged, sidecar, degraded, rank, fold) and the renders include the front page, the rows and the folds. |
| P29 | PASS | Regenerated p29_full_briefing.txt with cold_read_input.py (262 lines, front page + rows + folds) and ran three fresh cold reads on that text alone, folds included, no repo and no tools. Clause 1: all three named "Lumen redline v4 98 · Ana Beltre · due 2026-09-04", the second item on the page, so rank 2. Clause 2: all three named "Harbor budget review 92 · from Dana Ruiz · 10 days ago", which is at briefing line 60 inside the Harbor Solar fold. Clause 3, graded on the product: I verified every number in the file myself before reading any transcript, and found no contradiction. Rows 19+27+18+29 = 93, 93+7 = 100; every fold header equals its bullet count (19/27/18/29); all 31 per-function brackets, including the "(N on you)" direction, match the items behind their heading exactly, 0 mismatches; "18 of 22" equals folded plus the 4 Cedar items promoted, and the same holds for 27 of 28, 29 of 31 and Harbor's bare 19 with nothing promoted; the opening's "3 need a reply from you, 4 are waiting on someone else" matches the 3 and 4 labels on the page. Reader counts: 2 of 3 reported 0 contradictions in their final answer (read 1 outright, read 2 after working each candidate and withdrawing all of them). Read 3 reported 2, both the same error: it assumed a row's function breakdown partitions the whole business rather than the folded count, so it read "Accounting 3" against 22 and "Legal 6" against 28. That assumption is contradicted twice in the document itself, by "The other 93 are filed below by business and function" and by "THE REST, 93 items", and the brackets sum to the first number in "18 of 22". Verdict: no verifiable contradiction remains in the product, and read 1 alone satisfies all three clauses in one run, so I grade the criterion met. Transcripts: <scratchpad>/cold1.txt, cold2.txt, cold3.txt. |
| P30 | PASS | skill_contract.py rc 0: four scripts run against a real Tier 2 vault, five keys on the success and fail-open branch of all four, 22 key references across four SKILL.md all present in the emitting script's real output, render order in all four, _shared/front-page.md 142 lines naming all five keys. |
| P31 | PASS | Independently re-derived, not read. All 8 snapshots regenerated from an extracted 5beb8cf tree: 3 byte-identical, 5 differ by 20 lines total and 0 of those 20 is anything but the disclosed label substitution. Padding confirmed in code: _LABEL_COL 20, "Older, still open:" 18 chars, "Waiting on them:" 16, both land on 20, so no column moved. Test counts measured on both trees myself: 797 on 5beb8cf, 882 on HEAD, both fully green. |
| P32 | PASS | pyproject.toml:3 `version = "0.32.0"`, .claude-plugin/plugin.json:4 `"version": "0.32.0"`, test_plugin_manifest.py green. |

Overall: PASS (32 of 32 gradeable items PASS; 1 NEEDS_HUMAN sub-check under P25, the Drafts-folder creation the contract itself carves out)

Mutation audit, all nine requested, each restored and the tree left clean at b337889 with 882 passing:
P4 (_right_align wrap branch deleted -> fails), P15 (function filter replaced with the whole folded list -> fails), P22 (link_line loses the URL -> live control arm NO; failure_line loses the reason -> live treatment arm NO), P26 (redact no-op, four-backslash Windows over-escape, first token rule deleted -> all three fail), P27 (em dash on an uncommitted added line; en dash in _BAND -> both named), the row-direction test (bare "(N waiting)" restored -> fails), the opening-split tests (split disabled -> 2 fail; on_you/on_them swapped -> all 3 fail), the "18 of 22" test (bare folded count -> fails), and the note-consistency test (the already-in-monitor stamp removed -> fails; the stamp made unconditional -> fails).

The two changes since round 6 both do what they claim. "CEDAR 18 of 22" replaces the bare count and all three readers this round parsed the rows correctly, where three of five read the bare count as a business total. `demoted_because` is now stamped on both paths by the same relevance test, and the test proves both halves: an item already in cold_monitor gets the note, and an item that is about a stated priority does not.
