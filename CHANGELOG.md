# What's new in Van Gogh

Plain-language notes for each release, newest first. These are written for the
person *using* the product, not the person building it: no file names, no
function names, no jargon. Each entry says what got better in words a
non-technical reader can act on. `app/plugin_update.py` reads this file to show
"what's new" after an update, so the format is load-bearing:

- One `## <version> (<YYYY-MM-DD>)` heading per release, newest first.
- Bullets under it, each starting `New:`, `Improved:`, or `Fixed:`.
- No dashes as punctuation inside the text (the interface voice rules apply,
  see DESIGN.md); use commas, colons, or two sentences.

## 0.80.2 (2026-10-01)

- Improved: the guide now covers connecting your CRM, everything that can
  run by itself and when, and the new meeting prep email.

## 0.80.1 (2026-10-01)

- Improved: in a meeting prep, the section that was headed Deal context is
  now headed Context.

## 0.80.0 (2026-10-01)

- New: the meeting prep email has a proper layout. The day opens with every
  call on one screen, time beside title. Each call then gets its own card,
  with who is on it, what was last said, and what is still open set out under
  small labels with space between them.
- New: the one line worth reading if you read nothing else, what you want out
  of the call, is set apart in its own block on every card.
- Improved: a person's name stands out on their line, and a first meeting is
  marked beside the name.
- Improved: it reads properly on a phone, without sideways scrolling.

## 0.79.2 (2026-10-01)

- Improved: Morning Coffee now says First meeting for someone you have not
  met before, the same words the prep email uses.

## 0.79.1 (2026-10-01)

- Improved: the morning prep email is now titled Daily Meeting Prep with the
  date, for example Daily Meeting Prep: Oct 01, 26, so every morning's email
  sorts and searches as one series.
- Improved: when you have never met someone before, the prep says First
  meeting, once, followed by whatever is known about them. It no longer
  repeats that there are no notes on them in several places.
- Improved: a prep only shows the sections that have something in them. A
  heading over a line saying there is nothing is left out, and so is a CRM
  section when your CRM holds nothing on anyone on the call.

## 0.79.0 (2026-10-01)

- New: five things that only ran when you asked can now run by themselves.
  The check for meetings nobody recorded (every morning), the look at who is
  going cold (Monday), your weekly client reports (Friday afternoon, left as
  drafts), and the two that keep drafts sounding like you (Sunday). Each is
  off until you turn it on, and each time is yours to change.
- New: none of them sends anything. The most any of them leaves is a draft or
  a page in your vault.
- Improved: when your tone profile is refreshed, the one it replaces is kept
  beside it, so a refresh you do not like can be undone.
- Improved: every one of these is watched like your briefings are. If one
  does not run, it is restarted, and you are told about what cannot be fixed.

## 0.78.1 (2026-10-01)

- Fixed: on a day with no calls, the morning prep now checks your calendar
  once and stops. It was looking again every fifteen minutes until midnight.

## 0.78.0 (2026-10-01)

- New: a meeting prep now reads your live mail. For each person on the call
  it shows the recent threads with them and who wrote last, so you walk in
  knowing where the conversation actually stands, not where your notes left
  it.
- New: you can connect your CRM, or any other system you already use in
  Claude, with /van-gogh:add-connector. You choose it once, Van Gogh proves
  it works with one real lookup, and from then on each prep includes what
  that system holds on the people you are about to meet. It only ever reads,
  and what it finds is shown as coming from that system.
- New: a prep for the whole day. Ask for today's calls and you get one brief
  covering each of them in order, which is also what the morning prep email
  now sends.
- New: every prep that is emailed is kept in your vault next to your
  briefings, so the rest of Van Gogh can see what you were told.
- Improved: on a busy day the small outside calls are prepared first. A
  large internal meeting can no longer use up the lookups and leave a one to
  one with nothing.
- Improved: a prep says plainly when it could not read a mailbox or a
  connected system, and never reports that as there being nothing to find.
- New: the product is now described as four things, Context, Connectors,
  Capabilities and Cadence, with every skill and every scheduled job placed
  under one of them.

## 0.77.1 (2026-10-01)

- Fixed: meetings filed at the start of a new month are listed in that
  month's source catalog again. The catalog for a new month was never
  created, so from the first of each month every meeting was filed but left
  off the list, and a vault that never had a catalog was never given one.
  The first meeting filed in a month now starts the list.
- Fixed: a meeting note now keeps straight who promised what. With some
  notetakers your own promises were being filed under what others owe you,
  which left your own list empty.
- Fixed: a meeting no longer updates a deal just because its title shares a
  common word with it, such as strategy or expansion.

## 0.77.0 (2026-09-30)

- New: Van Gogh can now suggest work and do it for you. Once a day it reads
  your whole vault, picks at most a few things it could take off your plate,
  and asks. Each one says why it matters, exactly what it would do, and the
  line in your own notes it is based on.
- New: the same suggestions appear inside Morning Coffee and Afternoon Tea,
  under their own heading, and Afternoon Tea also says what was done that day
  on your yes.
- New: you answer with a yes or a no. Reply to the email or the briefing with
  Y or N and the number, press KICK OFF or Dismiss in the Workbench, or type
  /van-gogh:suggest in a chat. A yes leaves a draft in your Drafts folder, a
  file, or a small correction to a page in your vault, ready for you to
  check. A no is remembered, so the same thing is not suggested twice.
- New: nothing is ever sent on a yes. If you want a draft it wrote to go out,
  that is a second, separate instruction, and replying SEND by email stays
  switched off until you turn it on.
- New: suggestions are off until you ask for them. Ask Van Gogh to turn on
  daily suggestions, or run /van-gogh:suggest any time to get them on the
  spot.

## 0.76.1 (2026-09-30)

- Fixed: a briefing that was taking a long time to build could be stopped and
  started over by the check that is meant to rescue failed jobs. A job that is
  still working is now left alone until it finishes.
- Fixed: when a briefing stopped because an account needed signing in again,
  the note said it would restart once you did, and it did not. It now checks
  each half hour and restarts the briefing as soon as the account works.
- Fixed: a briefing that was switched on but had lost its place in your
  computer's schedule stopped arriving without a word. The next briefing you
  do get now says so, and tells you how to set it back up.

## 0.76.0 (2026-09-30)

- New: when a new version of Van Gogh is published, your next briefing ends
  with one question asking whether to install it. Say yes and it installs,
  then loads the next time you open Claude Code. Say not now and it stays
  quiet until there is a newer version. Nothing is ever installed without
  your say, and emailed briefings never ask.
- Fixed: asking Van Gogh to update itself could report success while leaving
  you on the old version. It now installs the new version for real, and says
  so plainly when it could not.

## 0.75.7 (2026-09-29)

- Fixed: filing a meeting could touch a deal because one attendee shared a
  first name with that deal's contact. An all hands with a Maria on it marked
  a closed deal run by a different Maria as recently contacted. A first name
  on its own no longer counts as a match; a full name or a matching meeting
  title still does.
- Fixed: a nickname for one of your contacts was being linked to other people
  with the same first name, so "Ben Hale" showed up as a link to Ben Carter.
  A first name followed by a different surname is now left alone.

## 0.75.6 (2026-09-24)

- Fixed: filing a meeting could mark an unrelated deal as dead. A call about
  a "data center strategy" was matched to a different data center deal on
  those two common words alone, and a passing mention of something being dead
  closed that deal out. Two common words no longer count as a match, and a
  meeting can now move a deal forward but never end one. Only you mark a deal
  dead or passed.
- Fixed: meetings were sometimes filed from your notetaker's summary instead
  of being read from the transcript, with no reason given. The transcript read
  now gets a second try when the first reply comes back unusable, and when it
  still falls back to the summary, filing tells you why.

## 0.75.5 (2026-09-20)

- Fixed: filed meetings were not being listed in your monthly source catalog.
  The catalog is the index of every meeting filed that month, and new meetings
  stopped being added to it a few weeks ago, while filing still reported
  success each time. Seventeen meetings that were missing have been added back,
  and filing now says plainly when it cannot list one instead of reporting a
  clean run.
- Fixed: a meeting could be suggested for a business that does not exist.
  When a project or contact page listed its business in brackets, filing read
  the brackets as part of the name and told you to file under "[zeus]" rather
  than "zeus". The brackets are now ignored, so the suggestion is always one of
  your real businesses.

## 0.75.4 (2026-09-18)

- Fixed: filing a meeting could stamp the wrong deal. A call about a "term
  sheet and deal structure" was filed against a project that shared only the
  word "project" with it, which also pushed that deal's stage forward. Deals
  are now matched on distinctive words rather than common ones, and when two
  deals look equally likely the meeting is left unattached rather than guessed
  at. The confirmation line now names the words it matched on, so you can
  check the decision at a glance.
- Fixed: action items from a meeting could land in the wrong part of your
  hotcache, filed under a list of things you had already finished months ago
  instead of near the top with your open work. They now go directly under the
  action items heading where you will see them.

## 0.75.3 (2026-09-17)

- Fixed: when a briefing page was out of date it always said the same thing,
  whether it was a few hours behind or a job that had stopped over a week ago.
  It now tells you how old the page is, so "3 days ago" and "8 days ago" read
  as the different problems they are. The line is also labelled "Written"
  rather than "Rewritten", since most briefings are written once.
- Improved: Afternoon Tea wrote its section headings in capitals while the
  other three briefings used ordinary sentence case, so one page in the set
  looked like it came from somewhere else. All four now match.
- Fixed: the Week Retro was the only briefing with no suggested focus task, so
  its page opened on a list instead of a recommendation. It now picks one,
  argued from the week that just finished.

## 0.75.2 (2026-09-17)

- Fixed: an update that added a new permission could disconnect an account you
  had connected before it. Van Gogh asked your email provider for every
  permission it currently wants each time it refreshed your sign in, and the
  provider refused the whole request when your account predated one of them, so
  one optional feature's new permission took your mail and calendar with it and
  a briefing came back empty with no explanation. Permissions are now asked for
  only when you sign in, so your existing accounts keep working. If this hit
  you, the fix reaches you with this update and there is nothing to do.
- Improved: when an account really does need you to sign in again, the briefing
  says which account and points you at the one command that fixes it, instead
  of showing the raw error underneath it.
- Improved: signing in again for an account you already have replaces that
  connection in place, and a sign in that comes back empty is refused rather
  than wiping the connection you were repairing.
- Fixed: the weekly briefing and the week retro no longer stop outright when
  your meeting notetaker is connected. A step that gathers recent meetings
  handed back nothing at all instead of an empty list, and both briefings fell
  over on it before they had fetched anything.

## 0.75.1 (2026-09-17)

- Fixed: meetings whose notes use top-level headings no longer lose their next
  steps. A meeting could arrive with six clear follow-ups and be filed with an
  empty action list, so the commitments never reached your briefings.
- Fixed: a short action item written in bold, like "Meet in Las Vegas on the
  28th", was mistaken for a person's name and dropped. It is now kept.
- Fixed: asking for a meeting to be filed as a regular meeting is respected.
  Choosing that type was previously indistinguishable from choosing nothing, so
  a partner strategy session could be filed as a sales call.

## 0.75.0 (2026-09-16)

- New: setting Van Gogh up now fills your vault before your first briefing, instead of handing you the job. It reads 60 days of your sent mail, shows you the threads that look like live deals, and writes the ones you confirm. Nothing is recorded as a deal unless you tick it.
- New: setup asks what you are actually trying to get done in each of your businesses. That one answer is what lets every briefing afterwards say why something matters, rather than just listing it in date order.
- New: people you deal with get a page from the start, built from your own mail. That is what gives a drafted reply something to argue from, and what lets Van Gogh tell you when you have gone quiet on someone.
- New: setup can file your recent meetings so your first briefing knows what was said in them. Ten of them, which takes 10 to 20 minutes; the nightly job picks up the rest over the following nights.
- Improved: the old setup ended by asking you to seed your hot cache by hand from a briefing. Nobody ever did, so that step is now the product's job, and it can be re-run any time with /van-gogh:warm-start if your setup predates this.

## 0.74.1 (2026-09-16)

- Fixed: the commitments Van Gogh reads out of your meetings now actually reach your action list. Reading the transcript produces a clean list of what you personally promised, but the step that files those into your hot cache was still looking for the old format your notetaker used, so it filed none of them. Every meeting since the transcript change was recording your commitments on the page and nowhere else.

## 0.74.0 (2026-09-16)

- Fixed: your first briefing was hiding your oldest threads. Van Gogh treats "no active deal behind this" as a reason to push something down the page, but on a brand new setup you have not written down any deals yet, so everything looked dormant. A fresh install now shows you everything your mail supports.
- New: your first briefing says so. One line at the top tells you how much mail and how many calls it read, and why nothing is marked as moved yet: there is no earlier page to compare against. Tomorrow's will say what changed.
- Improved: the first briefing no longer needs the weekly one to run first. Either order works now, and a first run reads two weeks of calls instead of one day, so the meetings you had before installing are not invisible.
- New: every briefing names the one thing to do first, chosen from the same ranking you see underneath it, so the suggestion can never disagree with the list.
- Improved: replies are drafted against what the other person actually wrote, including the question they asked, rather than against the subject line. This matters most early on, when your vault holds nothing about them yet.
- Improved: if a business has no priorities set, the briefing now says so, names the one setting that turns on judgment, and lists who you have been corresponding with most as a starting point. Those are candidates for that conversation, never treated as priorities you stated.
- Fixed: a first briefing no longer reports the absence of history as if it were news. No "nothing auto-resolved", no announcing your entire backlog as newly cold, no missing-ledger error on the one page where every line is a first impression.
- Improved: meeting prep no longer invents talking points for someone your vault has never seen. It says it has nothing on them, which you can trust, instead of offering guesses you cannot tell apart from real records.

## 0.73.0 (2026-09-16)

- Improved: Van Gogh now reads the actual transcript of your meetings instead of filing the summary your notetaker wrote. A summary is another tool deciding what mattered, and it drops the things that turn out to matter most: the hedge before someone agreed, the plan that quietly changed, the question nobody answered.
- New: every filed meeting now answers eight questions. What was discussed, what was decided, what changed from the previous plan, what you owe, what other people owe you, the deadlines and who owns them, the questions still open, and the tension or hesitation worth noticing.
- New: what you owe and what others owe you are now two separate lists. Somebody else's promise never lands on your own task list again.
- Improved: your meeting notes stay small. The transcript is read and then dropped, so it never bloats your vault, and it can always be pulled again from your notetaker if you want the raw text.
- Improved: if a transcript cannot be read, for any reason, Van Gogh files the notetaker's summary as before and marks the page so you can tell which one you are reading. A meeting always gets filed.

## 0.72.0 (2026-09-14)

- Fixed: setting Van Gogh up now offers you everything it can run on its own. The four briefings were never actually offered during setup, so if you finished the install and stopped, you got the overnight filing jobs and no briefings at all. Nothing was broken. Nobody had asked you.
- New: already set up? After your next update, Van Gogh asks once about anything it never offered you, and never asks again. Saying no is an answer it remembers.
- Fixed: four background jobs were running without anything watching them. If the meeting prep emails or the finance brief had quietly stopped, nothing would have noticed or restarted them. Everything scheduled is now watched.
- Improved: your morning is spread out instead of stacked. Monday used to fire three big jobs at 7:00 AM at once, all competing, and one of them could lose. Now the Week briefing lands at 6:30, the finance brief at 6:45, and Morning Coffee at 7:00. Afternoon Tea moved to 2:30 PM and the Friday retro to 3:30 PM.
- Fixed: a briefing that arrives late now waits for your notes to be filed first. If your laptop was shut at 7:00 AM, everything used to start at once when you opened it, and the briefing could be built before the night's filing had finished. It now waits for that to land, and if it takes too long it runs anyway and tells you.
- Improved: the weekly scorecard is now part of your Friday retro instead of a separate email. Same six numbers, one fewer thing in your inbox. Turning it off in settings still turns it off.
- New: the Workbench can start itself when you log in, so it is already open when a briefing arrives and can show it to you. Off unless you ask for it during setup.
- Fixed: the Workbench now uses the port you set. It had a setting for it and ignored it.
- Fixed: opening the Workbench when it is already running now just opens it, instead of reporting an error. It also notices when a previous one crashed, rather than pointing you at a window that is not there.
- Fixed: if you get one prep email each morning covering the whole day, it now arrives at 6:15 instead of 7:00. It used to land in the same minute as Morning Coffee, which read as the same email twice.
- Note: Slack and WhatsApp delivery are not in this release. The plumbing for other delivery methods is in place, but email is the only one that works today.

## 0.71.1 (2026-09-14)

- Fixed: ticking a box on a briefing page works again. Every checkbox had quietly stopped closing anything, on every item, because the weekly file had changed shape and the part that reads it had not kept up. It would say the item was not in your week file when it was sitting right there.
- Fixed: you can now tick off an item that came from your inbox. Those rows are mail, not tasks, so there was nothing in the weekly file to close and the box could never work. Ticking one now records it, already closed, so the page stops offering you a button that does nothing.
- Fixed: when a tick genuinely cannot close something, the reason now reads as a sentence. It was rendering one letter per line down the side of the page.

## 0.71.0 (2026-09-10)

- New: you can check any connector before trusting it. One command lists everything connected to the machine and says which ones actually work, another asks a connector what it can do and sorts that into what only reads and what changes things, and a third makes one real call and shows you the shape of the answer.
- New: these work for connectors Van Gogh has never heard of, so when a client connects Slack or HubSpot or anything else, you can tell in a minute whether it is really working and what it offers.
- Improved: the shape it shows you never includes the contents. You see the structure of a reply, not the balances or customer names inside it, because it gets read on a screen other people can see.

## 0.70.2 (2026-09-10)

- Fixed: a finance brief that failed to send no longer costs you the whole week. It used to mark the week as handled before the mail actually left, so a momentary fault meant no brief until the next Monday. Now a send that definitely did not happen gives the week back, while one that may have gone out still refuses to send twice.

## 0.70.1 (2026-09-10)

- Fixed: the finance brief now actually reads QuickBooks. The first version was built against a different shape of reply than the connector really sends, so every section would have come back blank. It was caught by running it against real books before anyone relied on it.
- Improved: a section that QuickBooks declines to answer says why and what to do about it. The profit and loss needs an industry set on your QuickBooks profile, and the brief now tells you that instead of showing an empty month.
- Improved: the totals are checked against the detail underneath them before anything is shown. If the two ever disagree, the brief says so rather than picking one.

## 0.70.0 (2026-09-10)

- New: Van Gogh can now read your books. Connect QuickBooks once in Claude's connector settings and /van-gogh:finance-brief tells you where the money stands: what is in the bank by account, who is past due to you and by how long, what you are past due on, and how this month is going against last month.
- New: Turn on the weekly brief and it arrives every Monday morning without you asking. It compares against last Monday, so you see what moved rather than just where things stand.
- New: Every figure says which way it went and whether that is the good way. Cash rising and overdue invoices rising are not the same kind of news, and the page never leaves you to work out which is which.
- Improved: A number that could not be read says so instead of showing zero. An empty report and an empty bank account look identical if you only print the total, and only one of them is good news.
- Improved: Nothing in QuickBooks can be changed. The part of Van Gogh that reads your books is allowed those reports and nothing else, so it cannot create, send or delete anything even by mistake.
- Improved: If the QuickBooks connection expires, which it does from time to time, you get a short email telling you it needs reconnecting and where to do it. Before, an expired connection would have looked like the brief quietly stopping.

## 0.69.0 (2026-09-10)

- New: Setup now reads up on you while it runs. The moment it knows your name and your company, Van Gogh starts reading the public web in the background: what the business does, who runs it, what you have said in public, what you keep coming back to. By the time setup finishes, it has a profile ready for you to check.
- New: What it found becomes part of your vault. You get a page for you and a page for your company, each fact carrying the link it came from, plus a short profile that loads at the start of every session so the product is not meeting you fresh each time.
- New: It suggests what to file your mail under. From what it read about your industry it proposes keywords and a handful of priorities, and you pick which ones are actually real before anything is saved.
- New: Run /van-gogh:operator-research anytime to do this again, or for the first time if you set Van Gogh up before this version. Refreshing replaces the old profile rather than stacking a second one on top.
- Improved: Nothing it finds is saved until you have read it and said yes. If it cannot confirm you are the person it was reading about, it files nothing and tells you the company name is usually the fix.

## 0.68.1 (2026-09-10)

- Fixed: the automated test run now passes on the build machines. Thirteen tests were failing there for reasons that had nothing to do with the product: they assumed Claude Code was installed on the machine running them, and a few assumed a Mac-style filesystem. Nothing about how Van Gogh behaves on your computer changes.

## 0.68.0 (2026-09-10)

- New: Van Gogh now saves the people you actually correspond with into your Google Contacts, by itself. Gmail quietly collects every address you send to in a hidden "Other contacts" list, which is why your real address book has a few dozen people in it and that list has thousands. Run /van-gogh:contact-capture and it shows you who is missing, then saves them once you say yes.
- New: A captured contact arrives filled in, not blank. It reads the person's own email signature for their title and phone number, works out their company, and writes a short note saying when you last spoke and what about.
- New: Turn on the weekly job and it keeps up with you from then on. Anyone you start a real back-and-forth with this week is in your address book next week, on your phone, with no clicking.
- Improved: Only real exchanges are saved. Someone has to have written back, so newsletters, no-reply senders and one-off blasts stay out of your contacts.
- Improved: Nothing is ever written to your address book without you seeing the list first. Every run reports who it would add and stops there until you approve it.

## 0.67.0 (2026-09-09)

- Improved: the quote that opens Afternoon Tea is now hand-picked. Every one
  of the 134 quotes in the collection was read and chosen individually, so
  they are uplifting and worth thinking about rather than merely correct.
  Roughly a quarter of what the online quote library serves is unsuitable for
  the start of a working day, morbid, scolding, or simply strange, and none of
  that can be caught by a rule. Now none of it is there to catch.
- Improved: quotes no longer depend on an internet connection. The collection
  is built in, so the opening line is the same whether or not anything else is
  reachable, and nothing unreviewed can reach you.

## 0.66.0 (2026-09-09)

- New: when something it runs in the background breaks, it now looks at the
  problem itself. Before, a job that failed the same way three mornings running
  got restarted three times and then gave up quietly. Now it reads its own
  logs, works out the cause, fixes what it is allowed to fix, and leaves a
  short note in your vault saying what was wrong and whether anything is
  needed from you. It never touches your notes, never sends anything, and only
  looks after restarting has already been proven not to help.
- New: it watches the meeting prep emails too. That one runs every fifteen
  minutes rather than at a set time, so it used to sit outside the check
  entirely. If it stops, you now find out from your morning briefing instead of
  from a call you walked into cold.
- New: one line about work that stopped moving. Not jobs, the work itself: a
  commitment weeks past the date you set, a draft written for you that nobody
  read. Nothing chases those on its own, so the briefing names them and says
  they are waiting on you. It stays quiet on the days there is nothing to say.
- Fixed: a background job that goes quiet is now counted properly. A counting
  mistake meant one kind of stuck job could be restarted every half hour
  forever without ever being looked at.
- Fixed: the repair session could not find its own instructions, so it exited
  with an error instead of looking at anything. Caught by running it for real
  against a broken job rather than trusting the tests.
- Fixed: a briefing name the system does not know now says so plainly. It used
  to fail with a message about weekly retro files, which sent anyone reading it
  looking in the wrong place.

## 0.65.0 (2026-09-09)

- New: a Note between the briefings. Van Gogh now looks every fifteen minutes,
  and when someone you were waiting on writes back, a counterparty says in
  words that a deal is off, or a meeting on today's page moves, it mails you a
  short Note with the reply already written and waiting for your nod. It
  watches only what the morning briefing put on your front page and the deals
  in your notes, sends at most two a day (the rest wait for Afternoon Tea),
  never sends during a meeting, and stays quiet outside working hours. The
  briefing on your screen gains a Notes section so the page stays true after
  it was written. Off until you switch it on.
- Improved: Afternoon Tea opens with the Notes that went out today, and names
  the ones you have not seen yet.

## 0.64.0 (2026-09-09)

- Fixed: the Stoic quote that opens Afternoon Tea kept repeating. It was
  picked from a list of eight written into the skill, and nothing remembered
  which ones had already been used, so the same few came back within days.
  Quotes now come from a much larger library that tops itself up in the
  background, and Van Gogh remembers the last thirty it showed you, so a
  quote you have just seen cannot come back for a month.
- Improved: quotes are checked before they are ever shown. Anything that
  reads badly in a briefing is filtered out: passages too long to open a
  page, stage dialogue from Seneca's plays, Latin with a translation bolted
  on, text with transcription errors, leftover footnote and chapter markers
  from the scanned books, and lines in all capitals. Seneca's passages
  arguing for suicide are filtered out too, so a working day never opens on
  one.
- Improved: if the quote library cannot be reached, Afternoon Tea still
  opens with a quote and still avoids repeats, rather than failing or
  showing nothing.

## 0.63.0 (2026-09-09)

- New: a controller you can hand the books to. Ask about closing the month,
  chasing a balance that will not reconcile, an aging report, a journal entry,
  who is allowed to approve what, or how to get ready for an audit, and a
  specialist answers instead of a generalist. It knows revenue recognition,
  leases, stock compensation and purchase accounting, and it works from the
  records already in your vault rather than asking you to retype them.
- New: it never invents a number. Every figure it gives you names the file it
  came from, and when the support is not there it says so instead of
  estimating. Anything it writes to send, a collections note or a query to a
  vendor, arrives as a draft for you to read first.

## 0.62.0 (2026-09-09)

- New: a scorecard, emailed to you every week, showing whether Van Gogh is
  actually earning its place. Six measures over a fortnight: how many threads
  are waiting on you, how many are late, how long things take to close, how
  many action items name who owns them, how many outside calls got a prep, and
  how much you closed. Each one says whether it hit its target and what it is
  counted over, and any measure without enough behind it says so rather than
  showing you a made-up zero. It ends with one part of Van Gogh you have not
  used lately, in case it is the part you needed.
- New: the numbers are counted on your machine as Van Gogh works, from a log
  that holds counts and dates and nothing else. No subject line, no name and no
  address is ever written to it, so the scorecard can be shared without sharing
  your inbox.
- New: the scorecard is off until you ask for it, during setup or later through
  your settings. It can go to a different address from your briefings, since it
  is about you rather than about your week, and the first one waits until there
  is a full fortnight to report on.

## 0.61.0 (2026-09-09)

- New: a meeting prep, emailed to you before the call. Van Gogh could always
  write one, but only when you thought to ask, which is the moment it helps
  least. Now it can arrive on its own: who is on the call, what the deal
  context is, what you still owe them, and what a good outcome looks like.
  Choose one email per meeting, about an hour ahead, or a single email each
  morning covering the whole day. It is off until you ask for it, during setup
  or any time through your settings, and it goes to the same address as your
  briefings. Only meetings with someone outside your team get one, an internal
  standup has nothing to look up, and a call already underway never gets a
  prep, because a brief that arrives late is worse than none.

## 0.60.0 (2026-09-09)

- Fixed: a Send button could appear on a reply you had already sent. On the
  end of day page, a line that already said the reply had gone still offered to
  send it again, with the confirmation struck through beside it. Pressing it
  would have sent a second copy of a settled position. The button now only
  appears on replies that are genuinely still waiting, and a line that has
  already gone out simply says when.
- Fixed: overdue work now shows in red on the weekly briefing and the Friday
  retro. Both pages report things slipping for a living, and both were printing
  every "3 days overdue" and "88 days since contact" in the same grey as the
  tag next to it. The one colour that means "this is falling behind" could not
  reach the two pages that most needed it.
- Fixed: items on the weekly briefing broke apart on a wide screen. The project
  name was thrown to the right margin and the sentence describing the work
  restarted underneath beginning with a stray colon, so your eye landed on the
  category tag instead of the task. Each item is one clean line again.
- Fixed: the ask box no longer runs off the left edge of a phone screen. It was
  wider than the screen on every briefing, so its first word was cut in half.
- Improved: amber now means one thing again. The button that opens a draft
  reply had been wearing the same colour as the send stamp and the travel
  warning, so three marks competed for the colour reserved for a deadline you
  cannot move. Only travel and the send stamp carry it now.
- Improved: the Friday retro has house rules for how it is written. It no
  longer prints internal words like "stalled" or "loi", it spells out an
  abbreviation the first time it uses one, and it puts one deal per line
  instead of chaining ten of them together into a single paragraph.

## 0.59.0 (2026-09-09)

- Fixed: travel notices now reach Afternoon Tea and the weekly briefing. Both
  briefings had been collecting your trips and then dropping them before
  anything was written, so a flight that showed up in Morning Coffee was
  missing from the evening and weekly versions of the same day. It appears in
  all three now, in the same place, with the same wording.
- Improved: the four briefings now agree on section order. The focus task comes
  first, then travel, then what to do today, then any replies waiting for you.
  The written file, the web page and the emailed digest all follow that order,
  so a briefing reads the same wherever you open it.

## 0.58.0 (2026-09-09)

- Improved: travel now has its own block directly under the suggested focus
  task, outlined in amber. A flight is the one thing in the briefing whose
  deadline you cannot move, and it used to sit further down the page among
  the day's meetings.
- Improved: that block is now assembled from the calendar and your airline
  confirmation rather than written fresh each morning, so a leave by time is
  never reworded or rounded on the way to the page.
- New: a trip with nothing booked yet carries a Find flights button on the
  Workbench, and the same trip is a link in the written file.
- Changed: replies waiting for your nod now sit after the front page rather
  than above it. They are still one press each, and a reply can wait a day
  where a flight cannot.

## 0.57.0 (2026-09-09)

- New: replies that are written and waiting for you now have their own block,
  directly under the suggested focus task, outlined in amber so you cannot
  miss it. It used to sit further down the page, described in a sentence that
  changed wording from one morning to the next.
- New: on the Workbench each waiting reply carries an Open draft button that
  takes you straight to it. In the written file the same reply is a link, so
  it works wherever you read the file.
- Improved: that block is now assembled from the record of what was drafted,
  rather than written fresh each morning. Who each reply is to, what it is
  about, which account it is in and whether it has been sent are all facts,
  and they now read the same way every day.

## 0.56.0 (2026-09-09)

- New: the end of day briefing now tells you about tomorrow's flight, the
  evening before, while there is still time to pack for it. Being told at 5:55
  in the morning that you leave at 6 is too late to be useful.
- New: the Monday briefing lists the week's trips, and flags any that have no
  flight booked yet, which is the week's travel problem you can still fix.
- Improved: each briefing now tells you about a trip on its own terms, so
  hearing about a flight on Monday no longer means going without the leave by
  time on the morning you actually fly.
- Note: the end of day briefing tells you about tomorrow's flight only when
  your home address and a Maps key are set. Without them it still names the
  airport and the terminal, and says which of the two is missing, rather than
  guessing a leave by time.

## 0.55.0 (2026-09-09)

- New: each briefing now opens its own page when it finishes, so the work is
  in front of you instead of waiting somewhere you have to go and find. All
  four do it: Morning Coffee, Afternoon Tea, The Week and the Week Retro.
- Improved: a briefing only opens a page into a Workbench you already have
  open, it never starts one, and each briefing opens at most one page every
  six hours, so a rerun or a retry does not stack up tabs. If you would rather
  it stayed quiet, set the Workbench "open on run" setting to false and
  nothing will open again.
- Fixed: on a Monday, The Week opened nothing, and on a Friday neither did the
  Week Retro. Both share their hour with Morning Coffee, and the rule that
  stopped repeat tabs could not tell two different briefings apart, so the
  second one of the pair was treated as a repeat of the first.

## 0.54.0 (2026-09-09)

- New: the Week Retro now has a page in the Workbench, like the other three
  briefings. It was being written every Friday and there was no way to open it
  there; the address simply came back as not found. It opens now, and it shows
  the most recent retro by its date.

## 0.53.0 (2026-09-09)

- Improved: opening the Workbench now lands you on the Morning Coffee page
  itself, the briefing with its live controls, instead of the dashboard shell.
  That page is the front door for you and for every client. The dashboard
  (tickets, vault graph, inbox roll-up) is still there at the server's root
  address if you want it, but nothing opens it for you any more.

## 0.52.0 (2026-09-09)

- New: the briefing now reads your airline confirmation email. A calendar
  entry that just says "Flight to Dallas" is enough: where you are flying to,
  the terminal, and the flight number are pulled from the confirmation, so you
  get the destination weather and the right terminal without editing your
  calendar by hand. What your calendar already says is never overruled, since
  you wrote it and the email may be a month old.
- Fixed: your settings file holding API keys and sign in tokens was readable
  by every account on the computer. It is now readable only by you, and
  existing files are tightened the next time anything is saved.
- Fixed: the destination weather could be for the wrong continent. Airport
  codes were being looked up as if they were place names, so a flight to
  Dallas Love showed the forecast for Dali, in China, complete with a 97
  percent chance of rain. Real weather, real forecast, wrong side of the
  world, and nothing about it looked broken. The busiest airports are now
  read from a list of where they actually are.
- Fixed: the briefing missed where you were flying to unless your calendar
  entry was written in one exact format. An entry reading "John Wayne Airport
  (SNA) to Dallas Love (DAL)" now works, as do the other ways people write it,
  so the weather notice fires when it should.
- Improved: when there is no drive time, the briefing says which piece is
  missing instead of always telling you to set your home address. If the
  address is already set, being told to set it sends you looking in the wrong
  place.

- New: Van Gogh now notices when one of its own scheduled jobs stops running,
  and starts it again. Before this, a briefing that failed twice in a row, or
  never fired because the computer was asleep, simply did not arrive, and you
  found out by noticing it was not there. A check runs every half hour, sees
  which jobs were due, and re-runs the ones that did not deliver.
- New: it knows the difference between kinds of failure, and waits rather than
  hammering. If you have hit your usage limit it reads the reset time out of
  the message and waits for it, instead of spending its retries inside the
  lockout. If the Claude app needs updating it waits for that to finish. If
  you need to sign in again it stops and says so, because trying again cannot
  fix a sign-in.
- New: when something did go wrong, your next briefing says so in one line at
  the bottom, in plain words, and always says whether you need to do anything.
  Something the system is handling ends with "Nothing for you to do". Something
  only you can fix, like signing in again, says that first and says what to do.
  On a normal day the line is not there at all.
- Improved: the briefings themselves stop retrying a run that hit a usage
  limit or a sign-in problem. That retry never worked, and skipping it means
  the job is ready to try again as soon as trying can actually help.
- Improved: your run history no longer grows without limit. It keeps the most
  recent few thousand entries, which is more than anything reads.
- Fixed: a scheduled job that had never run once could be reported as healthy
  if it was not the kind of job that writes a file.

## 0.51.1 (2026-09-09)

- Fixed: the vault audit could not accept its own evidence. When it graded
  itself and pointed at one of your meeting notes as proof, it refused the
  citation, because it did not recognise a filename with spaces in it as a
  filename. Since almost every note in a real vault has spaces in its name,
  nine of the twenty rows in the score could never be backed up, however good
  the evidence was.
- Fixed: an audit with a long list of findings always failed its own check.
  The list shows the first forty and says how many more there are, but the
  check insisted on seeing every one, so the report could not pass no matter
  what it said.

## 0.51.0 (2026-09-09)

- New: your briefing now handles flights. When a trip is coming up it offers a
  flight search if you have not booked yet, shows the destination forecast two
  days out so you know what to pack, and on the morning you fly it tells you
  the airport, the terminal when your calendar knows it, how long the drive is
  with current traffic, and what time to leave the house to be there 90
  minutes early.
- New: the leave-by time is worked out against the flight's own clock, not
  yours. A 6:05 AM flight out of Newark is 3:05 AM if you are on the west
  coast, and getting that backwards is how you miss a plane, so the briefing
  does the conversion and names the timezone it is showing you.
- Improved: a trip is only recognised when your calendar entry names an
  airport code, so a meeting about travel never sets off a 4 AM alarm. Each
  notice is shown once and then remembered, so rerunning your briefing does
  not repeat it.
- New: to get drive times, add your home address to your private settings
  file. It is deliberately kept out of the settings that sync to your vault,
  so it is never written to a page you might share. Without it you still get
  flight links and the forecast.

## 0.50.0 (2026-09-09)

- Improved: the vault audit now scores your setup out of 100 instead of handing
  out letter grades. Every point has to be backed by something real on disk, so
  a brand new setup scores low and honestly rather than getting a polite B for
  having no problems yet. Two limits stop a lopsided setup from looking good:
  the score is held down when the vault cannot answer a question about your own
  priorities, and again when nothing is scheduled to run on its own.
- New: the audit remembers what it found. Each finding keeps the same id week to
  week, so you can see what came back, what got fixed, and what was never
  rechecked, instead of reading the same list again with no history.
- Improved: the weekly pick now asks whether the work should happen at all
  before asking whether to automate it, names the smallest level of independence
  that would help, and has to say which number it would move. Stopping something
  is a valid answer and sometimes the right one.
- New: the audit checks its own report before finishing. If a number does not
  add up, or a score is claimed without pointing at anything, the report is sent
  back to be fixed rather than published.
- New: long interviews now save each answer as you give it. If a session ends
  partway through building your voice guide, changing settings, or creating a
  skill, the next run picks up where it stopped instead of asking everything
  again.
- Fixed: the audit no longer claims it runs automatically on Fridays. Nothing
  scheduled it, so it runs when you ask for it.

## 0.49.0 (2026-09-09)

- New: some users get a personal add-on pack, private skills built just for
  them by the Van Gogh team. If you receive a Van Gogh key file, the installer
  now sets it up, and /van-gogh:update-van-gogh installs it later or swaps in
  a replacement key anytime. Your key stays on your machine and never appears
  in the chat.

## 0.48.1 (2026-09-09)

- Improved: after an update, the "what changed" notes now appear exactly once,
  in your next chat session, and nowhere else. Emailed digests and the
  briefing web pages stay clean of update chatter. Missed the notes? They are
  always waiting in /van-gogh:release-notes.

## 0.48.0 (2026-09-08)

- New: Van Gogh is now yours to extend without waiting on an update. You can
  add your own sections to the briefings, plug in a meeting notetaker we do
  not ship, and teach the briefings your own filing words, all from files
  that live on your machine and survive every update untouched.
- New: /van-gogh:create-skill can now copy any built-in command as a personal
  one you are free to change. Want a morning briefing that reads differently?
  Fork it, edit your copy, and the original stays as it was.
- New: you can leave standing guidance for the part of Van Gogh that judges
  what matters, in your own words, in a note that travels with your vault.
  Something like "newsletters never count" is read before every briefing.
- Improved: if one of your own extensions breaks, the briefing says so in its
  errors and carries on. A broken add-on costs you its own section, never the
  whole briefing.

## 0.47.0 (2026-09-08)

- New: Van Gogh now works with Grain as well as Granola for meeting notes. Use
  whichever one you like, one at a time. If you set up a Grain API key, Van
  Gogh finds it on its own, and you can switch anytime with
  /van-gogh:update-settings.
- New: after Van Gogh updates itself, your next briefing tells you what
  changed, in plain language like this. Missed it? /van-gogh:release-notes
  shows the last update's notes again, anytime, and the full version history
  on request.
- Fixed: the end-of-day and morning completion scans were missing meeting
  evidence entirely, so items you closed out in a call were never suggested as
  done. They are now.
- Fixed: a typo in the notetaker setting no longer makes meetings silently
  vanish from briefings. Van Gogh falls back to Granola and tells you about
  the typo instead.
- Fixed: on Windows, an outdated background task left over from an old version
  is now cleaned up automatically instead of failing quietly every morning.
- Improved: if a meeting service ever returns more meetings than one briefing
  can walk through, the briefing now says some were left out instead of
  looking complete when it is not.
