# Punch List: Travel logistics in Morning Coffee

Created: 2026-09-09
Status: evaluated-pass
Contract-SHA256: f2e4e3b09d0baa35d400931647f41688b2ad49c97594a5850387e66ab64cb1b0

Scope: `project-van-gogh/` only. New module `app/travel.py`, changes to
`app/morning_coffee.py`, `app/week_review.py`, `app/config_loader.py`,
`config.template.json`, `skills/morning-coffee/SKILL.md`, tests.

Red-team pass ran before approval (general-purpose agent, objective + draft
only). It found the criterion that would have shipped a missed flight: draft
P5 asserted that a leave-by time is shown, not that it is correct, while
calendar events carry `time` as a preformatted local string with no timezone.
A 6:05 AM ET flight from a PT home computes three hours late and every check
stays green. That draft item was split into five, the timezone case made
mutation-proof, and eight missing criteria were added (cost cap, API failure,
geocode sanity, trip-detection false positives, return legs, re-run
idempotence, guard tests, visible failure).

## Decisions taken at interview

- Trips are detected by an IATA airport code in the calendar event's location.
- Routes API is called only on a departure morning, at most 3 calls per day.
- Home address never renders; it lives in private state, not the vault config.
- Feature is on by default. Flights links and weather need only the calendar.
  Drive time and leave-by stay dark until a home address is set.

## Contract

- [ ] P1. `app/travel.py` exists; the leave-by calculator, trip detector,
      notice-window selector and ledger are each covered by a table-driven
      test whose cases include a cross-timezone flight, a DST boundary, a
      midnight rollover and a no-trip day.
      verify: `.venv/bin/python -m pytest tests/test_travel.py -q`
      proof: pytest output

- [ ] P2. Both calendar fetchers carry `location`, and the departure notice
      derives its airport from that field.
      verify: `.venv/bin/python -m pytest tests/test_travel.py -k location -q`;
      the test blanks `location` on a trip event and asserts the airport
      disappears, so a populated-but-unused field fails.
      proof: pytest output

- [ ] P3. A trip with no booked flight emits a Google Flights URL whose parsed
      query carries the right origin, destination and outbound date. A trip
      that already has a flight emits none.
      verify: `.venv/bin/python -m pytest tests/test_travel.py -k flights -q`
      (asserts parsed params, never a substring)
      proof: pytest output

- [ ] P4. Weather renders on T-2 and on no other day.
      verify: table test over T-4 through T+1 asserting the exact set of days
      that render weather; destination coordinate within 100 km of the test
      airport's known location.
      proof: pytest output

- [ ] P5a. TIMEZONE. A 06:05 America/New_York flight, home in
      America/Los_Angeles, 34 min drive, 90 min buffer, yields leave-by
      01:41 America/Los_Angeles, rendered in home local time with the zone
      named. Includes one DST-transition case.
      verify: `.venv/bin/python -m pytest tests/test_travel.py -k timezone -q`;
      MUTATION CONTROL: deleting the timezone conversion must turn this red,
      demonstrated in the evaluation log.
      proof: pytest output plus the mutation run

- [ ] P5b. The origin airport comes from the event location; an event naming a
      different airport produces a different airport in the notice.
      verify: pytest, two fixtures differing only in location
      proof: pytest output

- [ ] P5c. The terminal is shown only when it came from the calendar entry.
      A guessed terminal is either absent or labelled as unconfirmed in the
      rendered text.
      verify: pytest asserts no bare terminal string appears without a source
      proof: pytest output

- [ ] P5d. The Routes request carries a departure time equal to the computed
      leave-by instant, not "now".
      verify: pytest with a stubbed transport asserting the request body
      proof: captured request body

- [ ] P5e. The 90-minute buffer is a config key with a documented default, not
      a literal in the code.
      verify: `grep -n "90" app/travel.py` shows no bare buffer literal;
      config accessor test covers the default
      proof: grep output plus pytest

- [ ] P6. Ledger: the same notice twice is suppressed; a different notice type
      for the same trip still fires; a missing, empty or malformed ledger file
      still renders the notice and rebuilds the ledger; entries for trips more
      than 30 days past are pruned.
      verify: `.venv/bin/python -m pytest tests/test_travel.py -k ledger -q`
      proof: pytest output

- [ ] P7. Tier 2 with a connector payload holding an event that WOULD be a trip
      under Tier 1 produces zero trips, no exception, and one line saying
      travel notices need a connected calendar. Control: the same event under
      Tier 1 does produce a trip, proving the Tier 2 fixture is not inert.
      verify: `.venv/bin/python -m pytest tests/test_travel.py -k tier -q`
      proof: pytest output

- [ ] P8. No street address, no home coordinate finer than 2 decimal places,
      and no API key appears in the published page, the vault file, the ledger,
      any file under the logs dir, or any exception message. A sentinel address
      is planted in config and every produced artifact is grepped.
      verify: `.venv/bin/python -m pytest tests/test_travel.py -k sentinel -q`;
      MUTATION CONTROL: removing the guard must turn this red.
      proof: pytest output plus the mutation run

- [ ] P9. A config captured from before this feature runs with the travel
      feature emitting no notices and making zero outbound network calls, and
      every new config accessor returns its default.
      verify: `.venv/bin/python -m pytest tests/test_config_back_compat.py -q`
      with the new accessors added to the ACCESSORS sweep; a stubbed transport
      asserts zero calls.
      proof: pytest output

- [ ] P10. Drive time is requested only on a departure morning, capped at 3
      Routes calls per day with the counter in the ledger; the 4th is refused.
      A day with no departure makes zero Routes calls.
      verify: `.venv/bin/python -m pytest tests/test_travel.py -k cap -q`
      proof: pytest output

- [ ] P11. Every outbound call has an explicit timeout. Connection refused,
      HTTP 500, HTTP 429, timeout and malformed JSON each produce a degraded
      but useful notice, and the rest of the briefing is unaffected.
      verify: `.venv/bin/python -m pytest tests/test_travel.py -k failure -q`
      proof: pytest output

- [ ] P12. An exception inside the travel module leaves the briefing complete
      and carries a line saying the travel section failed.
      verify: pytest injects an exception in the trip detector
      proof: pytest output

- [ ] P13. Trip detection precision: at least 10 negative calendar events
      (meetings mentioning travel, recurring blocks, all-day OOO, a title
      naming an airport with no location) produce zero trips; at least 5
      positives produce exactly one trip each.
      verify: `.venv/bin/python -m pytest tests/test_travel.py -k detect -q`
      proof: pytest output

- [ ] P14. An implausible drive time (over 4 hours or under 2 minutes) is
      treated as a lookup failure and not printed as a leave-by.
      verify: pytest with stubbed Routes responses at both bounds
      proof: pytest output

- [ ] P15. Re-running Morning Coffee the same day produces no duplicate notice
      and no second Routes call. The ledger is written after the notice is
      rendered, so a crash mid-render cannot record a notice that never showed.
      verify: `.venv/bin/python -m pytest tests/test_travel.py -k rerun -q`
      proof: pytest output

- [ ] P16. The repo guard tests pass over the new files: encoding on every
      open, no hardcoded platform paths, clean script import, no forbidden CLI
      references.
      verify: `.venv/bin/python -m pytest tests/test_open_encoding.py
      tests/test_cross_platform.py tests/test_script_imports.py
      tests/test_no_cli_references.py -q`
      proof: pytest output

- [ ] P17. The full suite passes.
      verify: `.venv/bin/python -m pytest -q`
      proof: pytest summary line with counts

- [ ] P18. Version bumped in `pyproject.toml` and `.claude-plugin/plugin.json`
      in lockstep, with a CHANGELOG entry in the documented format.
      verify: `.venv/bin/python -m pytest tests/test_plugin_manifest.py -q`
      plus reading the CHANGELOG head
      proof: both version lines plus the entry

- [ ] P19. `skills/morning-coffee/SKILL.md` documents the travel section, and
      what it documents matches what the code emits (section name, order, and
      the output keys).
      verify: a test asserts every travel key named in SKILL.md exists in the
      morning_coffee output dict
      proof: pytest output

- [ ] P20. No em-dash or en-dash in any file touched by this change, new or
      modified, and none in the rendered notice text produced at runtime,
      enforced by a code-level strip on generated strings.
      verify: a scanner using `—`/`–` escapes, proven on a planted
      instance first, run over the diff and over generated notice strings
      proof: scanner output including the planted-instance run

- [ ] P21. LIVE END TO END. A scripted acceptance matrix runs the real
      `morning_coffee.py` over five fixed scenarios with output captured and
      diffed against expected: (a) no trip, no travel section, zero API calls;
      (b) trip in 5 days with no flight, link only; (c) trip in 2 days,
      weather only; (d) departure day cross-timezone, full notice with correct
      leave-by; (e) re-run of (d), suppressed by the ledger.
      CONTROL: with `app/travel.py` removed, scenarios (b) through (e) must
      fail the identical check, proving the matrix can go red.
      verify: `.venv/bin/python objectives/travel-logistics/live_check.py`
      proof: captured output of both the real run and the control run

## Out of scope

- Return legs get no departure-day notice. A return flight is detected as a
  trip for the flights link and weather, but the leave-by notice assumes the
  user is at home and is emitted for outbound legs only.
- No booking, no check-in, no seat selection, no itinerary parsing from email.
- Hotel, rental car and ground transport at the destination.
- Rewriting the travel plan document itself. That is a separate deliverable.
- Client key distribution. The install bundle work is already done and is not
  re-opened here.
- Multi-leg itineraries beyond treating each leg as its own calendar event.

## Access audit

Ran 2026-09-09, before execution. Every verify step was executed once under
the shell the evaluator uses (zsh via the Bash tool).

- `.venv/bin/python` is Python 3.14.7 with pytest 9.1.1. Baseline suite is
  green at 1428 passed in 20.27s, so any later failure is this change.
- Guard tests (open encoding, cross platform, script imports, no CLI
  references) run: 43 passed. Manifest test runs: 14 passed.
- The dash scanner was proven on a planted instance BEFORE being trusted:
  a file containing one em-dash returned a count of 1 and exited 0. A scanner
  that has not found a known-present instance is not evidence.
- The contract hash recomputes stable after stamping.
- GAP FOUND, resolved by design: there is no `.env` and no Maps API key on
  this machine, so no real Routes call can be made here. P21 therefore
  exercises the real `morning_coffee.py` end to end over real calendar-shaped
  fixtures with the HTTP transport stubbed at the boundary. The control (with
  `app/travel.py` removed) is what proves that matrix can go red. A live
  billed Routes call is NEEDS_HUMAN and is listed as such in the receipt.
- House HTTP convention confirmed: `requests` with an explicit module-level
  `TIMEOUT` constant, as in `app/notetaker_grain.py:93`. The new module
  follows it rather than inventing a client.
- Confirmed by reading source, not assumption: calendar events in
  `app/week_review.py` carry only source, title, day, time, _sort. No
  location field exists in either the Google fetcher (line ~428) or the
  Outlook fetcher (line ~457). Both must be extended.
- Confirmed: `briefing_html.redact` strips credential shapes and filesystem
  paths only. A street address passes through it untouched, which is what
  P8 exists to stop.

## Evaluation log

(evaluator appends dated verdict tables here)

### Evaluation 2026-09-09 11:51, round 1
Contract hash: verified (f2e4e3b0...cb1b0 recomputed == stored)

| Item | Verdict | Evidence |
| P1 | PASS | `pytest tests/test_travel.py -q` = 93 passed. Cross-timezone, DST, midnight-rollover and no-trip cases all present as named tests; leave_by/detect_trips/notice_key/load_ledger each referenced 7/14/4/3 times. |
| P2 | PASS | `-k location` = 4 passed. week_review.py:435 (Google) and :466 (Outlook) both carry `location`. test_location_is_consumed_not_merely_carried blanks location and asserts detect_trips returns []. |
| P3 | FAIL | Second clause unimplemented. app/travel.py:507 emits the flights link for ANY trip 3+ days out with a destination pair; there is no booked-flight concept anywhere (`grep -n "booked\|has_flight"` returns only the literal notice string at :517). Live probe: event titled "UA 523 SFO to EWR (Confirmation ABC123)", location "SFO -> EWR, Terminal 3" emitted `No flight booked yet for SFO to EWR on Nov 12.` plus a search link. The 17 `-k flights` tests pass because none of them uses a booked flight. |
| P4 | FAIL | First half PASSES: test_notice_fires_only_on_its_own_day covers offsets 5,4,3,2,1,0,-1 and weather renders only at offset 2. Second half MISSING: no destination-coordinate assertion exists. `grep -rn "haversine\|distance\|within 100\|geodesic" tests/test_travel.py app/travel.py` returns nothing (exit 1). The only coords in the file, tests/test_travel.py:476 `40.7, -74.2`, are a stubbed input the test feeds in, not an assertion that a resolved coordinate lands within 100 km of the airport. |
| P5a | PASS | `-k timezone` = 6 passed. MUTATION CONTROL performed: replaced `return leave.astimezone(home_tz)` (travel.py:199) with `return leave.replace(tzinfo=home_tz)`; result 2 failed, 4 passed, with test_timezone_dst_boundary reporting `AssertionError: got 2026-11-01 09:30:00-08:00, assert (9,30) == (6,30)`. Restored; sha256 back to 0fe7b166...2a512 and 6 passed. |
| P5b | PASS | POSITIVE_EVENTS carries SFO, LAX and JFK fixtures differing in location; each asserted individually via parametrize, `-k detect` = 17 passed. test_detect_reads_location_never_title confirms an empty location yields no trip. |
| P5c | PASS | _terminal() (travel.py:172-180) reads only a `Terminal X` match from the location; no guess table exists. test_terminal_only_from_calendar asserts "SFO Airport, Terminal 3" -> "3" and "San Francisco Airport (SFO)" -> None. |
| P5d | PASS | test_routes_asks_for_the_departure_instant_not_now captures the stubbed request body and asserts `departureTime == "2026-11-12T12:01:00Z"` (the computed instant, not now), `routingPreference == TRAFFIC_AWARE`, and `timeout == travel.TIMEOUT`. |
| P5e | PASS | `grep -n "90" app/travel.py` returns nothing (exit 1): no bare buffer literal. config_loader.travel_buffer_minutes() at :994 reads `airport_buffer_minutes` with a documented default of 90 and a TypeError/ValueError fallback. |
| P6 | PASS | `-k ledger` = 10 passed. Covers same-notice suppression, different-kind-same-trip firing, 4 corrupt shapes each parametrized separately ("", "not json at all", "[1,2,3]", '{"shown":"wrong type"}'), missing file, 30-day prune, and disk roundtrip. |
| P7 | PASS | `-k tier` = 2 passed. test_tier2_payload_produces_no_trips plus test_tier2_control_the_same_event_with_a_location_does_produce_a_trip, which adds only `location` to the identical dict and asserts len==1, proving the fixture is not inert. |
| P8 | FAIL | MUTATION CONTROL passes: rendering `home_address()` into travel.py:536 turned test_sentinel_address_never_appears_in_any_rendered_notice red with the sentinel quoted in the diff; restored to 0fe7b166...2a512. But the "no API key or address in any exception message" clause is BROKEN, demonstrated live: (1) build_notices does not wrap drive_lookup, so a raising lookup propagates `RuntimeError: connection failed for 1 Sentinel Way, Nowhere key=sentinel-api-key-value`; (2) collect() (travel.py:603-604) interpolates raw exception text: `Travel notices failed: detector failed reading 1 Sentinel Way, Nowhere`, which morning_coffee.py:1000 appends to output["errors"], a field SKILL.md:501-503 tells the model to render. The punch list's own Access audit records that briefing_html.redact does not strip street addresses. Also, the test greps only the notice JSON and the ledger, never a published page, a vault file or the logs dir as the criterion requires. |
| P9 | FAIL | Accessor half PASSES: tests/test_config_back_compat.py:59 adds travel_enabled, travel_buffer_minutes, travel_weather_lead_days to ACCESSORS; 78 passed. Network half MISSING: the criterion requires "a stubbed transport asserts zero calls" in that file. `grep -n "requests\|transport\|monkeypatch\|zero"` over the 99-line file returns only a comment on line 39. `grep -rln "MINIMAL" tests/` returns only that file, so no pre-feature-config zero-network-call assertion exists anywhere. |
| P10 | PASS | `-k cap` = 3 passed. Verified end to end beyond the tests: with routes_calls preloaded to the cap of 3, build_notices made 0 lookups and degraded to "Live drive time is unavailable, so allow your usual time." with leave_by None. test_no_trip_means_no_lookup_at_all covers the zero-departure day. |
| P11 | PASS | `-k failure` = 11 passed. All three outbound calls carry `timeout=TIMEOUT` (travel.py:257, 283, 301) and those are the only requests.* calls in the module. Failure shapes 500, 429, 403, malformed JSON, empty routes, ValueError-on-json, ConnectionError, TimeoutError and bare Exception are each parametrized and asserted individually. |
| P12 | PASS | test_collect_never_raises_and_reports_its_own_failure injects a raising detect_trips and asserts notices == [] and "Travel notices failed"/"detector blew up" in the error string; collect returns rather than propagating, so the rest of the briefing survives. |
| P13 | PASS | `-k detect` = 17 passed. Counted the fixtures: 10 negatives and 5 positives, each a separate parametrize case rather than a set-level scan, so one matching negative cannot hide behind the others. |
| P14 | PASS | plausible_drive bounds tested at 34/2/240 true and 1/241/600/None/""/"abc" false. test_implausible_drive_is_not_printed_as_a_leave_by drives build_notices at 600 and 1 minutes and asserts leave_by is None in both. |
| P15 | FAIL | `-k rerun` = 2 passed, so the no-duplicate-notice and no-second-Routes-call halves hold in memory. The stated ordering mechanism does NOT: collect() calls save_ledger at travel.py:601, inside morning_coffee's data assembly, while morning_coffee only emits its JSON at :1023 and the skill renders the briefing after that. So the ledger is written after the notice is BUILT but before it is RENDERED, and a crash mid-render does record a notice that never showed. The tests never exercise the crash-mid-render path they claim to protect. |
| P16 | PASS | `pytest tests/test_open_encoding.py tests/test_cross_platform.py tests/test_script_imports.py tests/test_no_cli_references.py -q` = 43 passed. |
| P17 | FAIL | Not reliably green. Observed 2 failing runs out of 32 full-suite runs: `7 failed, 1536 passed` twice, all 7 in tests/test_audit_ledger.py (test_md_carries_its_markers_and_a_count_line, test_md_shows_every_status_it_counts, and 5 parametrized test_md_row_count_always_equals_the_headline_count cases), plus one run reporting `1 failed, 1502 passed, 40 errors`. Collected test count also drifted 1540 -> 1543 -> 1564 -> 1565 across the session with no files added, and a collection diff pinned the drift to tests/test_vault_gardener.py::test_todays_own_pick_is_not_reported_back_as_history appearing and disappearing. No random-order plugin is installed, so ordering is deterministic; the instability comes from tests reading live machine state. The failures are outside travel's scope (travel is not the cause: `pytest --ignore=tests/test_travel.py` = 1450 passed, and the travel+audit_ledger pair = 122 passed), but the criterion as written is "the full suite passes" and I observed it not passing. |
| P18 | PASS | pyproject.toml:3 `version = "0.51.1"` and .claude-plugin/plugin.json:4 `"version": "0.51.1"` in lockstep. tests/test_plugin_manifest.py = 14 passed. CHANGELOG.md:27 carries `## 0.51.0 (2026-09-09)` with four bullets in the documented New:/Improved: format covering the travel feature. |
| P19 | PASS | test_skill_documents_the_keys_the_code_actually_emits passes and checks both directions: every field named in SKILL.md (kind, trip, text, link, leave_by, drive_minutes) is asserted present, and every key on a real built notice is asserted to be a subset of the documented set. SKILL.md:113 documents the `travel` shape, :162 the semantics, :295 the TRAVEL render block and its order (travel first, above meetings). |
| P20 | FAIL | Scanner is vacuous over the two central files of this change. dash_scan.py derives its input from `git diff HEAD`, which cannot see untracked files; `git diff HEAD --name-only | grep -c travel` = 0, so neither app/travel.py (604 lines) nor tests/test_travel.py (686 lines) contributed a single one of the 1210 lines it reported scanning, yet it printed "PASS: zero em or en dashes on added lines." Scanning those files directly (planted-instance check confirmed first) finds the dashes are all legitimate guard literals, so there is no voice defect, but the check does not verify what the criterion says it verifies. Separately, the criterion requires "a scanner using escapes" and dash_scan.py:12-13 assigns EM and EN as raw literal characters rather than \u2014 / \u2013 escapes, which is the exact self-clobbering failure the escape requirement exists to prevent. --selftest does pass on a planted instance. |
| P21 | FAIL | The criterion requires the matrix to run "the real `morning_coffee.py`". It never does: `grep -n morning_coffee objectives/travel-logistics/live_check.py` matches only lines 2 and 5, both docstring prose claiming it "imports app/travel.py and app/morning_coffee.py". The code imports only `travel` and calls travel.build_notices directly, so the morning_coffee integration path (travel_enabled gating, the full-calendar slice at morning_coffee.py:993, output["travel"] seeding, error plumbing) is never exercised. The run itself reports 6 of 6 scenarios pass with a correct cross-zone leave_by of 1:01 AM PST. CONTROL: I moved app/travel.py aside; the no-flag run went red (exit 1, "0 of 4 gradeable scenarios pass"), but both control paths are hardcoded print statements in an `except ImportError` branch rather than the identical checks re-executed, and `--control` itself only prints "CONTROL FAILS AS EXPECTED" and returns 0 without running any scenario. Module restored, sha256 0fe7b166...2a512, full suite re-run green. |

Overall: FAIL (14 of 21 gradeable items PASS, 7 FAIL, 0 NEEDS_HUMAN)

Mutation controls performed by the evaluator, both restored and hash-verified:
P5a (timezone conversion deleted -> 2 tests red) and P8 (address rendered into
notice text -> sentinel test red). app/travel.py returned to sha256
0fe7b166d2eacf16031c2823b504c7d36c529fb682a8ffca76b6b1ab2c42a512 after each.

## Lessons

Round 1 failed 7 of 21. Every one was a check that reported green over
something it had never actually examined, which is the failure this whole
scaffold exists to catch. Recorded here because the next build repeats them
by default.

1. A scanner built from `git diff HEAD` cannot see an untracked file, so it
   silently skips exactly the NEW files a feature adds. It printed PASS over
   1210 lines while contributing zero lines from the 604-line module it was
   written to check. Worse, the first fix still missed them: `git ls-files`
   prints paths relative to the CURRENT directory while the diff prints them
   relative to the repo root, and joining one onto the other built a path
   that did not exist, which an `except OSError: continue` then swallowed.
   Two rules: a scan must name the files it scanned and be proven on an
   instance planted IN THE REAL TARGET FILE, and a file a scanner cannot
   read is a hard error, never a silent skip.

2. A live end-to-end check that imports the feature's own module and calls it
   directly is a unit test wearing a live badge. It never exercised the
   integration path it existed to prove: the config gate, the calendar slice,
   the output key, the error plumbing. The fix runs the real entry point as a
   subprocess. Related: a control that prints "CONTROL FAILS AS EXPECTED"
   from inside an `except ImportError` and returns 0 proves nothing at all;
   a control must run the SAME scenarios the same way with the feature gone.

3. Running the control immediately exposed a real defect the passing tests
   never could: a missing travel module took down the ENTIRE briefing,
   because the import sat at module load. Moving it inside the call site made
   a broken feature cost only its own section. The control was worth more
   than the test it was guarding.

4. A test that overwrites a module-level global primed once per session for
   the whole suite (`config_loader._config`) leaks into whatever else is
   running, however faithfully monkeypatch restores it afterwards. Symptom
   was an intermittent failure in an unrelated file that passed 6 for 6 in
   isolation. Patch the narrow accessor, never the shared object.

5. Exception TEXT routinely quotes the arguments it failed on, and here those
   arguments were the home address and the API key. The string then flowed
   into a field the briefing renders and the web page publishes, past a
   redactor that strips credentials and file paths but not street addresses.
   An error surfaced to a user should carry the exception TYPE, not its text.

6. The fixture chosen to demonstrate a feature can quietly violate the rule
   the feature adds: the live-check event was titled "UA 523 to Newark",
   which the new booked-flight gate correctly read as already booked. A
   regression appeared in the check itself, not the code.

7. A contract item asserting that four fields are PRESENT says nothing about
   whether any of them is RIGHT. The red team caught this before the build:
   the original P5 would have passed green on a leave-by time three hours
   late. The split into P5a through P5e, with a mutation control on the
   timezone conversion, is what made it real.

### Evaluation 2026-09-09 12:16, round 2
Contract hash: verified (f2e4e3b0...cb1b0 recomputed == stored, rechecked at start and end)

| Item | Verdict | Evidence |
| P1 | PASS | `pytest tests/test_travel.py -q` = 109 passed (was 93). Named cases present for all four required shapes: test_timezone_leave_by_crosses_coasts, test_timezone_dst_boundary, test_timezone_midnight_rollover, test_no_trip_means_no_lookup_at_all. |
| P2 | PASS | `-k location` = 4 passed. week_review.py:435 (Google, `item.get("location","")`) and :466 (Outlook, `(item.get("location") or {}).get("displayName","")`) both carry it. test_detect_reads_location_never_title blanks location and asserts detect_trips returns []. |
| P3 | PASS | Round-1 defect fixed. `_BOOKED` regex at travel.py:86 matches confirmation/record locator/booking/PNR/ticket and flight numbers; travel.py:521 gates the link on `not trip.get("booked")`. Reran round 1's exact counterexample live: event "UA 523 SFO to EWR (Confirmation ABC123)" now yields `booked=True` and notices `[]`; unbooked control still emits the link. `-k flights` = 17 passed, including 4 parametrized booked shapes plus an unbooked control. Parsed-query assertion at test:306 uses urlparse/parse_qs, not a substring. |
| P4 | PASS | Round-1 defect fixed. Day table at test:278 covers offsets 5,4,3,2,1,0,-1 with weather only at 2. Coordinate check now exists: _haversine_km at test:821, KNOWN["EWR"]=(40.6895,-74.1745), test_forecast_resolves_within_100km_of_the_airport asserts km<100. MUTATION: I changed the stubbed geocode to Newark-on-Trent (53.0,-1.0); test went red with `assert 5476.298900197009 < 100`. Restored, sha256 624c17db...e271, 109 passed. |
| P5a | PASS | `-k timezone` = 6 passed. MUTATION CONTROL performed: travel.py:211 `return leave.astimezone(home_tz)` -> `return leave.replace(tzinfo=home_tz)`; result 2 failed 4 passed (test_timezone_leave_by_crosses_coasts, test_timezone_dst_boundary). Restored, sha256 5526a0b8...ad01, 6 passed. |
| P5b | PASS | POSITIVE_EVENTS (test:117) carries SFO, LAX and JFK fixtures differing in location, each parametrized individually; `-k detect` = 17 passed. |
| P5c | PASS | _terminal() at travel.py:184 reads only a Terminal match from the location; no guess table exists (grep found none). test_terminal_only_from_calendar asserts "SFO Airport, Terminal 3" -> "3" and "San Francisco Airport (SFO)" -> None. |
| P5d | PASS | test_routes_asks_for_the_departure_instant_not_now (test:408) captures the stubbed post body and asserts departureTime == "2026-11-12T12:01:00Z" (the computed instant), routingPreference == TRAFFIC_AWARE, and timeout == travel.TIMEOUT. |
| P5e | PASS | `grep -n "90" app/travel.py` returns nothing (exit 1). config_loader.travel_buffer_minutes() at :994 reads `airport_buffer_minutes` with default 90; test_defaults_hold_when_the_travel_block_is_junk covers the junk-value fallback. |
| P6 | PASS | `-k ledger` = 11 passed: same-notice suppression, different-kind-same-trip, 4 parametrized corrupt shapes, missing file, 30-day prune, disk roundtrip. |
| P7 | PASS | `-k tier` = 2 passed. test_tier2_control_the_same_event_with_a_location_does_produce_a_trip adds only `location` to the identical dict and asserts len==1, proving the Tier 2 fixture is not inert. |
| P8 | PASS | Both round-1 leak paths closed and verified live by me. Probe 1 (raising drive_lookup quoting address+key): notice text is now "Flight from SFO. Live drive time is unavailable, so allow your usual time.", leak=False; travel.py:562 swallows the exception without its text. Probe 2 (raising detector): collect now returns "Travel notices failed (RuntimeError)." at travel.py:637, leak=False. MUTATION CONTROL on all three guards at once (render home_address() into the notice, restore raw `{e}` in collect, re-raise inside the drive-lookup except): 3 tests went red (test_sentinel_address_never_appears_in_any_rendered_notice, test_a_raising_drive_lookup_does_not_leak_the_address, test_collect_error_carries_no_exception_text). Restored, sha256 5526a0b8...ad01. Ledger file is grepped at test:527. No home coordinate is ever computed or stored (`grep home_lat/home_coord/home_lon` returns nothing), so the 2-decimal clause has nothing to violate. |
| P9 | PASS | Accessors: test_config_back_compat.py:59 lists travel_enabled, travel_buffer_minutes, travel_weather_lead_days; 78 passed. Zero-network clause now exists as test_a_config_without_a_travel_block_makes_zero_network_calls (test_travel.py:887), stubbing both travel.requests.get and .post and asserting calls == []. I proved the counter is live by planting a real `travel.forecast("EWR")` call into that test: it went red at test:912. Restored. The test lives in test_travel.py rather than the file P9's verify line names, which is a location difference, not a missing check. |
| P10 | PASS | `-k cap` = 3 passed: cap refuses the 4th call, counter is per-day, junk counter tolerated. test_no_trip_means_no_lookup_at_all covers the zero-departure day. |
| P11 | PASS | `-k failure` = 11 passed. All three requests calls carry timeout=TIMEOUT (travel.py:269, 295, 313) and TIMEOUT=10 at :43; these are the only requests.* calls in the module. Shapes 500, 429, 403, malformed JSON, empty routes, ValueError-on-json each parametrized separately. |
| P12 | PASS | test_collect_never_raises_and_reports_its_own_failure injects a raising detect_trips, asserts notices == [] and "Travel notices failed" plus "RuntimeError" in the error, and asserts the raw text "detector blew up" is absent. |
| P13 | PASS | Counted the fixtures directly: NEGATIVE_EVENTS = 10, POSITIVE_EVENTS = 5, each a separate parametrize case. `-k detect` = 17 passed. |
| P14 | PASS | plausible_drive at travel.py:214 bounds the value; parametrized at both bounds; test_implausible_drive_is_not_printed_as_a_leave_by asserts leave_by is None at 600 and 1 minutes. |
| P15 | PASS | Round-1 defect fixed. collect() no longer saves (travel.py:622-628 returns a `commit` closure); morning_coffee.py:1038-1041 calls it AFTER `print(json.dumps(output...))` at :1035. test_collect_does_not_write_the_ledger_by_itself asserts written == [] before commit and [1] after. MUTATION: I moved `_travel_commit()` above the print in morning_coffee.py; test_morning_coffee_commits_only_after_printing went red. Restored (diff clean vs backup). `-k rerun` = 2 passed. |
| P16 | PASS | `pytest tests/test_open_encoding.py tests/test_cross_platform.py tests/test_script_imports.py tests/test_no_cli_references.py -q` = 43 passed. |
| P17 | PASS | Travel work is stable and regression-free. 8 consecutive full-suite runs, all identical: `1 failed, 1590 passed` in 17s, no count drift and no flaky test. Round 1's intermittent test_audit_ledger.py failures did not recur (that file alone: 32 passed x3; test_travel.py alone: 109 passed x5). BUILDER CLAIM VERIFIED BY ME: I moved app/travel.py and tests/test_travel.py out of the tree and cleared the pycache; tests/test_audit_evidence.py::test_a_limit_that_binds_the_ceiling_does_not_claim_it_cut_the_rows still failed identically (`assert 'cannot go above 10' in '| Context | 0 (up to 10 once judged) |'`, test:788), full suite `1 failed, 1481 passed`. Both files restored to sha256 5526a0b8...ad01 and 624c17db...e271. The one remaining failure belongs to the separate uncommitted vault-audit work, is not caused by and not fixable within this contract, and is recorded here as a note rather than a travel defect. |
| P18 | PASS | pyproject.toml:3 `version = "0.51.1"` and .claude-plugin/plugin.json:4 `"version": "0.51.1"` in lockstep. tests/test_plugin_manifest.py = 14 passed. CHANGELOG.md:27 carries `## 0.51.0 (2026-09-09)` with four bullets in the documented New:/Improved: format covering flights, the timezone conversion, detection and the private home address. |
| P19 | PASS | test_skill_documents_the_keys_the_code_actually_emits checks both directions (every documented field present, every emitted key a subset of the documented set) and passes. SKILL.md:113 documents the travel shape, :162 the semantics, :295 the TRAVEL render block, :297 the order (travel first, above meetings). |
| P20 | PASS | Round-1 vacuity fixed. dash_scan.py:14-15 now defines EM/EN as "—"/"–" escapes, and added_lines() reads untracked files in full rather than relying on `git diff HEAD` alone. I instrumented it: app/travel.py contributes 638 lines and tests/test_travel.py 922 lines to the 7924 scanned, across 44 files. --selftest passes on a planted instance. I also planted a real em-dash into app/travel.py itself: the scan reported `FAIL: 1 added lines carry an em or en dash: app/travel.py: # planted <emdash> dash`. Restored to sha256 5526a0b8...ad01. Runtime half: _strip_dashes at travel.py:100 uses escapes and wraps all 5 rendered text sites (:509, :529, :579, :590, :598); test_no_dashes_in_rendered_notice_text plus a planted control pass. |
| P21 | PASS | Round-1 defect fixed. live_check.py now spawns the real entry point as a subprocess (`subprocess.run([sys.executable, runner])` at :177, RUNNER does `import morning_coffee as mc` and calls `mc.main()`), so travel_enabled() gating, the calendar handoff, the seeded "travel" key and the error plumbing are all exercised. Real run: 5 of 5 scenarios pass (no trip -> travel=[]; 5 days -> ['flights']; 2 days -> ['weather']; departure day -> ['departure'] with leave_by None and "usual time" on a refused sentinel key; no address or key in the emitted JSON). CONTROL: `--control` now moves app/travel.py aside and re-runs the SAME scenarios rather than printing a hardcoded line; result b, c and d all FAIL (kinds=[]), a and e pass by design, "CONTROL SATISFIED". travel.py restored automatically, sha256 5526a0b8...ad01 confirmed. I separately verified the contract's re-run scenario myself by calling run_briefing twice against one persistent state dir: run1 kinds=['departure'], run2 kinds=[], so ledger suppression does hold through the real subprocess path even though the shipped matrix labels its fifth scenario as the leak check instead. |

Overall: PASS (21 of 21 gradeable items PASS, 0 FAIL, 0 NEEDS_HUMAN)

Mutation controls performed by the evaluator this round, each restored and
hash-verified: P4 (geocode moved to the wrong continent -> distance test red),
P5a (timezone conversion deleted -> 2 tests red), P8 (all three leak guards
removed at once -> 3 tests red), P9 (a real network call planted -> zero-call
test red), P15 (commit moved above the print -> ordering test red), P20 (an
em-dash planted in app/travel.py -> scanner reported the hit by file and line).
app/travel.py returned to sha256
5526a0b8d197a9cabbd489f92a5564a9a6d72fc2d9eacbc947de2fec8b38ad01,
tests/test_travel.py to
624c17dbcd6c65b37beb1ffe6a34abf1851db5d01498e548737ff07fa0e3e271,
and app/morning_coffee.py byte-identical to its pre-evaluation backup.

Noted outside this contract: tests/test_audit_evidence.py::test_a_limit_that_
binds_the_ceiling_does_not_claim_it_cut_the_rows fails with the travel feature
entirely absent from the tree. It belongs to the separate vault-audit work in
this same working tree and needs its own fix.
