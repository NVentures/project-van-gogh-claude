# The Four C's

Project Van Gogh is four things, and every part of it belongs to exactly one.

| | What it is | Where it lives |
|---|---|---|
| Context | The second brain. Everything Van Gogh learns or produces is written down there, so the next run starts from more than the last one did. | The Obsidian vault |
| Connectors | The systems it can read: mail, calendar, a notetaker, the books, and any other app connected in Claude. | `app/data_sources.py`, `app/notetaker.py`, `app/connector_fetch.py`, `app/connector_lookup.py` |
| Capabilities | The things it can do: every skill, and the intelligence layer that knows which of them exist and calls on them. | `skills/`, `agents/`, `app/advisor.py` |
| Cadence | When each capability fires without being asked. | `app/job_registry.py`, graded by `app/job_watch.py` |

```
                 CADENCE
        [job_registry: one row per schedule]
        [job_watch: did it run, did it deliver]
                    |
                    v
               CAPABILITIES
        [skills]   [agents]   [advisor: what is worth doing next]
           |                      |
           v                      v
      CONNECTORS              CONTEXT
   [mail, calendar]  ---->  [the vault: entities, meetings,
   [notetaker]              deals, briefings, logs]
   [books]                        ^
   [any Claude connector]         |
           |                      |
           +---- what was read ---+
                 and what was made
                 is written back
```

The rule that holds it together: a capability is finished when it reads the
context, reaches what it needs through a connector, writes what it produced
back to the context, and has a cadence. A capability with no cadence is one
somebody has to remember to run.

A person sits at three gates and nowhere else: connecting a system (sign-in
happens in a browser), answering yes or no to work the advisor proposes, and
saying send. Nothing is sent to anyone without the last one.

## Context

The vault is the only memory. Capabilities read it before they act and write
to it when they finish.

| What | Where in the vault | Written by |
|---|---|---|
| People and companies | `wiki/entities/` | meeting-ingest, warm-start, operator-research |
| Meetings | `wiki/sources/` and the monthly catalog | meeting-ingest, calendar-stub-check |
| Live deals and the user's open promises | `wiki/hotcache.md` | meeting-ingest, morning-coffee, warm-start |
| The week | `wiki/weekly/` | week, week-retro |
| The briefings as last rendered | `van-gogh/morning-coffee.md`, `afternoon-tea.md`, `week.md`, `meeting-prep.md` | each briefing |
| What ran and whether it delivered | `van-gogh/logs/` | every scheduled run |
| Voice | the voice guide and tone profile | the voice skills |
| Settings | `van-gogh/config.json` | update-settings, add-account, add-connector |

`app/context_pack.py` is how a capability asks the context about a person: it
returns their page, recent meetings, open commitments and deal threads, each
with the path it came from.

## Connectors

| System | How it is reached | Runs with nobody there |
|---|---|---|
| Gmail, Google Calendar, Google Contacts | The user's own sign-in (`app/google_client.py`) | Yes |
| Outlook mail and calendar | The user's own sign-in (`app/microsoft_client.py`) | Yes |
| Meeting notetaker (Granola or Grain) | Its API key (`app/notetaker.py`) | Yes |
| QuickBooks | A Claude connector, read exactly (`app/connector_fetch.py`) | Yes |
| Any other system connected in Claude (a CRM, a help desk) | A Claude connector, asked about a person (`app/connector_lookup.py`, set up with `/van-gogh:add-connector`) | Yes |
| Slack, Teams, HubSpot for the 5:15 report | Claude connectors used in a chat | No |

Two ways to read a Claude connector, by what happens to the answer. A number
that is summed or acted on gets an exact read, with code written for that
service. A question a person reads the answer to ("what does this system hold
about this attendee") gets a lookup, which needs no code per service.
`skills/_shared/connectors.md` has the detail.

## Capabilities and their cadence

Every skill, and when it fires. "On demand" means nothing runs it unless a
person does.

| Capability | What it does | Cadence |
|---|---|---|
| morning-coffee | The day's briefing | Weekdays 7:00 AM, emailed |
| afternoon-tea | The end of day retro | Weekdays 2:30 PM, emailed |
| week | The week's priorities | Monday 6:30 AM, emailed |
| week-retro | The week in review, with the scorecard | Friday 3:30 PM, emailed |
| meeting-prep | A prep for each call, or one for the day | Checked every 15 minutes: one email at 6:15 AM for the day, or one an hour before each call |
| meeting-ingest | Files meeting notes into the vault | Daily 4:30 AM |
| ingest-workspace | Syncs project notes into the vault | Daily 4:50 AM |
| note | Tells you when something moved between briefings | Every 15 minutes |
| suggest | The advisor: proposes work, does it on a yes | Daily at 6:00 AM, through the watcher |
| finance-brief | Where the money stands | Monday 6:45 AM, emailed |
| contact-capture | Saves the people you write to as contacts | Sunday 5:00 AM |
| workbench | The local dashboard | Starts at login |
| check-updates | Looks for a newer version | Once a day, inside any run |
| kpi-scorecard | Whether any of this is working | Inside week-retro |
| vault-audit | Vault hygiene and a scored self-audit | Inside week-retro |
| follow-up-radar | Follow-ups that have come due | Inside the four briefings |
| calendar-stub-check | Stubs meetings nobody recorded | Daily 5:05 AM |
| relationship-radar | Contacts going cold | Monday 5:20 AM |
| five-fifteen | Weekly client reports, left as drafts | Friday 1:00 PM |
| voice-calibration | Learns from the week's sent mail | Sunday 6:00 PM |
| voice-generator | A tone profile from sent mail | Sunday 5:40 AM |
| voice-bootstrap | A first voice guide | On demand, once |
| warm-start | Seeds a fresh vault from mail and meetings | At setup |
| operator-research | Who the user is, from the public web | At setup |
| release-notes | What changed in the last update | On demand |

Setting things up and changing them, which have no cadence because they are
answers to a question: install-van-gogh, uninstall-van-gogh, update-van-gogh,
migrate-from-legacy-van-gogh, add-account, remove-account, add-connector,
update-settings, update-digest-preferences, create-skill.

Times are the defaults, in the computer's own time zone, and each one is a
setting. All of them are off until the user asks for them, except the two
ingests and the watcher.

Every capability that does recurring work has a cadence. The ones marked on
demand or at setup are answers to a question, or happen once.

## The schedule itself

Every scheduled job is one row in `app/job_registry.py`. The row is what
installs it, removes it, reports on it, grades it and offers it to the user.

| Job | Runs | Fires |
|---|---|---|
| meeting-ingest | the meeting-ingest skill | Daily 4:30 AM |
| ingest-workspace | the ingest-workspace skill | Daily 4:50 AM |
| job-watch | the watcher, then the advisor | Every 30 minutes and at login |
| digest-morning-coffee | Morning Coffee, mailed | Set in `digest` |
| digest-afternoon-tea | Afternoon Tea, mailed | Set in `digest` |
| digest-week | Week, mailed | Set in `digest` |
| digest-week-retro | Week Retro, mailed | Set in `digest` |
| finance-email | the finance brief, mailed | Set in `finance` |
| contact-capture | contact capture | Set in `contact_capture` |
| prep-email | meeting prep, mailed | Every 15 minutes, set in `meeting_prep` |
| note | the Note tick | Every 15 minutes, set in `notes` |
| calendar-stub-check | the calendar-stub-check skill | Set in `cadence` |
| relationship-radar | the relationship-radar skill | Set in `cadence` |
| five-fifteen | the five-fifteen skill | Set in `cadence` |
| voice-calibration | the voice-calibration skill | Set in `cadence` |
| voice-generator | the voice-generator skill | Set in `cadence` |
| workbench | the Workbench server | At login |

The watcher is what makes a cadence a promise: every 30 minutes it compares
what was due against what was delivered, restarts what failed, and tells the
user about what it cannot fix.

## Adding to it

- A new capability: a skill under `skills/`, a script under `app/`, and a row
  in the capabilities table above.
- Its cadence: a row in `app/job_registry.py`. `tests/test_job_registry_guard.py`
  will not let a row be half added.
- A new system to read: `/van-gogh:add-connector` when a person reads the
  answer, a module beside `app/finance_brief.py` when code does.
- What it produces: written to the vault, in a place listed under Context.

`tests/test_four_cs.py` fails when a skill or a scheduled job exists that this
page does not name.
