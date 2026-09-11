"""Every config_loader accessor returns the primed FIXTURE values.

conftest primes config_loader._config = FIXTURE at collection time, so these
run with NO config.json present.
"""
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import config_loader as cl
from conftest import FIXTURE

# The vault root comes from the fixture, never a literal: these assert how
# the loader composes a path, and the fixture root is a per-session mkdtemp.
VAULT = Path(FIXTURE["obsidian"]["vault_path"])


def test_vault_and_paths():
    assert cl.vault() == VAULT
    assert cl.vault("wiki/x.md") == VAULT / "wiki/x.md"
    assert cl.hotcache_path() == VAULT / "wiki/hotcache.md"
    assert cl.entities_dir() == VAULT / "wiki/entities"
    assert cl.voice_guide_path() == VAULT / "wiki/sources/voice-guide.md"


def test_user_identity():
    assert cl.user_first_name() == "Test"
    assert cl.user_name() == "Test User"
    assert cl.user_name_variants() == ["Test User", "Test", "tuser"]
    assert cl.user_self_entities() == {"Test User", "Test", "me"}


def test_user_bio_descriptor():
    # role_description is set, so bio = "Name, role"
    assert cl.user_bio_descriptor() == "Test User, Founder of Acme"


def test_accounts():
    accts = cl.accounts()
    assert isinstance(accts, list)
    assert [a["email"] for a in accts] == [
        "primary@gmail.com", "sec@work.com", "user@company.com"
    ]
    # Exactly one primary.
    assert sum(1 for a in accts if a["is_primary"]) == 1
    assert cl.primary_account()["email"] == "primary@gmail.com"


def test_account_accessors():
    assert [a["email"] for a in cl.google_accounts()] == [
        "primary@gmail.com", "sec@work.com"
    ]
    assert [a["email"] for a in cl.microsoft_accounts()] == ["user@company.com"]
    assert cl.account_labels() == ["Gmail", "Work", "Outlook"]
    assert cl.account_emails() == [
        "primary@gmail.com", "sec@work.com", "user@company.com"
    ]
    assert cl.account_emails_lower() == {
        "primary@gmail.com", "sec@work.com", "user@company.com"
    }
    # Microsoft account carries its sent_folder_id; Google accounts don't.
    assert cl.microsoft_accounts()[0]["sent_folder_id"] == "FOLDERID"


def test_legacy_flat_config_migrates_on_read():
    """A pre-N-account flat-dict config is normalized to the list shape."""
    legacy = {
        "google_primary_email": "p@gmail.com", "google_primary_label": "Gmail",
        "google_secondary_email": "s@work.com", "google_secondary_label": "Work",
        "microsoft_email": "u@co.com", "microsoft_label": "Outlook",
        "outlook_sent_folder_id": "FID",
    }
    out = cl._normalize_accounts(legacy)
    assert [a["email"] for a in out] == ["p@gmail.com", "s@work.com", "u@co.com"]
    assert out[0]["is_primary"] is True
    assert sum(1 for a in out if a["is_primary"]) == 1
    ms = [a for a in out if a["provider"] == "microsoft"][0]
    assert ms["sent_folder_id"] == "FID"


def test_legacy_single_google_account_migrates():
    """Single-Google-account install: no secondary, no microsoft — one primary."""
    legacy = {
        "google_primary_email": "only@gmail.com", "google_primary_label": "Gmail",
        "google_secondary_email": "", "microsoft_email": "",
    }
    out = cl._normalize_accounts(legacy)
    assert [a["email"] for a in out] == ["only@gmail.com"]
    assert out[0]["is_primary"] is True


def test_empty_email_accounts_are_dropped():
    """List entries with no/empty email never become accounts."""
    out = cl._normalize_accounts([
        {"provider": "google", "email": "real@x.com", "label": "Real"},
        {"provider": "google", "email": "", "label": "Empty"},
        {"provider": "microsoft", "label": "NoEmailKey"},
    ])
    assert [a["email"] for a in out] == ["real@x.com"]


def test_normalize_does_not_mutate_source():
    """_normalize_accounts copies list entries — it must not write is_primary
    (or any normalized field) back into the caller's config dict."""
    source = [
        {"provider": "google", "email": "a@x.com", "label": "A"},  # no is_primary key
        {"provider": "microsoft", "email": "b@x.com", "label": "B"},
    ]
    out = cl._normalize_accounts(source)
    assert out[0]["is_primary"] is True
    # Source entries are untouched (no is_primary leaked back in).
    assert "is_primary" not in source[0]
    assert "is_primary" not in source[1]


def test_legacy_empty_primary_promotes_next_account():
    """Legacy flat config with a blank google_primary but a microsoft account:
    the microsoft account is the only one configured, so it becomes primary."""
    out = cl._normalize_accounts({
        "google_primary_email": "", "microsoft_email": "u@co.com",
        "microsoft_label": "Outlook", "outlook_sent_folder_id": "FID",
    })
    assert [a["email"] for a in out] == ["u@co.com"]
    assert out[0]["provider"] == "microsoft"
    assert out[0]["is_primary"] is True


def test_primary_account_none_when_no_accounts():
    """primary_account() returns None (never index-errors) on an empty config."""
    saved = cl._config
    try:
        cl._config = {**saved, "accounts": []}
        assert cl.accounts() == []
        assert cl.primary_account() is None
        assert cl.account_labels() == []
        assert cl.account_emails_lower() == set()
    finally:
        cl._config = saved


def test_exactly_one_primary_enforced():
    """If config marks zero or multiple primaries, exactly one survives."""
    none_marked = cl._normalize_accounts([
        {"provider": "google", "email": "a@x.com", "label": "A"},
        {"provider": "google", "email": "b@x.com", "label": "B"},
    ])
    assert [a["is_primary"] for a in none_marked] == [True, False]

    many_marked = cl._normalize_accounts([
        {"provider": "google", "email": "a@x.com", "label": "A", "is_primary": True},
        {"provider": "microsoft", "email": "b@x.com", "label": "B", "is_primary": True},
    ])
    assert [a["is_primary"] for a in many_marked] == [True, False]


def test_businesses_and_derived():
    assert cl.businesses() == FIXTURE["businesses"]
    assert cl.business_tags() == {"ACME", "BETA"}
    assert cl.project_pages() == {
        "Acme Corp": VAULT / "wiki/projects/Acme.md",
        "Beta Labs": VAULT / "wiki/projects/Beta.md",
    }
    assert cl.meeting_routes() == {
        "ACME": "10-Acme/Meetings",
        "BETA": "10-Beta/Meetings",
    }


def test_two_distinct_projects_concepts():
    """There are two unrelated `projects` dirs — don't conflate them.

    - project_pages() → the Obsidian *project pages* under wiki/projects/
      (one note per venture, linked from briefings).
    - projects_dir() → the Van Gogh *workspace memory* under van-gogh/projects/
      (Project Van Gogh/memory.md, read by /ingest-workspace).
    """
    assert cl.project_pages()["Acme Corp"] == VAULT / "wiki/projects/Acme.md"
    assert cl.projects_dir() == VAULT / "van-gogh/projects"
    assert cl.vangogh_memory_path() == (
        VAULT / "van-gogh/projects/Project Van Gogh/memory.md")


def test_email_filter_accessors():
    assert cl.internal_team_emails() == {"teammate@company.com"}
    assert cl.internal_team_names() == {"Jane Doe"}
    assert cl.deal_critical_domains() == {"counsel.com"}


def test_radar_accessors():
    assert cl.radar_yellow_days() == 30
    assert cl.radar_red_days() == 60
    assert cl.radar_skip_tags() == {"company", "org", "stub"}
    assert cl.radar_skip_entity_names() == {"Acme Corp", "BigCo"}


def test_voice_classifier_context():
    ctx = cl.voice_classifier_context()
    assert "Founder of Acme" in ctx
    assert "Acme Corp" in ctx
    assert "Beta Labs" in ctx


def test_business_for_text():
    assert cl.business_for_text("we love widgets") == "Acme Corp"
    assert cl.business_for_text("the acme deal") == "Acme Corp"
    assert cl.business_for_text("a shiny new gadget") == "Beta Labs"
    assert cl.business_for_text("nothing matches here") is None
    assert cl.business_for_text("") is None


def test_user_name_regex_word_boundary():
    rx = cl.user_name_regex()
    assert rx.search("Test owns this")  # word "Test"
    assert rx.search("met with Test User today")  # word "Test User"
    # Substring inside a larger word must NOT match.
    assert rx.search("Testing the system") is None
    assert rx.search("contested decision") is None


def test_resolved_meta_has_all_keys():
    meta = cl.resolved_meta()
    expected = {
        "vault_path", "van_gogh_root", "projects_dir", "vangogh_memory_path",
        "ingest_state_path", "hotcache_path", "sources_dir", "weekly_dir",
        "done_log_path", "workspace_week_md", "morning_coffee_md",
        "afternoon_tea_md", "entities_dir", "voice_guide_path",
        "tone_profile_path", "voice_snapshot_path", "voice_drafts_path",
        "relationship_radar_path", "logs_dir", "action_items_heading",
        "active_threads_heading", "user_full_name", "user_first_name",
        "accounts", "businesses",
    }
    assert expected.issubset(set(meta.keys()))
    # Spot-check a couple resolved values.
    assert meta["vault_path"] == str(VAULT)
    assert meta["action_items_heading"] == "Test's Action Items"
    assert meta["active_threads_heading"] == "Active Threads"
    assert meta["user_full_name"] == "Test User"
    # accounts is now a list of {label, provider, email, is_primary}.
    assert isinstance(meta["accounts"], list)
    assert [a["label"] for a in meta["accounts"]] == ["Gmail", "Work", "Outlook"]
    assert sum(1 for a in meta["accounts"] if a["is_primary"]) == 1
    assert {b["tag"] for b in meta["businesses"]} == {"ACME", "BETA"}


# ── Timezone accessors (Item 10: timezone follows the user) ───────────────────

def test_user_tz_falls_back_when_unset():
    """The FIXTURE has no user.timezone, so user_tz() returns the default zone,
    letting installs predating the timezone field keep working."""
    assert "timezone" not in FIXTURE["user"]
    assert cl.user_tz() == ZoneInfo("America/Los_Angeles")


def test_user_tz_returns_configured_zone():
    saved = cl._config
    try:
        cl._config = {**saved, "user": {**saved["user"], "timezone": "America/New_York"}}
        assert cl.user_tz() == ZoneInfo("America/New_York")
    finally:
        cl._config = saved


def test_user_tz_falls_back_on_bad_zone():
    """An unrecognized IANA name degrades to the default, never raises."""
    saved = cl._config
    try:
        cl._config = {**saved, "user": {**saved["user"], "timezone": "Not/AZone"}}
        assert cl.user_tz() == ZoneInfo("America/Los_Angeles")
    finally:
        cl._config = saved


def test_user_secondary_tz_none_when_unset():
    """No secondary_timezone configured (the default for new installs) → None,
    so briefings render a single zone."""
    assert cl.user_secondary_tz() is None


def test_user_secondary_tz_returns_zone_when_set():
    saved = cl._config
    try:
        cl._config = {
            **saved,
            "user": {**saved["user"], "secondary_timezone": "America/New_York"},
        }
        assert cl.user_secondary_tz() == ZoneInfo("America/New_York")
    finally:
        cl._config = saved


def test_user_secondary_tz_none_on_bad_zone():
    saved = cl._config
    try:
        cl._config = {
            **saved,
            "user": {**saved["user"], "secondary_timezone": "Not/AZone"},
        }
        assert cl.user_secondary_tz() is None
    finally:
        cl._config = saved


def test_date_window_computes_against_configured_zone():
    """A date-relative window ("today") computed via user_tz() lands on the
    correct local calendar day, not UTC's. At 04:00 UTC it is still the prior
    day in US zones, so the window must follow the configured zone."""
    # 2026-05-29 04:00 UTC == 2026-05-28 (21:00) Pacific, 2026-05-29 in London.
    instant = datetime(2026, 5, 29, 4, 0, tzinfo=timezone.utc)
    saved = cl._config
    try:
        cl._config = {**saved, "user": {**saved["user"], "timezone": "America/Los_Angeles"}}
        assert instant.astimezone(cl.user_tz()).date().isoformat() == "2026-05-28"
        cl._config = {**saved, "user": {**saved["user"], "timezone": "Europe/London"}}
        assert instant.astimezone(cl.user_tz()).date().isoformat() == "2026-05-29"
    finally:
        cl._config = saved


# ── Email digest accessors ─────────────────────────────────────────────────────

def test_digest_defaults_when_block_absent():
    saved = cl._config
    try:
        cl._config = {k: v for k, v in saved.items() if k != "digest"}
        assert cl.digest_enabled() is False
        briefings = cl.digest_briefings()
        assert set(briefings) == {"morning-coffee", "afternoon-tea", "week", "week-retro"}
        assert briefings["morning-coffee"]["time"] == "07:00"
        assert briefings["afternoon-tea"]["time"] == "13:00"
        assert briefings["week"]["days"] == ["monday"]
        assert briefings["week-retro"]["days"] == ["friday"]
        # sender/recipient fall back to the primary account
        assert cl.digest_sender_account()["email"] == "primary@gmail.com"
        assert cl.digest_recipient_email() == "primary@gmail.com"
    finally:
        cl._config = saved


def test_digest_overrides_merge_over_defaults():
    saved = cl._config
    try:
        cl._config = {
            **saved,
            "digest": {
                "enabled": True,
                "sender_label": "outlook",  # case-insensitive label match
                "recipient_email": "someone@else.com",
                "briefings": {
                    "afternoon-tea": {"enabled": False, "time": "15:30"},
                },
            },
        }
        assert cl.digest_enabled() is True
        assert cl.digest_sender_account()["email"] == "user@company.com"
        assert cl.digest_recipient_email() == "someone@else.com"
        briefings = cl.digest_briefings()
        assert briefings["afternoon-tea"]["enabled"] is False
        assert briefings["afternoon-tea"]["time"] == "15:30"
        # untouched keys keep their defaults
        assert briefings["afternoon-tea"]["days"] == [
            "monday", "tuesday", "wednesday", "thursday", "friday"]
        assert briefings["morning-coffee"]["enabled"] is True
    finally:
        cl._config = saved


def test_digest_unknown_sender_label_falls_back_to_primary():
    saved = cl._config
    try:
        cl._config = {**saved, "digest": {"enabled": True, "sender_label": "nope"}}
        assert cl.digest_sender_account()["email"] == "primary@gmail.com"
    finally:
        cl._config = saved
