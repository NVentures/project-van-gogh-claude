# The personal-skill API: what user code may lean on

Personal skills (`~/.claude/skills/`, scaffolded by `/van-gogh:create-skill`),
extension modules (`~/.config/van-gogh/extensions/`), and skills shipped in a
private companion plugin (`/van-gogh-custom:*`) live outside this plugin, but
run against its code. Most of `app/` is internals that may change
in any release. The names on THIS page are the supported surface: they are
guarded by `tests/test_personal_skill_surface.py`, so a refactor that breaks
one fails the plugin's own suite before it ships. Lean on this page; anything
not on it may vanish without notice.

## Reaching the plugin from user code

Interpreter: always the shared venv (`~/.config/van-gogh/venv/bin/python`,
Windows `~\.config\van-gogh\venv\Scripts\python.exe`). Imports: the plugin's
`app/` dir, found through the `~/.config/van-gogh/plugin-root` pointer file —
the scaffolder writes this bootstrap into every script skill:

```python
_state = Path(os.environ.get("VAN_GOGH_STATE_DIR", "") or Path.home() / ".config" / "van-gogh")
_plugin_root = Path((_state / "plugin-root").read_text(encoding="utf-8").strip())
sys.path.insert(0, str(_plugin_root / "app"))
```

Dependencies: whatever `requirements.txt` already puts in the shared venv.
A user may pip-install extras into that venv; the dependency sync only ever
installs, it never prunes, so they survive updates.

## Supported CLIs (shell out, read JSON)

| Command | What it prints |
|---|---|
| `app/skill_context.py` | `config_loader.resolved_meta()`: every resolved vault path, account label, business and client, ready to use verbatim |
| `app/context_pack.py --name "X" --email "x@y.com"` | the vault's record of a counterparty: `entity_page`, `meetings`, `open_commitments`, `deal_context`, `graph.neighbors`, each entry carrying its source path, plus `is_empty` |
| `app/draft_email.py --provider google --label "Gmail" --to ... --subject ... --body-file ...` | creates a DRAFT (never sends); `{"ok": true}` / `{"no_token": true}` / `{"existing": true}` |
| `app/meeting_fetch.py` | raw notetaker queries and `--check` diagnostics |
| `app/contact_capture.py [--apply] [--days N] [--account "L"] [--limit N] --json` | promotes two-way email contacts into Google Contacts; reports `candidates`, `promoted`, per-account `sample`. Writes NOTHING without `--apply` |
| `app/scaffold_skill.py --name x --kind script` | writes a personal skill skeleton; `{"ok": true, "path": ...}` |

## Supported Python imports

From `config_loader`: `cfg`, `vault`, `van_gogh_root`, `logs_dir`,
`resolved_meta`, `force_utf8_io`, `user_first_name`, `user_tz`, `businesses`,
`clients`, `entities_dir`, `sources_dir`, `hotcache_path`,
`briefing_functions`, `briefing_function_keywords`, `judgment_preamble`.

From `notetaker`: `fetch_meetings(today, since_str=...)`, `configured()`,
`display_name()`, `check()`.

From `collectors`: `meetings(days_back, errors)`,
`meeting_texts(days_back, errors)`.

From `user_state`: `state_dir()`, `extensions_dir()`, `load_env()`.

Rules, same as the plugin's own code: `encoding="utf-8"` on all file I/O,
`pathlib` joins, config only through `config_loader` (never read config.json
directly), no macOS-only shell commands, and outbound mail is drafts-first —
`send_email.py` runs only on an explicit per-message user instruction.

## Extension modules (`~/.config/van-gogh/extensions/`)

Machine-local, never synced, created by the user. Discovery is the filename;
contracts live in `app/extensions.py`:

- `briefing_section_<name>.py` — `TITLE`, optional `BRIEFINGS`
  (subset of `{"morning-coffee", "afternoon-tea", "week", "week-retro"}`),
  and `collect(ctx) -> str` markdown. Appears in every briefing's
  `extra_sections`; an exception becomes one line in `errors`, never a
  broken briefing.
- `notetaker_<name>.py` — the five-item provider contract documented at the
  top of `app/notetaker.py`. Joins the provider registry automatically;
  built-in names win a collision.

## Rendering like a briefing

A personal skill that renders items the way the briefings do should follow
[`front-page.md`](front-page.md) — the five-key contract, the render order,
and the written-file rules. Remember that its command examples anchor on
`${CLAUDE_PLUGIN_ROOT}`, which personal skills do not get: substitute the
pointer form shown at the top of this page.
