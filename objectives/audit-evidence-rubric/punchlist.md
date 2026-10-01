# Punch List: evidence-scored audit, finding ledger, eliminate-first pick, interview checkpoints

Created: 2026-09-09
Status: in-progress

Source plan: ~/.claude/plans/read-plan-and-make-rippling-hopcroft.md
Approval: Nobel approved the fit review and typed "Go". That is the human gate.
Red-team: the source plan was red-teamed during planning; the fit review added the
venv gap, the installed-plugin-version gap, and the developer-word rule.

## Contract

- [ ] P0. Dev venv exists and the suite is green before any change (baseline 1221 passed). verify: `.venv/bin/python -m pytest -q`
- [ ] P1. `run_sweep` output carries `evidence`, `score` (20 rows C1..D5, points in {0,1,3,5,null}, `judged_by`, `evidence` ids, four `subtotals`, `caps_applied`, `total_min`, `total_max`, `stage_min`, `stage_max`), `ledger`, `score_md`, `ledger_md`, `automation_md`, `sidecar_path`, `ledger_path`. On the planted fixture every list is non-empty. verify: `pytest tests/test_vault_gardener.py -q`
- [ ] P2. Scoring arithmetic in code: points only in {0,1,3,5} (2 or 4 raises ValueError); Context cap 10 when the purpose or priority probe is not found; Cadence cap 10 when D1 == 0; total caps 49 (any C < 10), 69 (any C < 15 or fewer than 2 due successes), 84 (any C < 20); stage table pinned at every threshold (24/25, 49/50, 69/70, 84/85). Calibration: 25/25/25/10 -> 69; 22/22/22/19 with 2 successes -> 84; 23x4 with 1 success -> 69; 24x4 with 2 successes -> 96. verify: `pytest tests/test_audit_evidence.py -q -k score`
- [ ] P3. Fresh install and hollow install both score Unproven; a fully planted fixture scores all 11 code rows at 5 with no caps. One assertion per row. verify: `pytest tests/test_audit_evidence.py -q -k "fresh or hollow or planted"`
- [ ] P4. No points without a citation, per criterion: deleting each cited file drops that row's points; a hand-built row with points and no evidence ids is zeroed with basis `no_citation`. verify: `pytest tests/test_audit_evidence.py -q -k citation`
- [ ] P5. Five retrieval probes through declared config routes, each `found_direct` or `not_found`: purpose, priority, project, entity, source. C2 = 5 at 5/5, 3 at 3-4, 1 at 1-2, 0 at 0. A moved project page -> not_found. Week freshness reads the `generated:` frontmatter date, never mtime. verify: `pytest tests/test_audit_evidence.py -q -k probe`
- [ ] P6. Per-account Connections evidence with no fuzzy match: `verified` needs a sidecar within 7 days, no error naming that exact label, and >= 1 item with `account == label`; "Gmail" and "Gmail 2" stay distinct; `verified_empty`, `failed`, `unverified`, `stale` (8 days) all distinguished. N1 5/3/1/0. verify: `pytest tests/test_audit_evidence.py -q -k account`
- [ ] P7. Cadence from evidence: D1 from a read-only `scheduler_setup.job_states()` seam; D2 counts rc 0 runs within 14 days (plus legacy digest sent lines), 5 only with >= 2 distinct dates AND every loaded job inside 2x cadence; a failure after the last success downgrades. D3 tiers 1/3/5. verify: `pytest tests/test_audit_evidence.py -q -k cadence`
- [ ] P8. Run ledger: `record_run` appends one JSON line, single write, fails open; `skill_run.main` records `scheduled.<skill>` and `digest_send.main` records `digest.<briefing>` on every exit path including exceptions and skipped; `read_runs` tolerates a torn last line. verify: `pytest tests/test_run_ledger.py tests/test_digest_send.py -q`
- [ ] P9. Finding ledger: atomic write, id = sha1("kind|key")[:12] with vault-relative separator/case normalized key (two absolute roots, one with backslashes, same ids); statuses new/open/resolved/reopened/not_rechecked/archived/no_longer_applicable walked across 6 runs; archived after 4 consecutive resolved runs, hidden from ledger_md, kept in file. verify: `pytest tests/test_audit_ledger.py -q`
- [ ] P10. Idempotency: two sweeps on an unchanged vault emit identical ids, run 2 has 0 new, ledger differs only by the appended run row's ts; same-day rerun replaces its own row; history capped at 52. verify: `pytest tests/test_audit_ledger.py -q -k "idempot or history"`
- [ ] P11. `score_md`, `ledger_md`, `automation_md` rendered by code, numbers equal the JSON, ledger_md ordered and capped at 40 with an "and N more" line and a per-status count line, all three carry their markers, zero em/en dashes (planted-dash mutation on `scrub`), and none carries a developer word from the DESIGN.md list. verify: `pytest tests/test_audit_evidence.py tests/test_audit_ledger.py -q -k md`
- [ ] P12. `--validate-report PATH` exits 0 only when every rule holds; reasons are exact strings (`missing_marker:<name>`, `score_mismatch:<id>`, `model_points_invalid:<id>`, `citation_unresolved:<id>`, `total_mismatch:<a>/<b>`, `ledger_row_missing:<id>`, `dash_found:<line>`, `developer_word:<word>`, ...). One mutation per reason string. verify: `pytest tests/test_vault_audit_validate.py -q`
- [ ] P13. Eliminate-first block validated: Candidate, Eliminate ending stop|keep with a >= 8 word reason, Autonomy L0-L4 only when keep plus a why-not-lower clause, KPI from the closed list plus a metric, Size S|M|L, Picked equal to the run date, Refs citing an id. Six named mutations. verify: `pytest tests/test_vault_audit_validate.py -q -k automation`
- [ ] P14. Pick follow-through: the previous report's automation block is parsed into a `pick` finding; the validator requires a `Previous pick:` line with outcome built|not built|stopped|dropped whenever the ledger holds a prior pick. verify: `pytest tests/test_audit_ledger.py tests/test_vault_audit_validate.py -q -k pick`
- [ ] P15. `skills/vault-audit/SKILL.md` rewritten: letter grades gone, paste-verbatim blocks with markers, model fills null rows only with citations, eliminate-first block, `--validate-report` mandatory with max 2 fix loops, description no longer claims a Friday schedule. verify: greps for each marker and `grep -c 'Fridays' == 0`, `grep -c 'Grade A' == 0`
- [ ] P16. In `--auto`, validation still failing after 2 loops writes `Validation: FAILED (<reasons>)` and prints the literal token STEP-FAILED. verify: grep STEP-FAILED in the --auto paragraph
- [ ] P17. `app/interview_capture.py`: start/append/show/close with exclusive create, same-day `-2`, resume detection, fsync + readback assertion (exit 2 `readback_failed`), OSError -> exit 2 `write_failed`, close flips frontmatter and a later append fails `no_open_capture`. verify: `pytest tests/test_interview_capture.py -q`
- [ ] P18. `skills/voice-bootstrap/SKILL.md`: `start` before Step 0; `append` after Step 0, 1, 3, 4, 5 sign-off, 6; `close` after Step 7; PowerShell twins. verify: `pytest tests/test_interview_capture.py -q -k skill_order`
- [ ] P19. `skills/update-settings/SKILL.md` and `skills/create-skill/SKILL.md`: start, append per answer, close, PowerShell twins. verify: same test
- [ ] P20. `skills/week-retro/SKILL.md` section 8 keys off the `Picked:` date and names both section titles. verify: greps
- [ ] P21. Negative, writes: a sweep writes exactly the ledger and the sidecar under logs/ and nothing else (tree snapshot against an explicit allow-list naming the import-time `{vault}/CLAUDE.md` sync); `--no-write` changes nothing; captures are never reported as stub or orphan findings. verify: `pytest tests/test_vault_gardener.py -q -k "writes or captures"`
- [ ] P22. Negative, secrets: a planted fake token, the same token inside a sidecar error, and an email address appear nowhere in the sweep JSON, the rendered blocks, the sidecar or the ledger. verify: `pytest tests/test_vault_gardener.py -q -k secret`
- [ ] P23. Live end-to-end: the real skill run unattended against this checkout writes the report and `--validate-report` exits 0; the full mutation matrix runs against a copy of that real report; a planted-evidence twin raises N1 and D2 to predicted values. verify: outputs captured to the proof dir
- [ ] P24. Version 0.49.0 -> 0.50.0 in pyproject.toml and plugin.json, CHANGELOG entry, four modules added to `_MIGRATED_MODULES`, CLAUDE.md logs listing and vault-audit paragraph updated, full suite green. verify: `.venv/bin/python -m pytest -q`
- [ ] P25. Cold-read loop on the real rendered report until a round returns zero findings; each round's findings and the rule added are logged here.
- [ ] P26. No em or en dash on any added line, scanner proven on a planted dash first. verify: `objectives/audit-evidence-rubric/dash_scan.py`

## Evaluation log

(appended by the evaluator)
