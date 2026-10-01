# Punch List: permanent fix for Google `invalid_scope` after an update (Van Gogh 0.75.2)

Created: 2026-09-17 11:00 (from datetime.now())
Status: evaluated-pass
Contract-SHA256: bb85740ff450adcf7731db442160a81161d4717fc6291862504b2c309cef8e1f
Slug: `google-scope-refresh-fix`. On approval this file is copied to `project-van-gogh/objectives/google-scope-refresh-fix/punchlist.md` and frozen by hash.
Red team: ran, 1 scenario + 14 weak rows + 7 gaps. It changed 9 items (see "Red-team changes" at the bottom).
Interview answers: publish after PASS; commit 0.75.1 first; Microsoft structural fix now; Drive scope left alone, flagged only.

## Context

A client ran `/van-gogh:week`; Calendar and Gmail both failed with `invalid_scope` and the briefing came back empty.

- `app/google_client.py:97-108` builds `Credentials(token=None, ..., scopes=GOOGLE_SCOPES)`. google-auth 2.57.1 puts that list in every refresh request (`oauth2/_client.py:510`); its docstring says all of them must already be authorized for the token.
- 0.68.0 (commit `a2f128b`) added 2 Contacts scopes. Every token minted earlier was never granted them, so Google rejects the whole refresh and Gmail plus Calendar die with it. Auto-update never re-runs install, and the release note never said to sign in again.
- It stayed hidden because 2 tests assert the wrong rule (`tests/test_page_actions.py:186-189`, `tests/test_contact_capture.py:227`: the client must carry every consent scope) and nothing ever exercised a refresh.
- The watcher's auth message says "sign in to Claude again" for every auth failure, including Google and Microsoft ones (`app/job_watch.py:569`).
- Microsoft has the same shape: `microsoft_client.py:119` sends the live list on every refresh.

## The fix

Scopes are named at the consent screen and nowhere else. Google refresh sends no `scope` field, so Google returns whatever the user granted. Microsoft (MSAL requires a list) refreshes with the scopes that account was actually granted, stored at sign-in, falling back to a frozen baseline equal to today's list. A future scope addition then breaks nobody: the one feature that needs it gets a 403 and asks for a fresh sign-in (`contact_capture.SCOPE_HELP` and `send_email._is_missing_send_scope` already do this), everything else keeps running. The client recovers with no browser step once 0.75.2 is published.

```
BEFORE                                   AFTER
consent: scopes = list v1                consent: scopes = oauth_scopes.*   <-- only place scopes are named
      |                                        |
      v                                        v
refresh token (granted v1)               refresh token (+ MS: granted list stored beside it)
      |                                        |
plugin update -> list v2                 plugin update -> list v2
      |                                        |
      v                                        v
refresh sends scope=v2                   Google: refresh sends NO scope
      |                                  MS: refresh sends stored-granted or frozen baseline
      v                                        |
invalid_scope, ALL Google calls dead           v
graded "unknown", retried                gmail / calendar / graph work
"sign in to Claude" (wrong act)                |
                                         new feature call -> 403 -> that feature alone says
                                         "sign in again for <account>"
                                         [HUMAN GATE: browser consent via /van-gogh:add-account]

Run gates: [HUMAN: approve this contract] -> build -> independent evaluator -> proof -> /base-publish -> [HUMAN: client confirms]
```

Build order: fix the 0.75.1 identity leak and commit 0.75.1; live repro; `app/oauth_scopes.py`; Google constructor; Microsoft granted-scope storage; watcher and briefing wording; tests; docs, version, changelog; evaluate; proof; publish; lessons.

## Contract

- [ ] P1. The inherited 0.75.1 work is committed on its own, green. | verify: `git log --oneline -3` shows a 0.75.1 meeting-ingest commit that touches no OAuth file; `tests/test_safety_denylist.py` passes (today it FAILS on `tests/test_meeting_ingest.py:230`, a real company name planted by that work; the fixture gets an invented name) | proof: git log + test output
- [ ] P2. Live control, real Google: credentials built the OLD way with the consent list plus 1 valid scope the token was never granted fail to refresh with `invalid_scope`; in the same run the fixed `google_client._credentials()` refreshes and a Calendar list call succeeds. No token value printed. | verify: run `objectives/google-scope-refresh-fix/live_refresh_probe.py` with `.venv/bin/python`; expect both lines, exit 0 | proof: probe stdout + the captured error text saved as a fixture
- [ ] P3. The Google refresh request carries no `scope` field. | verify: `tests/test_google_refresh_scopes.py` drives the REAL google-auth `refresh()` from `_credentials()` through a fake transport and asserts on the captured POST body; it appears BY NAME in the passed list, not skipped | proof: pytest -v output
- [ ] P4. Standing guard: no code under `app/` constructs Google credentials with scopes, by keyword or position, and only `google_client` constructs them at all. | verify: an AST test in the suite passes; evaluator greps `Credentials(`, `from_authorized_user` across `app/`, `skills/`, `../templates/`, `../clients/` and lists every hit | proof: test output + hit list
- [ ] P5. Mutation: in a COPY of the plugin, reverting the constructor to `scopes=GOOGLE_SCOPES` makes BOTH the P3 and P4 tests fail. The real tree is never mutated. | verify: evaluator makes the copy, applies the edit, runs the 2 tests, expects nonzero | proof: failing output
- [ ] P6. One definition. Google scope strings live in exactly 1 file under `app/`, Microsoft ones in exactly 1; `auth_bootstrap`, `google_client`, `microsoft_client` import them (`is` identity). The 2 wrong-invariant assertions are gone, replaced by "the consent list contains the scope". | verify: `grep -l "auth/gmail.modify" app/*.py` = 1 file; `grep -l "Mail.ReadWrite" app/*.py` = 1 file; identity asserts in the suite; `grep -n "<= set(google_client" tests/` = 0 | proof: grep output
- [ ] P7. Microsoft structural fix: sign-in stores the account's granted scopes beside its token; refresh uses the stored list, else a frozen baseline equal to today's list, never the live consent list; reserved scopes (`openid`, `profile`, `offline_access`) are filtered. | verify: unit tests for all 3 paths, plus a test that appending a scope to the consent list leaves the refresh scopes of an existing account unchanged, which FAILS in a copy where refresh reads the live list | proof: pytest output + mutation output
- [ ] P8. Live, real Microsoft: the fixed client refreshes the developer's existing Microsoft account (no stored list, so baseline path) and one Graph read succeeds. | verify: probe script second section, exit 0, no token printed | proof: probe stdout
- [ ] P9. Live end to end through the real entry point, falsifiable. Two COPIES of the plugin both get 1 never-granted scope appended to the Google consent list (the client's condition). Pre-fix copy: `app/week_review.py` run as a subprocess reports `invalid_scope` for the Google account. Fixed copy: no `invalid_scope`, no auth-class error for any Google or Microsoft account, calendar events present. | verify: evaluator runs both subprocesses against the real accounts and reads the JSON `errors` and `calendar` keys | proof: error lists and COUNTS only, never mailbox content
- [ ] P10. Stale failures do not send the client to a browser. A ledger seeded with a pre-fix failed row carrying `invalid_scope` gets a re-run verdict from the watcher, never a sign-in sentence, because the new build cures it on retry. `invalid_scope` is deliberately NOT added to the no-retry auth pattern, with a comment saying why. | verify: unit test on `job_watch.actionable()` with the P2 verbatim text; `failure_class.classify` of that text is not `auth` | proof: pytest output
- [ ] P11. Watcher wording. A held auth row whose text carries a Google or Microsoft OAuth marker names the provider, names the account label when the row carries one, points to `/van-gogh:add-account`, and does not contain "sign in to Claude". A Claude login failure still gets the Claude sentence. | verify: unit tests, 3 cases minimum (Google with label, Microsoft, Claude) | proof: pytest output
- [ ] P12. Briefing auth lines. When a per-account mail or calendar fetch raises an auth-class error, the briefing `errors` entry is one plain sentence naming the account label and `/van-gogh:add-account`, carries the exception type and OAuth error code only, and still classifies as `auth`. Raw exception text never reaches it. Non-auth errors keep today's format. | verify: tests plant an exception holding a secret-looking string at the account fetch sites of `week_review`, `afternoon_tea` and `week_retro`, assert the string is absent from the whole output and `classify(line) == "auth"`; evaluator reads the full `errors.append` list (53 sites today) and FAILS the item if any site guarding a Google or Microsoft fetch still formats the exception raw for the auth class | proof: pytest output + evaluator's site table
- [ ] P13. A missing scope degrades one feature only. A People API 403 (insufficient scope) surfaces contact capture's own sign-in sentence, classifies `auth` (held, not retried), and leaves mail and calendar untouched. | verify: unit test with the 403 text; evaluator confirms contact capture shares no try block with a mail or calendar fetch | proof: pytest output
- [ ] P14. Re-authorizing is safe and documented. Saving a sign-in result with an empty or missing refresh token refuses and leaves the stored token untouched; the same label overwrites in place (account count unchanged). `skills/add-account/SKILL.md` has a re-authorize section with bash AND PowerShell forms; `skills/week/SKILL.md` renders the code-written auth line as given. | verify: unit tests on the save path; grep the 2 skill files | proof: pytest + grep output
- [ ] P15. Full suite green twice: `.venv/bin/python -m pytest` exits 0, and again with `PATH=/usr/bin:/bin`. Passed count is at least 2670 plus the new tests; the skipped set is the same 4 as baseline. | verify: run both, compare counts and skip names | proof: both summaries
- [ ] P16. Version and notes. `pyproject.toml` and `.claude-plugin/plugin.json` read 0.75.2; `CHANGELOG.md` has a 0.75.2 entry whose bullets start `Fixed:` / `Improved:` / `New:`, name no file or function, and say in plain words that older connected accounts keep working and that a sign-in problem now names the account. The manifest test passes. Every file in the 0.75.2 commit traces to this objective. | verify: read both files; `git show --stat HEAD`; manifest test | proof: excerpts
- [ ] P17. NEGATIVE: zero em or en dashes in added lines, across the git diff AND untracked new files, with per-file line counts printed; the scanner (characters written as backslash-u 2014 and 2013 escapes) first finds a planted dash. | verify: run `objectives/google-scope-refresh-fix/dash_scan.py --selftest` then the real scan | proof: scan output
- [ ] P18. NEGATIVE: no secret and no client identity. No Google or Microsoft token value in the objective folder or the proof folder (patterns `1//0`, `ya29.`, `0.A` and JWT-shaped `eyJ`, proven on a planted instance); no added line in the diff names the client, its domain or its user. | verify: run `objectives/google-scope-refresh-fix/secret_scan.py --selftest` then the real scan | proof: scan output
- [ ] P19. Published. `/base-publish` has run after an evaluator PASS and the public repo's `plugin.json` on `main` reads 0.75.2. | verify: `gh api repos/NVentures/project-van-gogh-claude/contents/.claude-plugin/plugin.json --jq .content | base64 -d | grep version` | proof: command output
- [ ] P20. Lessons recorded where they recur: the OAuth rule in `~/Documents/Workspace/lab-notes.md` sharpened in place (cause: scopes sent on refresh; fix: never send them; the wrong-invariant tests), not a new entry; project `CLAUDE.md` Tier 1 section states "scopes are named at consent only"; the vault project memory has an entry. | verify: grep each of the 3 files | proof: grep output
- [ ] P21. NEEDS_HUMAN: the client confirms. After their plugin updates to 0.75.2, `/van-gogh:week` shows calendar and mail with no sign-in step. | verify: Nobel asks the client; a 3-line reply for them is drafted in the receipt | proof: their answer

## Out of scope

- Dropping the full-access Google Drive scope from sign-in. Nothing in `app/` uses it; flagged for a separate decision because companion and personal skills may.
- A general "probe every account with a real call" health check. With this fix a scope change no longer needs one.
- Passing a structured error class from raise site to classifier. The auth sentence keeps the OAuth error code so text classification still works.
- Regenerating the client's missed weekly briefing. They rerun `/van-gogh:week` after the update.
- Rewording the 50 or so non-account `errors.append` sites.
- Running the PowerShell re-authorize form on a Windows machine. It is written and reviewed, not executed; the receipt says so.

## Access audit

Done before freeze, read-only:
- State dir has 1 Google token (`GOOGLE_REFRESH_TOKEN_PERSONAL`) and 1 Microsoft token (`MS_GRAPH_REFRESH_TOKEN_PALLADIUM`); vault pointer resolves. P2, P8, P9 are runnable here.
- Baseline suite: 2670 passed, 4 skipped, 1 FAILED (`test_safety_denylist`, caused by the uncommitted 0.75.1 work). P1 clears it.
- The raw-error grep finds 53 `errors.append` sites under zsh with the pattern quoted, so the P12 site table has a non-empty input.
- `run_ledger.py:61` stores `error_class` at write time, so P10 must seed a row with both the old text and the old stored class.
- Still to confirm at build start, before any edit: `gh auth status` for P19, and that a plugin copy outside the repo runs `week_review.py` against the real state dir for P9.

## Evaluation log

### Evaluation 2026-09-17 11:44, round 1
Contract hash: verified (bb85740ff450adcf7731db442160a81161d4717fc6291862504b2c309cef8e1f, recomputed = stored)

| Item | Verdict | Evidence |
| P1 | PASS | `git log --oneline -3` shows 24bd114 "single-hash next steps... (0.75.1)"; `git show --stat` lists 5 files, no OAuth file. `pytest tests/test_safety_denylist.py` = 24 passed. |
| P2 | PASS | Ran `live_refresh_probe.py`, exit 0. CONTROL ok: old construction + 1 ungranted scope -> RefreshError invalid_scope. FIXED ok: `_credentials()` refreshed, Calendar list answered 3 calendars, scopes passed = None. No token printed. |
| P3 | PASS | `pytest tests/test_google_refresh_scopes.py -v` = 5 passed, 0 skipped. `test_the_refresh_request_carries_no_scope_field` ran BY NAME, drives real google-auth refresh through a fake transport, asserts `"scope" not in fields` of the captured POST body. |
| P4 | PASS | AST test `test_no_code_builds_google_credentials_with_scopes` passed. Evaluator grep of `Credentials(` across app/ skills/ ../templates/ ../clients/ = 1 hit (app/google_client.py:88); `from_authorized_user` = 0 hits. |
| P5 | PASS | Copy at scratchpad/p5copy, constructor reverted to `scopes=GOOGLE_SCOPES`. Both target tests failed, exit 1: `test_the_refresh_request_carries_no_scope_field` FAILED and `test_no_code_builds_google_credentials_with_scopes` FAILED ("google_client.py:88 must pass scopes=None explicitly"). Real tree verified clean after. |
| P6 | PASS | `grep -l "auth/gmail.modify" app/*.py` = oauth_scopes.py only; `grep -l "Mail.ReadWrite" app/*.py` = oauth_scopes.py only; `grep -n "<= set(google_client" tests/` = 0. Identity asserts present for auth_bootstrap, google_client, microsoft_client (test_oauth_scope_rule.py:25-28). Both former wrong-invariant sites now assert consent-list membership (test_page_actions.py:184, test_contact_capture.py:230). |
| P7 | PASS | `pytest tests/test_oauth_scope_rule.py -v` = 13 passed, covering stored-grant, frozen-baseline and reserved-scope-filter paths. Mutation in scratchpad/p7copy (`refresh_scopes` returns `list(GRAPH_SCOPES)`): 3 failed incl `test_a_new_consent_scope_leaves_existing_accounts_alone`, exit 1. |
| P8 | PASS | Same probe run, section 2: "MS ok: refreshed with 4 scopes (baseline path); Graph /me answered: True". Exit 0, no token printed. |
| P9 | PASS | Ran `live_end_to_end.py`, exit 0. CONTROL: 2 errors, 2 invalid_scope (Personal calendar, Personal deals). FIXED: 0 errors, 0 invalid_scope, 0 sign-in, 38 calendar events. Counts and error lines only. |
| P10 | PASS | `test_a_stale_scope_rejection_is_re_run_not_held` passed (actionable = True, no "sign in"). Evaluator ran `failure_class.classify` on the P2 verbatim text = "unknown", not "auth". `REFRESH_SCOPE_RE` checked before AUTH_RE with a 7-line comment (failure_class.py:82-89) saying why it is deliberately not auth. |
| P11 | PASS | 4 cases passed: Google-with-label ("your Google account (Personal)", /van-gogh:add-account, no "sign in to Claude"), Microsoft ("your Microsoft account (Outlook)"), unlabelled Google, and Claude keeping its own sentence. |
| P12 | PASS | `pytest tests/test_account_error_lines.py` = 17 passed. Evaluator independently called `account_error_line` with a RefreshError quoting a Google-refresh-token-shaped placeholder: secret absent from the line, `classify(line) == "auth"`, only type+code survive. Evaluator site table: 78 append-to-errors sites in app/; 45 format the exception raw and NONE guards a per-account Google or Microsoft fetch (afternoon_tea.py:717 swallows per-account exceptions inside `fetch_tomorrow_calendar`; morning_coffee.py:678/892 wrap a subprocess). All 6 account-fetch modules use the helper. NOTE: the tests prove this at the helper plus a source scan proven on a planted instance, not by planting an exception at the three named fetch sites; the criterion itself is observed true. |
| P13 | PASS | `pytest tests/test_contact_capture.py` = 23 passed. `classify("403 Request had insufficient authentication scopes...")` = "auth" and "auth" in NO_RETRY. `contact_capture.SCOPE_HELP` says "Sign in again". Contact capture is its own job_registry row and its own script; no briefing imports its People API calls (only warm_start uses pure helpers `is_robot_address`/`org_from_domain`), so it shares no try block with a mail or calendar fetch. |
| P14 | PASS | 5 save-path tests passed: empty/None/whitespace token raises SystemExit(1) leaving the stored token byte-identical; same label replaces in place keeping other keys; relabel carries granted scopes. `skills/add-account/SKILL.md:24` has the re-authorize section with bash AND PowerShell forms. `skills/week/SKILL.md:599` says render the auth line "as it is given". |
| P15 | PASS | `.venv/bin/python -m pytest` = 2719 passed, 4 skipped, exit 0 (baseline 2670 passed + 49 new). `PATH=/usr/bin:/bin` run = 2718 passed, 5 skipped, exit 0. Normal-PATH skip set matches the 4 baseline names (2x job_registry_guard, yaml import, skill_key_contract); the 5th under stripped PATH is `needs the claude CLI on PATH`, a property of the stripped PATH itself, not a regression. |
| P16 | PASS | `pyproject.toml:3` and `.claude-plugin/plugin.json:4` both read 0.75.2. CHANGELOG 0.75.2 entry has 4 bullets starting Fixed:/Improved:/Improved:/Fixed:, naming no file or function, saying older connected accounts keep working and that a sign-in problem now names the account. `pytest tests/test_plugin_manifest.py` = 14 passed. `git show --stat HEAD` = 32 files, all tracing to this objective; the one non-OAuth file (collectors.py, a 1-line `return out`) was the blocker that crashed week_review on any notetaker install, without which P9 could not run, and is disclosed in both the commit message and the changelog with mutation-proven tests. |
| P17 | PASS | `dash_scan.py --selftest` found its planted dashes (2 prose, 1 table cell, 0 clean), exit 0. Evaluator re-ran the equivalent scan over the real 0.75.2 commit because the shipped scan reads `git diff HEAD`, now empty: 0 em or en dashes in 1525 added lines across 32 files, per-file line counts printed. NOTE: as run post-commit the shipped scanner reports PASS over 0 files, which is vacuous; the substance was verified against the commit diff. |
| P18 | PASS | `secret_scan.py --selftest` found all 5 planted token shapes plus the identity string and stayed silent on a clean line, exit 0. Objective folder: 5 files scanned, 0 credential and 0 identity hits. Evaluator scanned the real commit's 1525 added lines: 0 identity hits; 1 credential-shaped hit is the invented token-shaped fixture at test_account_error_lines.py:30, the planted secret the P12 test proves never reaches a briefing, not a real token. Proof folder not present. |
| P19 | FAIL | `gh api repos/NVentures/project-van-gogh-claude/contents/.claude-plugin/plugin.json` decodes to `"version": "0.75.0"`. Required 0.75.2. /base-publish has not run. |
| P20 | PASS | `lab-notes.md` OAUTH rule sharpened in place, one entry, now carrying "invalid_scope = requested scopes are a SUPERSET of granted -> STOP NAMING SCOPES ON REFRESH", the scopes=None / MSAL-stored-list fix, and "THE TESTS ARE THE TRAP: 2 asserted the client must carry every consent scope". Project `CLAUDE.md:43` states "Scopes are named at consent and nowhere else". Vault memory.md:61 has "Footgun: a scope list sent on refresh disconnects every older account". |
| P21 | NEEDS_HUMAN | Nobel must ask the client, after their plugin updates to 0.75.2, whether `/van-gogh:week` shows calendar and mail with no sign-in step. Not runnable by the evaluator: it requires a third party's machine and their answer. Blocked behind P19 in any case, since 0.75.2 is not published. |

Overall: FAIL (1 of 20 gradeable items failing: P19 unpublished; 1 NEEDS_HUMAN)

## Lessons

1. A credential carries what it was GRANTED, never what the code currently
   wants. Any place code re-states a permission it already holds is a place a
   future addition breaks every existing user, one release later, silently.
   Recorded in lab-notes (OAUTH canon, sharpened in place: the old entry said
   invalid_scope means "re-consent all", which is the wrong advice this bug
   disproved), project CLAUDE.md, and vault project memory.
2. The tests were the trap, not just an omission. Two asserted the exact wrong
   invariant (the runtime client must carry every consent scope) and none ever
   drove a refresh, so the suite defended the bug. A guard over a protocol must
   exercise the real call and read what goes on the wire; a guard over a rule
   must read the AST so a new call site cannot opt out.
3. A diff-based scanner goes vacuous the moment the work is committed:
   `git diff HEAD` is then empty and it reports a clean pass over nothing. The
   evaluator caught this in both my scanners. They now take --commit <sha> and
   refuse to report a pass when they scanned zero lines. Recorded in the
   lab-notes VACUITY canon as a new shape of the empty-input gate.
4. A scanner holding planted fixtures of what it hunts flags itself, and so
   does a report quoting a token-shaped placeholder. Exempt exactly the scanner
   by resolved path (never delete the fixtures, they are the proof it fires),
   and fix a report by describing the shape rather than spelling it.
5. Proving the fix end to end through the REAL entry point found a second,
   unrelated live bug that the whole test suite missed: a helper fell off the
   end returning None and crashed both weekly briefings on any install with a
   notetaker configured. A unit suite cannot find what only the real binary
   running against a real account will show.

## Red-team changes

Dropped "classify `invalid_scope` as auth" (it would have told the client to re-consent for a fault the update already cures) and replaced it with P10. Replaced a one-time grep with a standing AST guard (P4) and tied the mutation to both guards (P5). Upgraded Microsoft from a tripwire to the structural fix (P7, P8). Added P13 (missing scope degrades one feature), P14 (re-authorize can never blank a stored token), the classify-the-sentence check in P12, pass-by-name and skip-set checks in P15, release-contents tracing in P16, counts-only proof in P9, publish and client confirmation (P19, P21).
