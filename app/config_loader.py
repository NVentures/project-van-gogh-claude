"""
config_loader.py — reads config.json from the user's Obsidian vault.

All Project Van Gogh state lives in a `van-gogh/` folder at the root of the
user's Obsidian vault: config.json, the rendered briefings, logs/, and the
projects/ workspace memory. The code repo holds only source and secrets.

Bootstrap: config.json stores `vault_path`, but it lives *inside* the vault, so
the loader can't read the vault path from a config it hasn't found yet. A small
pointer file in the per-user state dir (`~/.config/van-gogh/vault-pointer`, see
app/user_state.py) records the absolute vault path. The loader reads the
pointer → resolves `{vault}/van-gogh` → loads `{vault}/van-gogh/config.json`.
The pointer is written by `/install-van-gogh` (via app/migrate_to_vault.py);
legacy plugin-root `.van-gogh-vault` pointers self-migrate on first read.

Usage in scripts:
    from config_loader import cfg, vault, user_first_name, businesses, ...

See config.template.json for the full config schema.
"""

import json
import os
import re
import sys
from pathlib import Path
from zoneinfo import ZoneInfo

import user_state

# Every script imports config_loader before the third-party clients, so this is
# the choke point where a plain plugin update self-heals the venv (no-op unless
# requirements.txt changed — see user_state.sync_runtime_deps).
user_state.sync_runtime_deps()
# Same choke point keeps the plugin-root pointer current for user-authored
# personal skills (no-op unless the path changed — see record_plugin_root).
user_state.record_plugin_root()
# And keeps the managed context block in {vault}/CLAUDE.md current with the
# plugin template (no-op unless the block changed — see sync_vault_claude_block).
user_state.sync_vault_claude_block()
# And clears scheduled jobs orphaned by a skill rename, which otherwise keep
# firing daily into a command that no longer exists (no-op unless the legacy set
# changed — see user_state.sync_legacy_scheduler_jobs).
user_state.sync_legacy_scheduler_jobs()
# And refreshes the "a newer plugin is published" stamp at most once a day, so
# an install whose marketplace auto-update is off stops drifting silently
# (network-free on every other run — see plugin_update.check_quietly).
import plugin_update  # noqa: E402
plugin_update.check_quietly()

_REPO_ROOT = Path(__file__).parent.parent
_config = None

# Fallback timezone when user.timezone is unset (keeps legacy installs working).
_DEFAULT_TZ = "America/Los_Angeles"


def force_utf8_io() -> None:
    """Make stdout/stderr UTF-8 so scripts can print Unicode under any console.

    On Windows, stdout defaults to the locale codec (cp1252); printing box
    drawing or status glyphs (── ✓ ✗ ·) then raises UnicodeEncodeError and
    crashes the script. This silently killed the hourly auto-ingest, which died
    on its first print() before filing any meetings. Call once at the start of
    an entry-point main(). Idempotent and best-effort: a stream without
    .reconfigure (e.g. pytest's capture) is left untouched. This is the stdout
    sibling of the encoding="utf-8" rule that test_open_encoding guards for file
    I/O.
    """
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:
            continue
        try:
            reconfigure(encoding="utf-8", errors="replace")
        except (ValueError, OSError):
            pass


def _vault_from_pointer() -> Path:
    """Read the state-dir pointer file to locate the user's vault.

    Used only to bootstrap the initial config load. Once config is loaded,
    van_gogh_root() derives the vault from cfg() instead (see below), which
    keeps the whole path surface testable via a primed _config fixture.
    """
    pointer = user_state.pointer_file()
    if not pointer.exists():
        raise FileNotFoundError(
            f"Vault pointer not found at {pointer}. "
            "Run /van-gogh:install-van-gogh to set up Project Van Gogh."
        )
    raw = pointer.read_text(encoding="utf-8").strip()
    if not raw:
        raise FileNotFoundError(
            f"Vault pointer at {pointer} is empty. "
            "Run /van-gogh:install-van-gogh to set up Project Van Gogh."
        )
    return Path(os.path.expanduser(raw))


def cfg() -> dict:
    global _config
    if _config is None:
        config_path = _vault_from_pointer() / "van-gogh" / "config.json"
        if not config_path.exists():
            raise FileNotFoundError(
                f"config.json not found at {config_path}. "
                "Run /van-gogh:install-van-gogh to set up Project Van Gogh."
            )
        with open(config_path, encoding="utf-8") as f:
            _config = json.load(f)
    return _config


# ── Vault paths ───────────────────────────────────────────────────────────────

def vault(subpath: str = "") -> Path:
    """Return an absolute path inside the user's Obsidian vault."""
    base = Path(os.path.expanduser(cfg()["obsidian"]["vault_path"]))
    return base / subpath if subpath else base


def van_gogh_root() -> Path:
    """The `van-gogh/` state folder at the root of the user's vault.

    Home for config.json, the rendered briefings, logs/, and projects/.
    Derived from cfg() (not the pointer file) so it resolves correctly under
    a primed test fixture with no pointer present.
    """
    return vault("van-gogh")


def ensure_van_gogh_root() -> Path:
    """van_gogh_root(), created if missing. Call before writing into it."""
    root = van_gogh_root()
    root.mkdir(parents=True, exist_ok=True)
    return root


def hotcache_path() -> Path:
    return vault(cfg()["obsidian"]["hotcache_relpath"])


def sources_dir() -> Path:
    return vault(cfg()["obsidian"]["sources_relpath"])


def weekly_dir() -> Path:
    return vault(cfg()["obsidian"]["weekly_relpath"])


def entities_dir() -> Path:
    return vault(cfg()["obsidian"].get("entities_relpath", "wiki/entities"))


def hotcache_action_items_heading() -> str:
    return cfg()["obsidian"]["hotcache_action_items_heading"]


def hotcache_active_threads_heading() -> str:
    return cfg()["obsidian"].get("hotcache_active_threads_heading", "Active Threads")


# ── Voice + relationship artifacts (vault-relative) ────────────────────────────

def voice_guide_path() -> Path:
    return vault(cfg()["obsidian"].get("voice_guide_relpath", "wiki/sources/voice-guide.md"))


def tone_profile_path() -> Path:
    return vault(cfg()["obsidian"].get("tone_profile_relpath", "wiki/sources/tone-profile.md"))


def voice_snapshot_path() -> Path:
    return vault(cfg()["obsidian"].get("voice_snapshot_relpath", "wiki/sources/Voice Snapshot.md"))


def voice_drafts_path() -> Path:
    return vault(cfg()["obsidian"].get("voice_drafts_relpath", "wiki/sources/voice-drafts.md"))


def relationship_radar_path() -> Path:
    return vault(cfg()["obsidian"].get("relationship_radar_relpath", "wiki/sources/relationship-radar.md"))


# ── User identity ─────────────────────────────────────────────────────────────

def _user() -> dict:
    return cfg()["user"]


def user_name() -> str:
    return _user()["full_name"]


def user_first_name() -> str:
    return _user()["first_name"]


def user_role_description() -> str:
    return _user().get("role_description", "")


def user_bio_descriptor() -> str:
    """A one-line persona used in LLM prompts (e.g. voice generation).
    Falls back to the bare name when no role_description is configured.
    """
    role = user_role_description()
    return f"{user_name()}, {role}" if role else user_name()


def user_name_variants() -> list:
    """Strings used to match the user's own name in action items, etc."""
    return list(_user().get("name_variants", [_user()["full_name"]]))


def user_self_entities() -> set:
    """Entity names that represent the user (excluded from business voting)."""
    return set(_user().get("self_entities", [_user()["full_name"]]))


def user_tz() -> ZoneInfo:
    """The user's primary timezone as a ``ZoneInfo``.

    Reads the IANA name from ``user.timezone`` (e.g. ``America/Los_Angeles``).
    Falls back to ``America/Los_Angeles`` when unset or unrecognized, so
    existing installs that predate the timezone field keep working. Every
    date-relative window (today/tomorrow) and every rendered time computes
    against this zone, not a hardcoded Pacific.
    """
    name = (_user().get("timezone") or "").strip() or _DEFAULT_TZ
    try:
        return ZoneInfo(name)
    except Exception:
        return ZoneInfo(_DEFAULT_TZ)


def user_secondary_tz() -> ZoneInfo | None:
    """Optional secondary timezone for a side-by-side dual display.

    Reads the IANA name from ``user.secondary_timezone``. Returns ``None`` when
    unset (the default for new installs, which render a single zone). When set,
    briefings render both zones side by side (e.g. a PT / ET preference).
    An unrecognized value is treated as unset.
    """
    name = (_user().get("secondary_timezone") or "").strip()
    if not name:
        return None
    try:
        return ZoneInfo(name)
    except Exception:
        return None


def user_name_regex() -> re.Pattern:
    """Regex matching any of the user's name variants as a word."""
    variants = user_name_variants()
    if not variants:
        return re.compile(r"(?!x)x")  # never matches
    parts = [re.escape(v) for v in sorted(variants, key=len, reverse=True)]
    return re.compile(r"\b(?:" + "|".join(parts) + r")\b")


# ── Accounts ──────────────────────────────────────────────────────────────────

GOOGLE = "google"
MICROSOFT = "microsoft"


def _normalize_accounts(raw) -> list:
    """Return the accounts config in the N-account list shape.

    Accepts either the current list-of-objects shape or the legacy flat-dict
    shape (``google_primary_*`` / ``google_secondary_*`` / ``microsoft_*``) and
    migrates the latter on read, so existing installs keep working without a
    config rewrite. Each returned account is a dict with keys
    ``provider`` (``google``|``microsoft``), ``email``, ``label``,
    ``is_primary`` (bool), and ``sent_folder_id`` (microsoft only; "" otherwise).

    Invariant enforced here: exactly one account has ``is_primary: true``
    (if none is marked, the first becomes primary; extras are demoted).
    """
    if isinstance(raw, list):
        out = [dict(a) for a in raw if a.get("email")]
    else:
        raw = raw or {}
        out = []
        gp = raw.get("google_primary_email", "")
        if gp:
            out.append({
                "provider": GOOGLE, "email": gp,
                "label": raw.get("google_primary_label", "Gmail"),
                "is_primary": True, "sent_folder_id": "",
            })
        gs = raw.get("google_secondary_email", "")
        if gs:
            out.append({
                "provider": GOOGLE, "email": gs,
                "label": raw.get("google_secondary_label", "Work"),
                "is_primary": False, "sent_folder_id": "",
            })
        ms = raw.get("microsoft_email", "")
        if ms:
            out.append({
                "provider": MICROSOFT, "email": ms,
                "label": raw.get("microsoft_label", "Outlook"),
                "is_primary": False,
                "sent_folder_id": raw.get("outlook_sent_folder_id", ""),
            })

    for a in out:
        a["provider"] = a.get("provider", GOOGLE)
        a.setdefault("label", a.get("email", ""))
        a.setdefault("sent_folder_id", "")
        a["is_primary"] = bool(a.get("is_primary", False))

    # Enforce exactly-one-primary: keep the first marked primary, demote the
    # rest; if none is marked, the first configured account becomes primary.
    primaries = [a for a in out if a["is_primary"]]
    if out and not primaries:
        out[0]["is_primary"] = True
    elif len(primaries) > 1:
        seen = False
        for a in out:
            if a["is_primary"]:
                if seen:
                    a["is_primary"] = False
                seen = True
    return out


def accounts() -> list:
    """All configured email/calendar accounts, normalized to the list shape.

    See _normalize_accounts: legacy flat-dict configs are migrated on read.
    """
    return _normalize_accounts(cfg().get("accounts"))


def google_accounts() -> list:
    return [a for a in accounts() if a["provider"] == GOOGLE]


def microsoft_accounts() -> list:
    return [a for a in accounts() if a["provider"] == MICROSOFT]


def primary_account() -> dict | None:
    """The account designated primary (or the first configured one)."""
    accts = accounts()
    for a in accts:
        if a["is_primary"]:
            return a
    return accts[0] if accts else None


def account_labels() -> list:
    return [a["label"] for a in accounts()]


def account_emails() -> list:
    return [a["email"] for a in accounts()]


def account_emails_lower() -> set:
    """Lowercased set of every configured account email (for is-self checks)."""
    return {a["email"].lower() for a in accounts() if a.get("email")}


# ── Businesses / projects ─────────────────────────────────────────────────────

def businesses() -> list:
    return list(cfg().get("businesses", []))


def business_tags() -> set:
    return {b["tag"] for b in businesses()}


def project_pages() -> dict:
    """Map display_name → absolute Path inside vault."""
    return {b["display_name"]: vault(b["project_page"]) for b in businesses()}


def meeting_routes() -> dict:
    """Map business tag → vault-relative meeting folder."""
    return {b["tag"]: b["meeting_route"] for b in businesses()}


def _business_match(text: str) -> dict | None:
    """Return the first business whose keywords match `text`, else None.

    Matches are case-insensitive substring matches on the lowercased text.
    One definition, two callers: `business_for_text` wants the display name
    and `business_tag_for_text` wants the tag, and they must never disagree
    about which business a piece of text belongs to.
    """
    t = (text or "").lower()
    for b in businesses():
        for kw in b.get("keywords", []):
            if kw and kw.lower() in t:
                return b
    return None


def business_display_name(tag: str) -> str:
    """The name the user gave a business, from its tag. The tag if unknown.

    Anything a reader sees says the name they wrote, never the tag: a tag is
    lowercase and shaped for code, and it reads as a leak in the middle of a
    sentence.
    """
    for b in businesses():
        if b.get("tag") == tag:
            return b.get("display_name") or tag
    return tag


def business_for_text(text: str) -> str | None:
    """Return the display_name of the first business whose keywords match `text`.
    Returns None if no business matches.
    """
    m = _business_match(text)
    return m["display_name"] if m else None


def business_tag_for_text(text: str) -> str | None:
    """Return the tag of the first business whose keywords match `text`.
    Returns None if no business matches. Never guesses: an unmatched string
    stays unassigned rather than landing in whichever bucket looks closest.
    """
    m = _business_match(text)
    return m["tag"] if m else None


def business_priorities(tag: str) -> list:
    """The user's stated priorities inside one business bucket.

    This is the durable write target for "here are my top four things in
    power market". Returns [] for an unknown tag or a business that has
    never had priorities set, so every caller can treat the empty list as
    "no priorities configured" rather than an error.
    """
    for b in businesses():
        if b.get("tag") == tag:
            return list(b.get("priorities", []))
    return []


# ── Advisory clients (5:15 reports) ───────────────────────────────────────────

def clients() -> list:
    """Advisory clients the user writes a weekly 5:15 report for."""
    return list(cfg().get("clients", []))


def client_tags() -> set:
    return {c["tag"] for c in clients()}


def client_by_tag(tag: str) -> dict | None:
    for c in clients():
        if c.get("tag") == tag:
            return c
    return None


def client_report_dir(tag: str) -> Path | None:
    """Absolute path to a client's 5:15 report archive folder inside the vault."""
    c = client_by_tag(tag)
    if not c or not c.get("report_dir"):
        return None
    return vault(c["report_dir"])


def account_by_label(label: str) -> dict | None:
    """Resolve an account `label` to {label, email, platform}.

    platform is "gmail" or "outlook". Used to turn a client's `from_account`
    label into the mailbox the 5:15 is sent from. Returns None if unmatched.
    """
    for a in accounts():
        if a.get("label") == label and a.get("email"):
            platform = "gmail" if a.get("provider") == GOOGLE else "outlook"
            return {"label": a["label"], "email": a["email"], "platform": platform}
    return None


def _resolved_client(c: dict) -> dict:
    """Shape one client config entry for skill consumers (resolved paths/account)."""
    acct = account_by_label(c.get("from_account", "")) or {}
    report_dir = c.get("report_dir")
    return {
        "tag": c.get("tag"),
        "display_name": c.get("display_name"),
        "recipient_name": c.get("recipient_name", ""),
        "recipient_email": c.get("recipient_email", ""),
        "from_account": c.get("from_account", ""),
        "from_email": acct.get("email", ""),
        "from_platform": acct.get("platform", ""),
        "sources": list(c.get("sources", [])),
        "signoff": c.get("signoff", ""),
        "keywords": list(c.get("keywords", [])),
        "report_dir": str(vault(report_dir)) if report_dir else "",
        # The engagement terms the 5:15 measures against ("true north").
        # Optional; {} when a client has no contract block configured.
        "contract": c.get("contract", {}),
    }


def resolved_client(tag: str) -> dict | None:
    """The resolved 5:15 shape for one client tag, or None if the tag is unknown."""
    c = client_by_tag(tag)
    return _resolved_client(c) if c else None


# ── Follow-ups (date-triggered tickler) ────────────────────────────────────────

def _follow_ups() -> dict:
    return cfg().get("follow_ups", {})


def follow_ups_horizon_days() -> int:
    """Days ahead a trigger date is still surfaced as `due_soon` (default 4)."""
    return int(_follow_ups().get("horizon_days", 4))


def follow_ups_memory_path() -> Path:
    """Absolute path to the file holding the "## Pending follow-ups" ledger.

    Defaults to the vault workspace memory ({vault}/van-gogh/projects/Project
    Van Gogh/memory.md — seeded on install), so the ledger lives with the rest
    of the user's vault state and survives plugin updates. Override with
    `follow_ups.memory_path` (absolute or ~-relative) to point anywhere else.
    Returns the resolved path even when the file does not exist yet, so callers
    surface a clear "file not found" pointing at the expected location.
    """
    override = str(_follow_ups().get("memory_path", "")).strip()
    if override:
        return Path(os.path.expanduser(override))
    return vangogh_memory_path()


# ── Podcasts (local transcription) ────────────────────────────────────────────

def podcast_feeds() -> dict:
    """Show-key → RSS feed URL registry for app/podcast_transcribe.py."""
    return dict(cfg().get("podcasts", {}).get("feeds", {}))


# ── Notetaker ─────────────────────────────────────────────────────────────────

def notetaker_provider() -> str:
    """Which meeting-notetaker service is active ("granola", "grain", ...).

    Empty string means unset, and notetaker.active_name() infers from which API
    key is present — so an existing Granola install keeps working without ever
    touching config.json. See app/notetaker.py for the provider registry.
    """
    return str(cfg().get("notetaker", {}).get("provider", "") or "").strip().lower()


# ── Email filters ─────────────────────────────────────────────────────────────

def _email_filters() -> dict:
    return cfg().get("email_filters", {})


def allow_domains() -> set:
    return set(_email_filters().get("allow_domains", []))


def internal_domains() -> set:
    return set(_email_filters().get("internal_domains", []))


def internal_team_emails() -> set:
    return set(_email_filters().get("internal_team_emails", []))


def internal_team_names() -> set:
    """Display names of internal teammates — used for name-based skipping
    (e.g. relationship radar) where only an entity-page title is available."""
    return set(_email_filters().get("internal_team_names", []))


def extra_spam_fragments() -> list:
    return list(_email_filters().get("extra_spam_fragments", []))


def deal_critical_domains() -> set:
    """Sender domains that always surface tier-1 — bypass Haiku's suppress call.

    Same defensive pattern as internal_team_emails: a misclassification must
    never silently drop a known-important counterparty (counsel, lender,
    direct partner, etc.). Configured via email_filters.deal_critical_domains.
    """
    return set(_email_filters().get("deal_critical_domains", []))


# ── Relationship radar ─────────────────────────────────────────────────────────

def _radar() -> dict:
    return cfg().get("relationship_radar", {})


def radar_yellow_days() -> int:
    return int(_radar().get("yellow_days", 30))


def radar_red_days() -> int:
    return int(_radar().get("red_days", 60))


def radar_yellow_draft_cap() -> int:
    return int(_radar().get("yellow_draft_cap", 5))


def radar_skip_tags() -> set:
    return set(_radar().get("skip_tags", []))


def radar_skip_entity_names() -> set:
    """Entity-page titles to skip in relationship radar (orgs, etc.)."""
    return set(_radar().get("skip_entity_names", []))


# ── Email digest ───────────────────────────────────────────────────────────────

_WEEKDAYS = ["monday", "tuesday", "wednesday", "thursday", "friday"]

# Per-briefing cadence defaults; config digest.briefings entries override.
DIGEST_BRIEFING_DEFAULTS = {
    "morning-coffee": {"enabled": True, "days": list(_WEEKDAYS), "time": "07:00"},
    "afternoon-tea": {"enabled": True, "days": list(_WEEKDAYS), "time": "13:00"},
    "week": {"enabled": True, "days": ["monday"], "time": "07:00"},
    "week-retro": {"enabled": True, "days": ["friday"], "time": "07:00"},
}


def _digest() -> dict:
    return cfg().get("digest", {}) or {}


def digest_enabled() -> bool:
    """Master opt-in for emailed briefings (default off)."""
    return bool(_digest().get("enabled", False))


def digest_sender_account() -> dict | None:
    """The configured account digests send from: digest.sender_label if it
    matches an account label (case-insensitive), else the primary account."""
    label = (_digest().get("sender_label") or "").strip()
    if label:
        for a in accounts():
            if a["label"].lower() == label.lower():
                return a
    return primary_account()


def digest_recipient_email() -> str:
    """Where digests are delivered: digest.recipient_email, else the primary
    account's address."""
    to = (_digest().get("recipient_email") or "").strip()
    if to:
        return to
    p = primary_account()
    return p["email"] if p else ""


def digest_briefings() -> dict:
    """Per-briefing cadence config: defaults merged under any config overrides.
    Keys: enabled (bool), days (lowercase weekday names), time ("HH:MM")."""
    raw = _digest().get("briefings", {}) or {}
    out = {}
    for name, defaults in DIGEST_BRIEFING_DEFAULTS.items():
        merged = dict(defaults)
        merged.update(raw.get(name, {}) or {})
        out[name] = merged
    return out


# ── Meeting prep email ────────────────────────────────────────────────────────

# Modes: "each" mails one prep per meeting, about an hour ahead; "daily" mails
# one email covering the whole day. Anything else reads as off, so a typo in the
# config costs the feature rather than mailing on a schedule nobody chose.
PREP_MODES = ("each", "daily")

PREP_DEFAULTS = {
    "enabled": False,
    "mode": "each",
    "lead_minutes": 60,
    "daily_time": "07:00",
    "internal_only": False,
}


def _prep() -> dict:
    return cfg().get("meeting_prep", {}) or {}


def prep_email_enabled() -> bool:
    """Master opt-in for prep emails (default off).

    Off by default for the same reason alerts are: a fresh install that starts
    mailing before the user asked is a worse surprise than a quiet one.
    """
    return bool(_prep().get("enabled", PREP_DEFAULTS["enabled"]))


def prep_mode() -> str:
    """"each" (one email per meeting) or "daily" (one email for the day)."""
    mode = str(_prep().get("mode", PREP_DEFAULTS["mode"])).strip().lower()
    return mode if mode in PREP_MODES else PREP_DEFAULTS["mode"]


def prep_lead_minutes() -> int:
    """How far ahead of a meeting an "each" prep is mailed.

    Config rather than a constant because the right answer is personal: the
    default 60 leaves room for a headless render that retries, but someone who
    wants it closer to the call can say so without a code change. Clamped to
    the range a 15 minute tick can actually honour.
    """
    try:
        value = int(_prep().get("lead_minutes", PREP_DEFAULTS["lead_minutes"]))
    except (TypeError, ValueError):
        return PREP_DEFAULTS["lead_minutes"]
    return max(15, min(240, value))


def prep_daily_time() -> str:
    """Local "HH:MM" the daily digest of preps goes out. Invalid reads as default."""
    raw = str(_prep().get("daily_time", PREP_DEFAULTS["daily_time"])).strip()
    try:
        hour, minute = raw.split(":")
        if 0 <= int(hour) <= 23 and 0 <= int(minute) <= 59:
            return f"{int(hour):02d}:{int(minute):02d}"
    except (TypeError, ValueError):
        pass
    return PREP_DEFAULTS["daily_time"]


def prep_include_internal() -> bool:
    """Whether meetings with no external attendee get a prep (default no).

    An internal sync has nothing to look up, and mailing one costs a headless
    render for a page that would read "no entity page" all the way down.
    """
    return bool(_prep().get("internal_only", PREP_DEFAULTS["internal_only"]))


# ── The Note ──────────────────────────────────────────────────────────────────

# The watcher between briefings (app/note_send.py). Off by default like every
# other thing that mails. The cap is two because three daily batches was the
# winning condition in the one randomized trial that measured notification
# batching, and the two briefings already take two of those slots. The window
# keeps a 2 AM reply from becoming a 2 AM Note.
NOTE_DEFAULTS = {
    "enabled": False,
    "daily_cap": 2,
    "from": "07:30",
    "until": "18:00",
}


def _notes() -> dict:
    return cfg().get("notes", {}) or {}


def notes_enabled() -> bool:
    """Master opt-in for Notes (default off)."""
    return bool(_notes().get("enabled", NOTE_DEFAULTS["enabled"]))


def notes_daily_cap() -> int:
    """How many Notes may go out in one day; the rest fold into Afternoon Tea.
    Clamped to 1..5: zero would be a feature that is on and never speaks."""
    try:
        value = int(_notes().get("daily_cap", NOTE_DEFAULTS["daily_cap"]))
    except (TypeError, ValueError):
        return NOTE_DEFAULTS["daily_cap"]
    return max(1, min(5, value))


def _hhmm(raw, default: str) -> str:
    text = str(raw if raw is not None else default).strip()
    try:
        hour, minute = text.split(":")
        if 0 <= int(hour) <= 23 and 0 <= int(minute) <= 59:
            return f"{int(hour):02d}:{int(minute):02d}"
    except (TypeError, ValueError):
        pass
    return default


def notes_window() -> tuple:
    """(from, until) as local "HH:MM". Invalid halves read as their default."""
    return (_hhmm(_notes().get("from"), NOTE_DEFAULTS["from"]),
            _hhmm(_notes().get("until"), NOTE_DEFAULTS["until"]))


# ── The weekly scorecard ──────────────────────────────────────────────────────

# Defaults live here, beside the accessors that read them, so the template and
# the code cannot drift; tests/test_config_template.py compares the two.
KPI_DEFAULTS = {
    "enabled": False,
    "day": "friday",
    "time": "16:00",
    "window_days": 14,
    "recipient_email": "",
    "share_with_support": False,
}

_WEEKDAYS = ("monday", "tuesday", "wednesday", "thursday", "friday",
             "saturday", "sunday")


def _kpi() -> dict:
    return cfg().get("kpi", {}) or {}


def kpi_enabled() -> bool:
    """Master opt-in for the weekly scorecard email. Ships off.

    Same reason as alerts and prep emails: an install that starts mailing
    before the user asked for it is a worse surprise than a quiet one.
    """
    return bool(_kpi().get("enabled", KPI_DEFAULTS["enabled"]))


def kpi_day() -> str:
    """Weekday the scorecard is sent, lowercase. A junk value reads as the
    default rather than crashing a scheduled job or, worse, scheduling
    nothing."""
    day = str(_kpi().get("day", KPI_DEFAULTS["day"])).strip().lower()
    return day if day in _WEEKDAYS else KPI_DEFAULTS["day"]


def kpi_time() -> str:
    """Send time as `HH:MM`, computer-local. Junk reads as the default."""
    raw = str(_kpi().get("time", KPI_DEFAULTS["time"])).strip()
    try:
        hour, minute = (int(part) for part in raw.split(":", 1))
    except (TypeError, ValueError):
        return KPI_DEFAULTS["time"]
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        return KPI_DEFAULTS["time"]
    return f"{hour:02d}:{minute:02d}"


def kpi_window_days() -> int:
    """How many days the scorecard looks back, clamped to a sane band.

    Two halves are compared, so the window must be even and at least a
    fortnight for a week-over-week comparison to mean anything.
    """
    try:
        days = int(_kpi().get("window_days", KPI_DEFAULTS["window_days"]))
    except (TypeError, ValueError):
        return KPI_DEFAULTS["window_days"]
    days = max(14, min(56, days))
    return days if days % 2 == 0 else days + 1


def kpi_recipient_email() -> str:
    """Where the scorecard goes, falling back to the digest recipient.

    Its own setting on purpose: the scorecard is personal performance data, and
    a user who points briefings at an assistant should not have to send this
    there too. Empty means "wherever the briefings already go".
    """
    own = str(_kpi().get("recipient_email", KPI_DEFAULTS["recipient_email"]) or "").strip()
    return own or digest_recipient_email()


def kpi_share_with_support() -> bool:
    """Whether the same numbers-only email also goes to the support address."""
    return bool(_kpi().get("share_with_support",
                           KPI_DEFAULTS["share_with_support"]))


# ── The weekly finance brief ──────────────────────────────────────────────────

FINANCE_DEFAULTS = {
    "enabled": False,
    "day": "monday",
    "time": "07:00",
    "recipient_email": "",
    "company_id": "",
    "company_name": "",
    "commentary": True,
}


def _finance() -> dict:
    return cfg().get("finance", {}) or {}


def finance_enabled() -> bool:
    """Master opt-in for the weekly finance brief. Ships off.

    It reads the user's books, so it starts only when they ask for it.
    """
    return bool(_finance().get("enabled", FINANCE_DEFAULTS["enabled"]))


def finance_day() -> str:
    """Weekday the brief is sent, lowercase. Junk reads as the default."""
    day = str(_finance().get("day", FINANCE_DEFAULTS["day"])).strip().lower()
    return day if day in _WEEKDAYS else FINANCE_DEFAULTS["day"]


def finance_time() -> str:
    """Send time as `HH:MM`, computer-local. Junk reads as the default."""
    raw = str(_finance().get("time", FINANCE_DEFAULTS["time"])).strip()
    try:
        hour, minute = (int(part) for part in raw.split(":", 1))
    except (TypeError, ValueError):
        return FINANCE_DEFAULTS["time"]
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        return FINANCE_DEFAULTS["time"]
    return f"{hour:02d}:{minute:02d}"


def finance_recipient_email() -> str:
    """Where the brief goes, falling back to the digest recipient.

    Its own setting because the books are not the briefings: a user who points
    daily mail at an assistant may well not want the cash position going there.
    """
    own = str(_finance().get("recipient_email",
                             FINANCE_DEFAULTS["recipient_email"]) or "").strip()
    return own or digest_recipient_email()


def finance_company_id() -> str:
    """The QuickBooks company this brief belongs to.

    Written at install from the connector's own answer. A connector can only be
    authorized against one company at a time, and someone who runs several
    cannot tell from the figures which one replied, so every run checks the id
    it got against this and refuses to render numbers that are not this
    company's. Empty means the check has not been set up yet.
    """
    return str(_finance().get("company_id",
                              FINANCE_DEFAULTS["company_id"]) or "").strip()


def finance_company_name() -> str:
    """The company name to show, and to name when the wrong books answer."""
    return str(_finance().get("company_name",
                              FINANCE_DEFAULTS["company_name"]) or "").strip()


def finance_commentary() -> bool:
    """Whether the brief carries the controller's read under the numbers."""
    return bool(_finance().get("commentary", FINANCE_DEFAULTS["commentary"]))


# ── Contact capture ───────────────────────────────────────────────────────────

CONTACT_CAPTURE_DEFAULTS = {
    "enabled": False,
    "day": "sunday",
    "time": "05:00",
    "window_days": 14,
}


def _contact_capture() -> dict:
    return cfg().get("contact_capture", {}) or {}


def contact_capture_enabled() -> bool:
    """Master opt-in for the weekly contact capture. Ships off.

    This one writes to the user's real address book, which is the strongest
    reason of any opt-in here to stay off until asked for.
    """
    return bool(_contact_capture().get(
        "enabled", CONTACT_CAPTURE_DEFAULTS["enabled"]))


def contact_capture_day() -> str:
    """Weekday the capture runs, lowercase. Junk reads as the default."""
    day = str(_contact_capture().get(
        "day", CONTACT_CAPTURE_DEFAULTS["day"])).strip().lower()
    return day if day in _WEEKDAYS else CONTACT_CAPTURE_DEFAULTS["day"]


def contact_capture_time() -> str:
    """Run time as `HH:MM`, computer-local. Junk reads as the default."""
    raw = str(_contact_capture().get(
        "time", CONTACT_CAPTURE_DEFAULTS["time"])).strip()
    try:
        hour, minute = (int(part) for part in raw.split(":", 1))
    except (TypeError, ValueError):
        return CONTACT_CAPTURE_DEFAULTS["time"]
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        return CONTACT_CAPTURE_DEFAULTS["time"]
    return f"{hour:02d}:{minute:02d}"


def contact_capture_window_days() -> int:
    """How far back each weekly run looks, clamped to a sane band.

    Deliberately wider than the seven days between runs: a machine asleep on
    its slot would otherwise leave a permanent hole in the address book.
    """
    try:
        days = int(_contact_capture().get(
            "window_days", CONTACT_CAPTURE_DEFAULTS["window_days"]))
    except (TypeError, ValueError):
        return CONTACT_CAPTURE_DEFAULTS["window_days"]
    return max(7, min(3650, days))


# ── Support and alerts ────────────────────────────────────────────────────────

def _support() -> dict:
    return cfg().get("support", {}) or {}


def support_team_email() -> str:
    """A second address for failure alerts, e.g. whoever set this install up."""
    return (_support().get("team_email") or "").strip()


def alerts_enabled() -> bool:
    """Master opt-in for failure alert emails. Ships off: an unattended job
    that starts mailing on its own is a worse surprise than a quiet one."""
    return bool(_support().get("alerts_enabled", False))


def alert_cooldown_days() -> int:
    """Days before the same failure signature may alert again. A junk value
    falls back rather than crashing a scheduled run at 07:00."""
    try:
        return int(_support().get("alert_cooldown_days", 7))
    except (TypeError, ValueError):
        return 7


def job_watch_enabled() -> bool:
    """Whether the watcher re-runs jobs that failed or never fired.

    Ships ON, unlike alerts: this changes nothing outside the machine and it
    is the difference between a briefing that misses one morning and a
    briefing that quietly stops arriving. Turning it off leaves every job
    exactly as it was before the watcher existed."""
    return bool(_support().get("job_watch_enabled", True))


def job_watch_kick_cap() -> int:
    """How many times one missed slot may be re-run before it needs a human.

    Three is two more chances than the old behaviour and few enough that a
    genuinely broken job stops burning tokens by lunchtime."""
    try:
        return int(_support().get("job_watch_kick_cap", 3))
    except (TypeError, ValueError):
        return 3


def repair_agent_enabled() -> bool:
    """Whether a stuck job gets looked at by the mechanic (default on).

    On by default, like the watcher and unlike alerts: it changes nothing
    outside the machine, sends nothing, and the alternative is a job that
    fails identically every morning forever with nobody reading the log. It
    costs one model call per stuck job per day, capped, and only after a
    re-run has already been proven not to help."""
    return bool(_support().get("repair_agent_enabled", True))


def task_stall_enabled() -> bool:
    """Whether the briefing mentions work that stopped moving (default on).

    Reads two files already on disk and costs nothing. Off leaves the
    briefings exactly as they were."""
    return bool(_support().get("task_stall_enabled", True))


def setup_hint(exc: Exception) -> str:
    """Turn a config failure into one line a non-technical person can act on.

    A traceback is not an error message, it is a support call. Every script a
    user can reach should end here rather than in a stack trace.
    """
    msg = str(exc).strip() or exc.__class__.__name__
    if "install-van-gogh" in msg or "/van-gogh:" in msg:
        return msg
    return (f"{msg}\n"
            "If Project Van Gogh has not been set up on this computer yet, run "
            "/van-gogh:install-van-gogh.")


# ── Briefing front page ───────────────────────────────────────────────────────

def _briefing() -> dict:
    return cfg().get("briefing", {}) or {}


# Kept here rather than imported from week_review so config_loader stays a leaf:
# every script imports it, and it must not pull a renderer in behind it.
BRIEFING_FUNCTIONS_DEFAULT = ["Sales", "Finance", "Accounting", "Legal",
                              "People", "Operations", "Product", "Admin",
                              "Other"]


def briefing_front_page_cap() -> int:
    """How many non-late items reach the front page. Clamped 3 to 9.

    Below three the page stops being a plan and above nine it stops being a
    page, so a typo in config.json cannot turn the briefing back into a wall.
    """
    try:
        cap = int(_briefing().get("front_page_cap", 7))
    except (TypeError, ValueError):
        cap = 7
    return max(3, min(9, cap))


def briefing_functions() -> list:
    """The function vocabulary items are filed under inside a business."""
    names = _briefing().get("functions")
    if not isinstance(names, list) or not [n for n in names if str(n).strip()]:
        return list(BRIEFING_FUNCTIONS_DEFAULT)
    return [str(n).strip() for n in names if str(n).strip()]


def briefing_publish_page() -> bool:
    """Whether each briefing keeps one permanent private web page."""
    return bool(_briefing().get("publish_page", True))


def briefing_function_keywords() -> dict:
    """User keyword lists per function name: {"FunctionName": ["keyword", ...]}.

    The built-in keyword→function table is code (week_review.py); this is the
    user's additions, which is what makes a custom name in briefing.functions[]
    routable by keyword instead of reachable only through the classifier.
    Keywords are lowercased here because matching is against lowercased text.
    """
    raw = _briefing().get("function_keywords")
    if not isinstance(raw, dict):
        return {}
    out = {}
    for name, words in raw.items():
        name = str(name).strip()
        if not name or not isinstance(words, list):
            continue
        cleaned = [str(w).strip().lower() for w in words if str(w).strip()]
        if cleaned:
            out[name] = cleaned
    return out


# ── Priority judgment ─────────────────────────────────────────────────────────

def _judgment() -> dict:
    return cfg().get("judgment", {}) or {}


def judgment_enabled() -> bool:
    """Whether an item's relevance to a stated priority is decided by reading
    it. Off means fall back to word overlap, which cannot connect "grow the
    pipeline" to a signature deadline.

    Defaults ON, including for a config that predates the key. That is safe
    even though it costs model calls, because the call only happens for a
    bucket that HAS priorities and items, and setting a priority is a
    deliberate act. An existing client who has never set one pays nothing and
    notices nothing; the moment they set one, they get the good behavior
    without having to discover a setting first.
    """
    return bool(_judgment().get("enabled", True))


def judgment_model() -> str:
    """Empty means the module default. Configurable so an install can trade
    judgment quality for cost."""
    return (_judgment().get("model") or "").strip()


# Standing guidance is user prose appended to a prompt; the cap keeps a pasted
# document from drowning the items the model is actually judging.
JUDGMENT_PREAMBLE_MAX_CHARS = 4000


def judgment_preamble_path() -> Path:
    """`{vault}/van-gogh/prompts/priority-judge.md` — optional user guidance."""
    return van_gogh_root() / "prompts" / "priority-judge.md"


def judgment_preamble() -> str:
    """The user's standing guidance for the priority judgment, or "".

    Vault-resident on purpose (it is the user's words, like config, and should
    travel with the second brain), and fail-open: a missing or unreadable file
    means no guidance, never a broken briefing. Missing is quiet (not using
    the feature is normal); a file that EXISTS but cannot be read gets one
    stderr line, because the user believes their guidance applies and silence
    would hide that it does not.
    """
    path = judgment_preamble_path()
    try:
        text = path.read_text(encoding="utf-8-sig").strip()
    except FileNotFoundError:
        return ""
    except Exception as e:
        import sys
        print(f"van-gogh: {path} exists but could not be read ({e}); "
              "judgment guidance NOT applied", file=sys.stderr)
        return ""
    return text[:JUDGMENT_PREAMBLE_MAX_CHARS]


# ── Chat memory mirror ────────────────────────────────────────────────────────

def _memory_sync() -> dict:
    return cfg().get("memory_sync", {}) or {}


def memory_sync_enabled() -> bool:
    """Master opt-in for mirroring chat memory into the vault. Ships off.

    A machine can hold Claude Code projects for several unrelated clients, so
    an on-by-default mirror would copy one client's memory into another
    client's vault. Opting in is the user saying which projects are theirs.
    """
    return bool(_memory_sync().get("enabled", False))


def memory_source_dirs() -> list:
    """Explicit Claude Code project directories to mirror memory from.

    Empty means "let memory_sync derive the two obvious ones" (the vault and
    the repo), never "scan everything". Returns [] when the feature is off,
    whatever is configured, so a stale config can't leak on a disabled install.
    """
    if not memory_sync_enabled():
        return []
    return [str(d) for d in _memory_sync().get("claude_project_dirs", []) if d]


def memory_target_dir() -> Path:
    """Where mirrored chat memory lands inside the vault."""
    return vault(_memory_sync().get("target_relpath") or "wiki/memory")


# ── Workbench ─────────────────────────────────────────────────────────────────

def _workbench() -> dict:
    return cfg().get("workbench", {}) or {}


def workbench_port() -> int:
    """Port the local Workbench server binds on 127.0.0.1. 0 picks a free one."""
    try:
        return int(_workbench().get("port", 8765))
    except (TypeError, ValueError):
        return 8765


def workbench_email_mode() -> str:
    """`send` puts an approved email in the outbound queue; `draft` only ever
    creates a provider-side draft. Anything unrecognized falls back to `draft`,
    because the safe default for a send path is not sending."""
    mode = str(_workbench().get("email_mode", "send")).strip().lower()
    return mode if mode in ("send", "draft") else "draft"


def workbench_logo_fetch() -> bool:
    """Whether the Inbox may fetch sender favicons from Google's favicon service.

    On means one outbound request per counterparty domain, which discloses that
    domain to Google. Off keeps the cache-only path and letter avatars.
    """
    return bool(_workbench().get("logo_fetch", True))


def workbench_open_on_run() -> bool:
    """Whether a scheduled briefing run opens its page in the browser.

    ON by default. The briefing page is the product: a digest that arrives
    with nothing to look at is a notification, and the reader has to go
    find the page themselves. So each of the four briefings puts its own
    page up when it finishes.

    What keeps that from being a nuisance is everything around it. It only
    ever opens into a Workbench the user already has running, never starts
    one, and each briefing opens at most once every six hours, so a rerun
    reopens nothing. Set `workbench.open_on_run` to false to stop it.
    """
    return bool(_workbench().get("open_on_run", True))


def workbench_chat_model() -> str:
    """Model for the page's chat panel. Empty means the CLI's own default."""
    return str(_workbench().get("chat_model", "") or "").strip()


# ── Voice classifier context ───────────────────────────────────────────────────

def voice_classifier_context() -> str:
    """A short business-context sentence injected into the voice classifier
    prompt so audience categories map to the user's actual verticals. Built
    from role_description + business display names; never hardcoded."""
    role = user_role_description()
    names = [b["display_name"] for b in businesses()]
    parts = []
    if role:
        parts.append(role)
    if names:
        parts.append("Ventures: " + ", ".join(names))
    return ". ".join(parts)


# ── van-gogh/ state paths (briefings, logs, projects) ─────────────────────────

def repo_root() -> Path:
    """The plugin root (parent of this file's parent) — the plugin cache for
    installed users, the repo checkout in development.

    Holds source only. Per-user state lives in ~/.config/van-gogh/ (see
    app/user_state.py) and in {vault}/van-gogh/.
    """
    return _REPO_ROOT


def workspace_week_md_path() -> Path:
    """Where the rendered week.md lives. Drives --since auto resolution."""
    return van_gogh_root() / "week.md"


def morning_coffee_md_path() -> Path:
    """The rendered daily briefing (read by /afternoon-tea)."""
    return van_gogh_root() / "morning-coffee.md"


def afternoon_tea_md_path() -> Path:
    """The rendered end-of-day retro."""
    return van_gogh_root() / "afternoon-tea.md"


def logs_dir() -> Path:
    """logs/ inside van-gogh/. Created on demand by callers."""
    return van_gogh_root() / "logs"


# ── Travel ────────────────────────────────────────────────────────────────────
# Every accessor defaults, because live configs predate this block entirely.
# The home address is deliberately NOT here: it lives in the private per-user
# .env beside the refresh tokens, because this config is synced into the vault
# and rendered onto a published page.

def _travel() -> dict:
    block = cfg().get("travel")
    return block if isinstance(block, dict) else {}


def travel_enabled() -> bool:
    """Whether travel notices run at all (default on).

    On by default because the two notices that need no setup, the flights
    link and the destination forecast, read only the calendar. Drive time and
    the leave-by time stay dark until a home address exists.
    """
    return bool(_travel().get("enabled", True))


def travel_buffer_minutes() -> int:
    """How early to be at the airport, in minutes (default 90)."""
    try:
        return int(_travel().get("airport_buffer_minutes", 90))
    except (TypeError, ValueError):
        return 90


def travel_weather_lead_days() -> int:
    """How many days before a trip the forecast shows (default 2)."""
    try:
        return int(_travel().get("weather_lead_days", 2))
    except (TypeError, ValueError):
        return 2


def projects_dir() -> Path:
    """The workspace-memory projects/ folder inside van-gogh/."""
    return van_gogh_root() / "projects"


def vangogh_memory_path() -> Path:
    """memory.md session-init context, read by /ingest-workspace."""
    return projects_dir() / "Project Van Gogh" / "memory.md"


def ingest_state_path() -> Path:
    """State file tracking the last /ingest-workspace sync."""
    return van_gogh_root() / ".ingest_state.json"


def full_sidecar_path() -> Path:
    """Where week_review.py drops the full JSON (with filtered_pending bodies)."""
    return logs_dir() / "week_review_latest.full.json"


def done_log_path(year: int | None = None) -> Path:
    """`done-{YYYY}.md` in the configured weekly_dir. Defaults to current year."""
    from datetime import datetime
    y = year if year is not None else datetime.now().year
    return weekly_dir() / f"done-{y}.md"


# ── Resolved meta for skill consumers ─────────────────────────────────────────

def resolved_meta() -> dict:
    """Return a dict of resolved, ready-to-use values for skill consumers.

    Skills receive this as the `meta` block in script JSON output, so they
    never have to read config.json themselves or substitute placeholders.
    """
    return {
        "vault_path": str(vault()),
        "van_gogh_root": str(van_gogh_root()),
        "projects_dir": str(projects_dir()),
        "vangogh_memory_path": str(vangogh_memory_path()),
        "ingest_state_path": str(ingest_state_path()),
        "hotcache_path": str(hotcache_path()),
        "sources_dir": str(sources_dir()),
        "weekly_dir": str(weekly_dir()),
        "done_log_path": str(done_log_path()),
        "workspace_week_md": str(workspace_week_md_path()),
        "morning_coffee_md": str(morning_coffee_md_path()),
        "afternoon_tea_md": str(afternoon_tea_md_path()),
        "entities_dir": str(entities_dir()),
        "voice_guide_path": str(voice_guide_path()),
        "tone_profile_path": str(tone_profile_path()),
        "voice_snapshot_path": str(voice_snapshot_path()),
        "voice_drafts_path": str(voice_drafts_path()),
        "relationship_radar_path": str(relationship_radar_path()),
        "logs_dir": str(logs_dir()),
        "action_items_heading": hotcache_action_items_heading(),
        "active_threads_heading": hotcache_active_threads_heading(),
        "user_full_name": user_name(),
        "user_first_name": user_first_name(),
        "accounts": [
            {
                "label": a["label"],
                "provider": a["provider"],
                "email": a["email"],
                "is_primary": a["is_primary"],
            }
            for a in accounts()
        ],
        "businesses": [
            {
                "tag": b["tag"],
                "display_name": b["display_name"],
                "project_page": str(vault(b["project_page"])),
                "meeting_route": b["meeting_route"],
                "keywords": list(b.get("keywords", [])),
                "priorities": list(b.get("priorities", [])),
            }
            for b in businesses()
        ],
        "clients": [_resolved_client(c) for c in clients()],
        "support": {
            "team_email": support_team_email(),
            "alerts_enabled": alerts_enabled(),
            "alert_cooldown_days": alert_cooldown_days(),
            "job_watch_enabled": job_watch_enabled(),
            "job_watch_kick_cap": job_watch_kick_cap(),
            "repair_agent_enabled": repair_agent_enabled(),
            "task_stall_enabled": task_stall_enabled(),
        },
        "memory": {
            "enabled": memory_sync_enabled(),
            "target_dir": str(memory_target_dir()),
        },
        # "" unless a newer plugin is published; skills render it verbatim as a
        # single line and never block on it (see plugin_update.cached_notice).
        "update_notice": plugin_update.cached_notice(),
        # "" unless the plugin updated recently; the release notes the user has
        # not seen yet, in plain language, shown once in the first chat
        # session after the update and never in unattended runs
        # (see plugin_update.whats_new and CHANGELOG.md).
        "whats_new": plugin_update.whats_new(),
        # "" on a normal day. Carries one plain line when a scheduled job had
        # to be re-run, is waiting on something, or when the watcher itself
        # has gone quiet (see job_watch.summary). Imported here rather than at
        # module scope because job_watch imports this module.
        "job_watch": _job_watch_line(),
        # "" on a normal day. One line when work has stopped moving: a
        # commitment weeks past its date, a draft nobody read. Nothing
        # re-runs those, so unlike job_watch it is never actioned by the
        # machine, only told to the person (see task_stall.summary).
        "task_stall": _task_stall_line(),
    }


def _task_stall_line() -> str:
    """One line about work that stopped moving, or "". Never raises.

    Same shape and same reasoning as _job_watch_line: imported here rather
    than at module scope because task_stall imports this module, and failing
    open because a footer must never be able to stop a briefing rendering.
    """
    try:
        if not task_stall_enabled():
            return ""
        import task_stall
        return task_stall.summary()
    except Exception:                                           # noqa: BLE001
        return ""


def _job_watch_line() -> str:
    """The watcher's one line for the briefing footer, or "".

    Fails open to silence: a watcher that cannot report is not a reason for a
    briefing not to render."""
    try:
        import job_watch
        return job_watch.summary().get("line", "")
    except Exception:                                            # noqa: BLE001
        return ""


# Last, not with the other import-time hooks at the top: this one reads
# logs_dir(), which is defined in this module, so it can only run once the
# module body is complete. Same managed-venv gate as its siblings, and it
# fails open (see user_state.record_skill_use).
user_state.record_skill_use()
