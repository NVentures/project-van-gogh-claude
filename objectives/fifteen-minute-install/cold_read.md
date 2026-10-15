# Cold read (P20): the user-visible text of the install

A fresh agent that knows nothing about the build reads ONLY the rendered text
below and the pre-call checklist (`templates/pre-call-checklist.md`) and
answers four questions: does this read like one writer; name the three worst
lines verbatim; does any line tell the reader to do something they cannot do
from where they sit (unactionable); does anything contradict anything else.
The evaluator runs it twice: first on the planted copy made by
`cold_read_plant.py --plant OUT.md` (it must catch both plants), then on the
real text (it must find none).

The text between the markers is written by code, never by hand:

```bash
.venv/bin/python objectives/fifteen-minute-install/cold_read_plant.py --render
.venv/bin/python objectives/fifteen-minute-install/cold_read_plant.py --write-md
```

`--render` prints it; `--write-md` puts it here. `tests/test_setup_adopt.py`
fails when this file and the render differ, when any error line is longer
than 120 characters, or when a line names the resume command without saying
"in Claude". The client in it is invented (Jane Doe, Acme Energy) and matches
the sample pre-call checklist.

## Where each line comes from

| Surface | Source |
|---|---|
| The five prompts | `setup_flow.notetaker_prompt_spec`, `confirm_prompt_spec`, `schedule_prompt_spec`, `priorities_prompt_spec`, `deals_prompt_spec` |
| The sixth prompt, `account_mismatch` | `setup_flow.mismatch_prompt_spec` |
| Re-ask reasons | the `reason` argument of `setup_flow._prompt`, which leads the question |
| Wait and dispatch lines | `setup_flow.SAY_SCAN`, `SAY_MEETINGS`, `SAY_BRIEFINGS` |
| Error lines | `setup_flow.ERROR_LINES` and `SIGNIN_LINES`, through `all_error_lines` |
| Closing text | `setup_flow.closing_text`, `undone_line`, `unfinished_prefix`, `ALREADY_INSTALLED` |
| The reminder line | `setup_state.one_line`, and the same words in `hooks/setup_notice.sh` and `hooks/setup_notice.ps1` |
| The reminder email body | `setup_nudge.body_for` |

## The real text

<!-- render:begin (written by cold_read_plant.py --write-md, do not edit by hand) -->
```text
# The setup line (on the pre-call checklist, under "The setup line")

Mac, in Terminal:
claude plugin marketplace add https://github.com/NVentures/project-van-gogh-claude.git; claude plugin marketplace update van-gogh; sh ~/.claude/plugins/marketplaces/van-gogh/setup.sh

Windows, in PowerShell:
claude plugin marketplace add https://github.com/NVentures/project-van-gogh-claude.git; claude plugin marketplace update van-gogh; powershell -ExecutionPolicy Bypass -File "$HOME\.claude\plugins\marketplaces\van-gogh\setup.ps1"

# The five prompts, in the order they are asked

## notetaker (header: Notetaker)
Which meeting notetaker do you use?
- Granola: You paste its API key next. Get one from the Granola desktop app (needs a Business or Enterprise plan): Settings → Connectors → API keys → Create new key → choose the note access scopes → Generate API Key.
- Grain: You paste its API key next. Get one from Grain: Account settings → Integrations → Personal API (needs a Starter plan or above; the Free plan has no API access).
- None, or another notetaker: Van Gogh reads Granola and Grain today. Briefings still work, without meeting notes.

## confirm (header: Your details)
Name: Jane Doe (Jane)
Company: Acme Energy
Timezone: America/New_York
Notes folder: /Users/jane/van-gogh-vault (where Van Gogh keeps its pages)
Accounts: jane@acme-energy.example
Does this look right?
- Yes, continue: Saves these settings and creates the notes folder.
- Change something: Say what is wrong, for example the timezone or the notes folder.

## schedule (header: Schedule)
Shown before the question:
What runs by itself on this computer, by its own clock.

ON (13)
  Meeting notes filing       every hour                            files your recorded meetings in the vault
  Project notes sync         every day at 4:50 AM                  copies your project notes into the vault
  To-do check-off            every day at 5:10 AM                  closes the to-dos your mail and meetings show are done
  Job check                  every 30 minutes                      restarts any of these that failed
  Morning Coffee             weekdays at 7:00 AM                   your morning briefing, by email
  Afternoon Tea              weekdays at 2:30 PM                   your end of day briefing, by email
  Week                       Mondays at 6:30 AM                    the plan for your week, by email
  Week Retro                 Fridays at 3:30 PM                    how the week went, by email
  Meeting prep               one email each morning at 6:15 AM     who you are meeting, by email; or one before each call
  Relationship radar         Mondays at 5:20 AM                    who is going cold
  Writing review             Sundays at 6:00 PM                    learns from the mail you sent this week
  Tone profile refresh       Sundays at 5:40 AM                    rebuilds the tone your drafts are written in
  Workbench                  when you log in                       your briefings as a page on this computer

OFF (5)
  Finance brief              Mondays at 6:45 AM                    the week's numbers, by email
  Contact saving             Sundays at 5:00 AM                    adds new people to your address book
  Watcher between briefings  every 15 minutes, 7:30 AM to 6:00 PM  mails you when something urgent lands
  Unrecorded meeting check   every day at 5:05 AM                  finds meetings nobody took notes on
  Client reports             Fridays at 1:00 PM                    drafts your weekly client reports, never sends

Keep these, or change something first?
- Keep these: Scheduled on this computer as shown. You can ask for a change at any time later.
- Change something: A different time or day, or something turned on or off. You say it in your own words.
- Schedule nothing: No briefing is emailed and nothing runs by itself. Every skill still works when you ask for it.

## priorities (header: Priorities)
What matters most right now in Acme Energy? Tick any that are right, and type your own if they are missing. This is what ranks your briefings.
- Close the deals already in motion: Briefings rank replies on live deals first.
- Bring in new business: Briefings put new introductions and first replies near the top.
- Hire or build the team: Briefings flag candidates and team threads ahead of routine mail.

## deals (header: Deals)
Which of these are real deals? Nothing is ticked to start. Tick each one that is a real, live deal; only ticked ones are tracked.
- Harbor Point solar PPA: Dana Reyes, negotiating, last contact September 28
- Northwind storage lease: Lee Park, proposal, last contact September 20
- None of these: Pick this alone when none are real deals.

# Asked only when the signed-in address differs from the key file

## account_mismatch (header: Account)
You signed in as jane@gmail.example, but the key file expected jane@acme-energy.example. Use this account anyway?
- Use this account anyway: Briefings read jane@gmail.example.
- Sign in again: Open the sign-in window again and pick jane@acme-energy.example.

# Asked only when no browser could open on this computer

## signin_link (header: Sign in)
No browser opened on this computer, so sign in to Google by link.
1. Open this address in a browser. One on this computer is easiest, because you copy an address back from it:
https://accounts.google.com/o/oauth2/auth?client_id=EXAMPLE&state=EXAMPLE
2. Sign in and approve.
3. The page you end on looks like an error ("This site can't be reached"). That is expected. Copy the full address of that page.
4. Choose "I signed in"; you paste the address next.
- I signed in: You paste the copied address next.
- Not now: Setup stops here and nothing is lost; you can come back to it.

## signin_link (header: Sign in)
No browser opened on this computer, so sign in to Microsoft with a code.
1. On any device, go to https://microsoft.com/devicelogin
2. Type this code: ABCD-EFGH
3. Sign in and approve, then come back here.
- Done, I signed in: Van Gogh checks the sign-in and carries on.
- Not now: Setup stops here and nothing is lost; you can come back to it.

# When a prompt is asked again, the reason leads the question

- That was not one of the choices.
- The Granola key was empty. Paste it as your reply.
- That Granola key did not work. Paste it again, or pick "None, or another notetaker".
- Updated.
- Say what to change, or choose Yes, continue.
- Still missing: your timezone. What is it?
- Still missing: your first name and your timezone. What are they?
- First, tell me your timezone.
- For your timezone, the city you live in is enough.
- I could not place Springfield. Type the largest city near you that shares your clock, for example Chicago, London or Singapore.
- The folder /Users/jane/van-gogh-vault could not be created. Choose Change something and give another folder.
- The folder /Users/jane/van-gogh-vault cannot be written to. Choose Change something and give another folder.
- That was not a web address. Paste the full address of the page you ended on.

# Wait and dispatch lines

reading your mail...
filing your meetings...
two briefings being written

# Error lines

- Your key file is not in Downloads. Save it there, then in Claude, type /van-gogh:install-van-gogh
- No key file yet. Ask the Van Gogh team for it, save it in Downloads, then in Claude, type /van-gogh:install-van-gogh
- No Python yet. Paste your checklist's setup line in Terminal (PowerShell on Windows); it reopens setup in Claude.
- No plugin yet. Paste your checklist's setup line in Terminal (PowerShell on Windows); it reopens setup in Claude.
- No account is signed in yet. To sign in, in Claude, type /van-gogh:install-van-gogh
- Van Gogh could not read your Google account just now. To sign in again, in Claude, type /van-gogh:install-van-gogh
- Sign-in is not finished. To come back to it, in Claude, type /van-gogh:install-van-gogh
- The background jobs did not install. To retry, in Claude, type /van-gogh:install-van-gogh
- The mail scan stopped early. To run it again, in Claude, type /van-gogh:install-van-gogh
- Your accounts could not be written to the settings. To sign in again, in Claude, type /van-gogh:install-van-gogh
- Deals could not be read from the scan. To retry, in Claude, type /van-gogh:install-van-gogh
- The ticked deals could not be saved. To retry, in Claude, type /van-gogh:install-van-gogh
- 2 ticked deals were not saved. To retry, in Claude, type /van-gogh:install-van-gogh
- The meeting notes were not filed. To try again, in Claude, type /van-gogh:install-van-gogh
- The first briefings were not written. To retry, in Claude, type /van-gogh:install-van-gogh
- A step took too long and was stopped. To continue, in Claude, type /van-gogh:install-van-gogh
- Settings did not save. To retry, in Claude, type /van-gogh:install-van-gogh; if it repeats, write to team@example.com
- The sign-in link could not be made on this computer. Write to team@example.com and we will sign you in another way.
- Your IT admin must approve Van Gogh for Google first. Once they have, in Claude, type /van-gogh:install-van-gogh
- The Google sign-in was cancelled. To try again, in Claude, type /van-gogh:install-van-gogh
- Google blocked this account. Write to team@example.com; once approved, in Claude, type /van-gogh:install-van-gogh
- The Google sign-in left a permission unticked. Tick every box next time: in Claude, type /van-gogh:install-van-gogh
- The Google sign-in got no answer in time. To try again, in Claude, type /van-gogh:install-van-gogh
- Save the key file we sent you into your Downloads folder, then in Claude, type /van-gogh:install-van-gogh
- Google did not finish the sign-in. To sign in again, in Claude, type /van-gogh:install-van-gogh
- Google refused the sign-in. To try again, in Claude, type /van-gogh:install-van-gogh
- Your IT admin must approve Van Gogh for Microsoft first. Once they have, in Claude, type /van-gogh:install-van-gogh
- The Microsoft sign-in was cancelled. To try again, in Claude, type /van-gogh:install-van-gogh
- Microsoft blocked this account. Write to team@example.com; once approved, in Claude, type /van-gogh:install-van-gogh
- The Microsoft sign-in left a permission unticked. Tick every box next time: in Claude, type /van-gogh:install-van-gogh
- The Microsoft sign-in got no answer in time. To try again, in Claude, type /van-gogh:install-van-gogh
- Save the key file we sent you into your Downloads folder, then in Claude, type /van-gogh:install-van-gogh
- Microsoft did not finish the sign-in. To sign in again, in Claude, type /van-gogh:install-van-gogh
- Microsoft refused the sign-in. To try again, in Claude, type /van-gogh:install-van-gogh

# Closing text

## A complete run
Installed in 14 minutes 45 seconds.
Your first briefing email arrives Friday at 7:00 AM. If it is not there by 9:00 AM, open Claude and type /van-gogh:morning-coffee, or write to team@example.com.
Your chief of staff is Vincent. In any Claude session, type /van-gogh:vincent and ask for what you need.
Your meetings from the last 60 days are filed. Next, Van Gogh gets the rest of your meeting history from your notetaker, a few meetings an hour, newest first, while this computer is on. Nothing for you to do.
You can close this window.

## The client answered "Schedule nothing"
Installed in 12 minutes 10 seconds.
Your chief of staff is Vincent. In any Claude session, type /van-gogh:vincent and ask for what you need.
Nothing is scheduled, as you chose: no briefing is emailed and nothing runs by itself. To turn something on, ask Vincent to schedule it.
For help, email team@example.com.
You can close this window.

## The client ticked "None of these" and has no notetaker
Installed in 11 minutes 30 seconds.
Your first briefing email arrives Friday at 7:00 AM. If it is not there by 9:00 AM, open Claude and type /van-gogh:morning-coffee, or write to team@example.com.
Your chief of staff is Vincent. In any Claude session, type /van-gogh:vincent and ask for what you need.
No deals are tracked yet, because none were ticked. In Claude, /van-gogh:warm-start proposes them again.
No meeting notes are filed, because no notetaker was connected. When you have one, in Claude, type /van-gogh:update-settings; it asks for the key.
You can close this window.

## One meeting was too long to read during the install
Installed in 13 minutes 20 seconds.
Your first briefing email arrives Friday at 7:00 AM. If it is not there by 9:00 AM, open Claude and type /van-gogh:morning-coffee, or write to team@example.com.
Your chief of staff is Vincent. In any Claude session, type /van-gogh:vincent and ask for what you need.
1 long meeting was filed from your notetaker's own summary for now; Van Gogh reads the full recording overnight while this computer is on.
Your meetings from the last 60 days are filed. Next, Van Gogh gets the rest of your meeting history from your notetaker, a few meetings an hour, newest first, while this computer is on. Nothing for you to do.
You can close this window.

## Two were, for a client who answered "Schedule nothing"
Installed in 12 minutes 40 seconds.
Your chief of staff is Vincent. In any Claude session, type /van-gogh:vincent and ask for what you need.
Nothing is scheduled, as you chose: no briefing is emailed and nothing runs by itself. To turn something on, ask Vincent to schedule it.
2 long meetings were filed from your notetaker's own summary for now. To have the full recordings read, in Claude, type /van-gogh:meeting-ingest
For help, email team@example.com.
You can close this window.

## The usage limit was reached while meetings were being filed
Setup is almost done: 6 of your 10 meetings are filed. The other 4 are waiting because your Claude plan's usage limit was reached, and they file by themselves overnight while this computer is on. To check on them, in Claude, type /van-gogh:install-van-gogh
Your first briefing email arrives Friday at 7:00 AM. If it is not there by 9:00 AM, open Claude and type /van-gogh:morning-coffee, or write to team@example.com.
You can close this window.

## The same, for a client who answered "Schedule nothing"
Setup is almost done: 6 of your 10 meetings are filed. The other 4 are waiting because your Claude plan's usage limit was reached. To file them once the limit resets at 3:40 PM, in Claude, type /van-gogh:install-van-gogh
Your chief of staff is Vincent. In any Claude session, type /van-gogh:vincent and ask for what you need.
Nothing is scheduled, as you chose: no briefing is emailed and nothing runs by itself. To turn something on, ask Vincent to schedule it.
For help, email team@example.com.
You can close this window.

## The usage limit was reached while the mail was being read, with no reset time given
Setup is almost done. Reading your mail stopped because your Claude plan's usage limit was reached. To pick up where it stopped once the limit resets (Claude shows the time when you type there), in Claude, type /van-gogh:install-van-gogh
Your first briefing email arrives Friday at 7:00 AM. If it is not there by 9:00 AM, open Claude and type /van-gogh:morning-coffee, or write to team@example.com.
Your meetings from the last 60 days are filed. Next, Van Gogh gets the rest of your meeting history from your notetaker, a few meetings an hour, newest first, while this computer is on. Nothing for you to do.
You can close this window.

## Run again on a finished install
Van Gogh is already installed on this computer. Nothing to do.

# The reminder line (session start, any Van Gogh command, the reminder email)

Van Gogh setup: 9 of 9 install steps left. Next: paste your checklist's setup line in Terminal (PowerShell on Windows).
Van Gogh setup: 8 of 9 install steps left. Next: paste your checklist's setup line in Terminal (PowerShell on Windows).
Van Gogh setup: 7 of 9 install steps left. Next: install your key file. In Claude, type /van-gogh:install-van-gogh
Van Gogh setup: 6 of 9 install steps left. Next: sign in to your accounts. In Claude, type /van-gogh:install-van-gogh
Van Gogh setup: 5 of 9 install steps left. Next: confirm your settings. In Claude, type /van-gogh:install-van-gogh
Van Gogh setup: 4 of 9 install steps left. Next: schedule your briefings. In Claude, type /van-gogh:install-van-gogh
Van Gogh setup: 3 of 9 install steps left. Next: tick your real deals. In Claude, type /van-gogh:install-van-gogh
Van Gogh setup: 2 of 9 install steps left. Next: file your meetings. In Claude, type /van-gogh:install-van-gogh
Van Gogh setup: 1 of 9 install steps left. Next: see your first briefings. In Claude, type /van-gogh:install-van-gogh
```
<!-- render:end -->
