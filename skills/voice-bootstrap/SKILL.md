---
name: voice-bootstrap
description: Build a voice style guide from scratch for a brand-new user. Produces a device inventory (sentence structure, vocabulary fingerprint, rhythm, tone markers, banned words) plus calibration sentences and an anti-prompt. Ingests writing samples via links, file paths, or pasted text to derive the baseline, and falls back to the deduped sent-email corpus if no samples are given. Always pulls sent email for by-audience tone segmentation. Use when the user types /van-gogh:voice-bootstrap or asks to create a voice profile or style guide from scratch.
allowed-tools:
  - Bash("$HOME/.config/van-gogh/venv/bin/python" *)
  - Bash(& "$HOME\.config\van-gogh\venv\Scripts\python.exe" *)
  - Bash(cd $env:CLAUDE_PLUGIN_ROOT)
---

# Voice Bootstrap

This is the cold-start companion to /van-gogh:voice-generator (which scores tone by audience but does not author the baseline) and /van-gogh:voice-calibration (which refines voice over time). Run this first; the others build on its output.

A Python script supplies the email corpus and the by-audience tone data. Your job: ingest any writing samples the user provides, extract the device inventory and calibration sentences, render the tone cards, and write the voice guide.

## What this builds

A voice style guide at `meta.voice_guide_path`: six device sections, a paste-ready prompt version, by-audience tone cards, and calibration sentences.

Two layers, two sources:

- **Baseline** (device inventory plus calibration sentences) is the rhetorical core. Built from **writing samples** if the user provides any, falling back to the **sent-email corpus** if they do not. Samples win, because crafted long-form shows rhetorical devices that transactional email flattens.
- **By-audience tone cards** (formality, energy, directness per counterparty category) are **always** built from sent email, because only email reveals how the user shifts between, say, an investor and a contractor.

## Resolved paths and labels come from the script context

The JSON output from the engine includes a `meta` block (same shape as `app/skill_context.py`). Use these keys verbatim, never hardcode them:

- `meta.voice_guide_path`: where to write the final style guide
- `meta.user_first_name`, `meta.user_full_name`
- `meta.accounts`: list of configured accounts, each `{label, provider, email, is_primary}` — use each account's `label` for account-breakdown notes; iterate the list rather than assuming a fixed set
- `meta.businesses[]`: context for grounding examples

---

## Python runtime

**Python runtime:** before the first script invocation, run the ensure-venv
guard in [`_shared/python-runtime.md`](../_shared/python-runtime.md).

---

## Checkpoint every answer

This interview asks for writing samples, reads a year of sent mail, builds
an inventory and gets five sentences approved. Until the guide is written
none of that exists anywhere but this conversation, so a session that ends
early throws all of it away and the user is asked everything again.

**Before the first question**, open a capture:

macOS / Linux (bash/zsh):
```bash
"$HOME/.config/van-gogh/venv/bin/python" "${CLAUDE_PLUGIN_ROOT}/app/interview_capture.py" start --skill voice-bootstrap --goal "build the voice guide"
```

Windows (PowerShell):
```powershell
& "$HOME\.config\van-gogh\venv\Scripts\python.exe" "$env:CLAUDE_PLUGIN_ROOT\app\interview_capture.py" start --skill voice-bootstrap --goal "build the voice guide"
```

If it answers `resumable: true`, an earlier run is still open: read it back
with `show`, tell the user what you already have, and skip straight to the
first step they have not answered. Never re-ask an answered question.

**After every answer below**, append it before asking the next thing. The
command is the same each time, with a different question and answer:

macOS / Linux (bash/zsh):
```bash
"$HOME/.config/van-gogh/venv/bin/python" "${CLAUDE_PLUGIN_ROOT}/app/interview_capture.py" append --skill voice-bootstrap --question "<what you asked>" --answer "<what they said>"
```

Windows (PowerShell):
```powershell
& "$HOME\.config\van-gogh\venv\Scripts\python.exe" "$env:CLAUDE_PLUGIN_ROOT\app\interview_capture.py" append --skill voice-bootstrap --question "<what you asked>" --answer "<what they said>"
```

It re-reads the file and fails loudly if the answer did not land, so a
reported checkpoint is a real one. If it returns an error, tell the user
the capture is not saving and carry on rather than stopping the interview.

---

## Workflow

**Step 0: Establish whose voice this is**

Confirm the person's name from `meta.user_full_name`, or ask if the guide is for someone new. The output file is `meta.voice_guide_path`.

---

> Checkpoint: Append the Step 0 answer (whose voice this is) before asking for samples.

**Step 1: Ask for writing samples**

Ask once, in one message:

> "To build your voice baseline, share your strongest writing, anything that sounds like you at your best. You can give me:
> - links (published articles, posts, blog posts),
> - file paths (local docs, transcripts, decks, .md/.txt/.pdf), or
> - pasted text directly.
>
> Or leave it blank and I will build the baseline from your sent email instead."

Wait for the response. Do not proceed until the user answers or explicitly skips.

---

> Checkpoint: Append the Step 1 answer (the samples they gave, or that they gave none).

**Step 2: Ingest the samples**

For each source the user provides:

- **Links** → fetch the page and extract the article text.
- **File paths** → read them. For PDFs, use the page-range parameter.
- **Pasted text** → use as-is.

Concatenate into a sample corpus, labeling each piece by source, and note the total word count. If a link fails to fetch, report it and continue with the rest. If the user provided no samples, skip to Step 3 with the baseline source set to email.

---

**Step 3: Pull sent email (always)**

macOS / Linux (bash/zsh):
```bash
LOGS_DIR=$("$HOME/.config/van-gogh/venv/bin/python" -c "import sys, os; sys.path.insert(0, os.path.join(os.environ['CLAUDE_PLUGIN_ROOT'], 'app')); from config_loader import logs_dir; print(logs_dir())") && mkdir -p "$LOGS_DIR" && "$HOME/.config/van-gogh/venv/bin/python" "${CLAUDE_PLUGIN_ROOT}/app/voice_generator.py" --corpus > "$LOGS_DIR/voice_bootstrap_output.json"
```

Windows (PowerShell):
```powershell
$LOGS_DIR = (& "$HOME\.config\van-gogh\venv\Scripts\python.exe" -c "import sys, os; sys.path.insert(0, os.path.join(os.environ['CLAUDE_PLUGIN_ROOT'], 'app')); from config_loader import logs_dir; print(logs_dir())").Trim()
New-Item -ItemType Directory -Force -Path $LOGS_DIR | Out-Null
& "$HOME\.config\van-gogh\venv\Scripts\python.exe" "$env:CLAUDE_PLUGIN_ROOT\app\voice_generator.py" --corpus > "$LOGS_DIR\voice_bootstrap_output.json"
```

Forward `--days N` if the user asked for a different window. This emits the normal `by_category` block (for the tone cards) plus a `corpus` array of raw deduped sent-mail threads (subject, snippet, recipient, account, category) used for device extraction when no samples were given. Takes 60 to 120 seconds; progress prints to stderr.

Read `$LOGS_DIR/voice_bootstrap_output.json`. If `errors` is non-empty, surface them and continue with whatever succeeded.

---

> Checkpoint: Append the Step 3 result (the corpus path and how many pieces it holds).

**Step 4: Build the device inventory**

Source: the **sample corpus** if Step 2 produced one, otherwise the email `corpus` array.

Analyze the source text and write the inventory across these six sections. Every observation must trace to a specific quoted example, no generic style advice.

1. **Sentence Structure Patterns**: how they open (conclusion-first vs. context-first), characteristic constructions, sentence-length rhythm, whether they ground abstract claims in concrete detail.
2. **Vocabulary Fingerprint**: signature words and phrases that recur (with an example each), authentic colloquialisms, how they use numbers, and a banned-words list (their hedges, their filler, clichés they avoid).
3. **Rhythm and Cadence**: recurring structural engines (for example a before/after pivot), parallelism or triadic patterns, where they place emphasis.
4. **Tone Markers**: direct vs. collaborative vs. visionary, and the approximate ratio between them, how they signal authority and humility, pronoun preference ("we" vs. "I").
5. **Structural Patterns**: their opening sequence and closing sequence as ordered steps, how they end (question vs. declarative).
6. **What They Never Do**: absolute rules and anti-patterns, including the global ones (never em-dashes, no passive default, no unanchored jargon, no filler transitions, no name-drops without function).

If the baseline came from email (no samples), say so plainly in the file's header note. Email yields a flatter, more transactional baseline than crafted long-form, and the user should know the ceiling.

---

> Checkpoint: Append the Step 4 inventory.

**Step 5: Draft the calibration sentences (then approve)**

These are the densest compression of the inventory. Each sentence should demonstrate 2 to 3 of the devices identified in Step 4, written in the user's own vocabulary and about their real subject matter. They are the few-shot anchor downstream drafting tools read.

Method:

1. **Extract**: pull 5 to 8 real sentences from the source corpus that best exemplify the identified devices. This grounds you in real phrasing.
2. **Compose**: recompose them into 5 device-complete exemplars. State, for each, which devices it demonstrates.
3. **Gate**: present the 5 draft sentences to the user with the device notes and ask them to approve or edit. Do NOT write the file until they sign off. This block is the anchor everything references; it cannot ship unreviewed.

---

> Checkpoint: Append the five approved sentences from Step 5, verbatim, before anything else is rendered. These are the most expensive thing in the interview to recreate: they came from the user's own judgment, not from the corpus.

**Step 6: Render the by-audience tone cards**

From the `by_category` block in the JSON, render one card per category with `sample_size >= 3`, sorted by sample size descending. Translate the medians into plain English (formality 1.4 → "casual and direct", energy 4.5 → "high energy, closes with momentum") and quote one real snippet per card. Same format as /van-gogh:voice-generator Step 3.

---

> Checkpoint: Append the Step 6 tone cards.

**Step 7: Write the voice guide** to `meta.voice_guide_path`:

```markdown
---
generated: YYYY-MM-DD
baseline_source: writing_samples | sent_email
samples: [list of sources, or "none"]
window_days: N
---

# {Name} Voice Style Guide

*Baseline derived from: {sample sources, or "sent email (no writing samples provided, flatter baseline; rerun with samples to deepen)"}*

[Sections 1 to 6 from Step 4]

---

## Paste-Ready Prompt Version

[A single-paragraph-per-rule prompt distilled from the six sections, suitable for dropping into a drafting tool. Include the anti-prompt rules from /van-gogh:voice-generator Step 5.]

---

## Tone of Voice by Audience

[Per-category cards from Step 6]

---

## Calibration Sentences

[The approved sentences from Step 5, verbatim]
```

---

---

**Step 8 checkpoint: close the capture** once the guide is written:

macOS / Linux (bash/zsh):
```bash
"$HOME/.config/van-gogh/venv/bin/python" "${CLAUDE_PLUGIN_ROOT}/app/interview_capture.py" close --skill voice-bootstrap --status complete
```

Windows (PowerShell):
```powershell
& "$HOME\.config\van-gogh\venv\Scripts\python.exe" "$env:CLAUDE_PLUGIN_ROOT\app\interview_capture.py" close --skill voice-bootstrap --status complete
```

If the user stops partway instead, close it with `--status paused` so the
next run starts fresh rather than resuming an interview they abandoned.

**Step 8: Offer next steps**

- "Run /van-gogh:voice-generator any time for a fresh by-audience snapshot. It reads this file for the baseline comparison."
- "Want a condensed, prompt-ready version to paste into any drafting tool?"
- "If you only had email this round, send me 2 to 3 articles or posts later and I will deepen the baseline."

---

---

## When to ask the user

- Email fetch fails entirely: surface the error, and suggest confirming the API key is set and the email accounts are authenticated. If the user provided samples, you can still build the baseline from those alone; only the tone cards are lost.
- All categories have `sample_size < 3`: tell the user the lookback is thin and suggest `--days 180`.
- A provided link returns no usable text (paywall, JS-only): report it and ask for a paste of the text instead.
