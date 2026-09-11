---
name: voice-generator
description: Generate a tone-of-voice profile from the last 90 days of sent email, segmented by audience category (investors, clients, vendors, legal, and so on). Surfaces how writing style and energy shift by audience, and includes a fixed anti-prompt of universal rules that hold regardless of who you are writing to. Use when the user types /van-gogh:voice-generator or asks to analyze their email tone or voice by audience.
allowed-tools:
  - Bash("$HOME/.config/van-gogh/venv/bin/python" *)
  - Bash(& "$HOME\.config\van-gogh\venv\Scripts\python.exe" *)
  - Bash(cd $env:CLAUDE_PLUGIN_ROOT)
---

# Voice Generator

A Python script fetches sent email across all configured accounts, classifies each thread by counterparty category, and scores tone features. It emits JSON to stdout (with a `meta` block) and prints progress to stderr. Your job: parse the JSON, render per-audience tone cards plus the universal anti-prompt, and write the profile.

## Resolved paths and labels come from the script

The JSON output includes a top-level `meta` block (same shape as `app/skill_context.py`, which you can run if you need values without the full data pull). Use these keys verbatim, never hardcode them:

- `meta.user_first_name`, `meta.user_full_name`
- `meta.tone_profile_path`: where to write the rendered profile
- `meta.voice_guide_path`: the base voice guide for the "vs. base voice" comparison and calibration sentences, if it exists
- `meta.accounts`: list of configured accounts, each `{label, provider, email, is_primary}`. Use each account's `label` in the account-breakdown notes; iterate the list rather than assuming a fixed set.

---

## Python runtime

**Python runtime:** before the first script invocation, run the ensure-venv
guard in [`_shared/python-runtime.md`](../_shared/python-runtime.md).

---

## Workflow

**Step 1: Run the data engine**

macOS / Linux (bash/zsh):
```bash
LOGS_DIR=$("$HOME/.config/van-gogh/venv/bin/python" -c "import sys, os; sys.path.insert(0, os.path.join(os.environ['CLAUDE_PLUGIN_ROOT'], 'app')); from config_loader import logs_dir; print(logs_dir())") && mkdir -p "$LOGS_DIR" && "$HOME/.config/van-gogh/venv/bin/python" "${CLAUDE_PLUGIN_ROOT}/app/voice_generator.py" > "$LOGS_DIR/voice_generator_output.json"
```

Windows (PowerShell):
```powershell
$LOGS_DIR = (& "$HOME\.config\van-gogh\venv\Scripts\python.exe" -c "import sys, os; sys.path.insert(0, os.path.join(os.environ['CLAUDE_PLUGIN_ROOT'], 'app')); from config_loader import logs_dir; print(logs_dir())").Trim()
New-Item -ItemType Directory -Force -Path $LOGS_DIR | Out-Null
& "$HOME\.config\van-gogh\venv\Scripts\python.exe" "$env:CLAUDE_PLUGIN_ROOT\app\voice_generator.py" > "$LOGS_DIR\voice_generator_output.json"
```

Flags (forward whatever the user passed):

- `--days N`: lookback window, default 90
- `--voice-file PATH`: path the script writes its raw JSON profile to (defaults to the configured voice guide path). This is the engine's machine output, separate from the curated profile you write in Step 6.
- `--corpus`: also emit a raw deduped thread corpus (used by /van-gogh:voice-bootstrap, not normally needed here)

Takes 60 to 120 seconds. Progress prints to stderr. Wait for the full JSON before parsing.

**Step 2: Parse the output**

Read `$LOGS_DIR/voice_generator_output.json`. Schema:

```json
{
  "meta": { "...": "resolved paths and labels" },
  "generated": "2026-05-22T...",
  "window_days": 90,
  "total_threads": 412,
  "classified": 389,
  "by_category": {
    "investor": {
      "sample_size": 47,
      "median_formality": 3.8,
      "median_energy": 2.1,
      "median_directness": 3.4,
      "dominant_we_vs_i": "we",
      "pct_uses_numbers": 0.72,
      "dominant_greeting": "formal",
      "example_subjects": ["..."],
      "example_snippets": [{"subject": "...", "snippet": "...", "account": "..."}]
    }
  },
  "errors": []
}
```

If `errors` is non-empty, show the errors at the top and render whatever succeeded.

---

**Step 3: Render per-category tone cards**

Sort categories by `sample_size` descending. Skip any with `sample_size < 3`.

For each category, render a card:

```
## [Category Label] (n=47 emails)

**How you write to them:**
[2 to 3 sentence narrative derived from the scores. Translate numbers into
plain English. Examples:
  - formality 1.4: "Casual and direct. You write like you are texting a peer."
  - formality 4.2: "Formal but not stiff. Proper salutation, measured close."
  - energy 1.8: "Deliberate and measured. No exclamation marks, no urgency markers."
  - energy 4.5: "High energy. You pitch hard and close with momentum."
  - directness 4.0: "You land the ask clearly. Context is brief, CTA is explicit."
  - we_vs_i=we: "You write in 'we', an institutional voice, not a personal one."
  - pct_uses_numbers 0.72: "You cite specific data in 72% of these emails."
  - greeting=formal: "Consistent formal salutation."]

**What you actually say:**
> "[verbatim snippet from example_snippets[0]]"

> "[verbatim snippet from example_snippets[1] if available]"

**vs. your base voice:**
[1 observation cross-referenced against the base voice guide, if it exists]
```

Category display names:

- investor → Investors / Capital Partners
- strategic_partner → Strategic Partners (JV, advisors)
- client → Clients / Customers
- vendor → Vendors / Service Providers
- legal → Legal / Counsel
- government → Government / Utilities / Regulators
- prospect → Prospects / Cold Outreach
- media → Media / Press
- other → Other

---

**Step 4: Read the base voice guide** at `meta.voice_guide_path`, if it exists.

Use it for the "vs. your base voice" line in each card. Focus on whether the guide's signature rhetorical engine, number density, pronoun preference, and closing pattern hold in that category or shift. If no base voice guide exists, suggest running /van-gogh:voice-bootstrap first, or proceed with the anti-prompt only and omit the "vs. base voice" lines.

---

**Step 5: Render the anti-prompt**

This section is always present, regardless of what the email analysis found. These are universal rules that hold for every audience.

```
---

## Anti-Prompt (universal rules, every audience, no exceptions)

**Never use em-dashes.** Replace with a period, a colon, or restructure into two
sentences. This is an absolute rule with no exceptions.

Never hedge mid-claim. "I think", "perhaps", "sort of", "I believe" (when
softening a fact rather than stating a value) all weaken authority. Cut them.

Never default to passive voice. Every sentence has an actor. Passive hides
accountability.

Never generalize when a number exists. "High response rate" becomes "25% hit
rate". Precision is the proof.

Never use filler transitions. "Furthermore", "Moreover", "In addition" are
throat-clearing. Use "Now", "Next", or nothing.

Never let technical language float unanchored. Within 2 to 3 sentences of any
technical claim, land the human implication.

Never end on a question. Every email closes on a declarative, even a soft one.
"Looking forward to connecting Thursday" beats "Does that work for you?"

Never name-drop without function. Every named person earns their place by
carrying a functional role in the argument.

Never use bullet points in formal correspondence. Enumerate only when items are
genuinely parallel and a list is the clearest form. For narrative flow, use prose.

Banned words regardless of audience: "synergy", "streamline" (use "systematize"),
"fast-paced", "game-changing", "leverage the power of", "perhaps", "maybe" (as
hedges), and the filler transitions above.
```

---

**Step 6: Write the curated profile** to `meta.tone_profile_path`:

```markdown
---
generated: YYYY-MM-DD
window_days: 90
threads_analyzed: N
---

# Tone of Voice by Audience

[Per-category cards from Step 3, in order of sample size]

---

[Anti-prompt from Step 5]

---

## Base Voice Calibration

[The calibration sentences from the base voice guide, verbatim, as an anchor, if it exists]
```

---

**Step 7: Generate verification drafts**

After writing the profile, draft one short sample email per category (those with `sample_size >= 3`), each applying that category's tone scores, example snippets, and the anti-prompt. This is the proof step: it lets the user confirm the profile actually captures their voice.

For each category:

- Invent a realistic scenario grounded in the user's actual context. Use the businesses in `meta.businesses[]` and the base voice guide for grounding. Reuse a real `example_subjects` line where one fits.
- Apply the category's calibration: formality, energy, directness, we_vs_i, number density (`pct_uses_numbers`), and greeting style.
- Obey the anti-prompt with no exceptions (no em-dashes, declarative close, active voice).
- Keep each to 4 to 6 sentences plus a signature.

Render them inline under a heading like `## Verification drafts, does this sound like you?`, grouped by category, each preceded by a one-line note on which calibration knob it demonstrates.

Then ask: "Read these and tell me which sound like you and which miss. I will recalibrate the profile from your feedback." If the user flags a miss, adjust that category's card narrative in the profile and redraft only that one.

---

**Step 8: Offer next steps**

- "Want me to draft a real email for a specific upcoming thread in that audience's profile tone?"
- "I can generate a condensed, prompt-ready version for use in any drafting tool."
- "Want me to flag any recent draft that violates the anti-prompt?"

---

---

## When to ask the user

- Script fails entirely: show the error, and suggest confirming the API key is set and the email accounts are authenticated.
- `classified` is much lower than `total_threads` (under 50%): note it in the header. The profile is based on a partial sample.
- All categories have `sample_size < 3`: tell the user the lookback may be too short and suggest `--days 180`.
