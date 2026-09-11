"""Backward-compatibility guard: an older or partial config.json must still
resolve against the current code.

When a feature adds a new config field, an existing install (e.g. a collaborator
who set up weeks ago and has not re-run /install) has a config.json without that
field. A plugin update never rewrites their config (it is gitignored, in their
vault), so the only thing that keeps the updated scripts working against the old
config is a safe default on every accessor. This test enforces that: it primes
config_loader with a MINIMAL config holding only the always-required fields, then
calls every public accessor and asserts none raise. A new accessor that hard-reads
a new field (no default) turns this red, before it can break a real install.
"""
import tempfile

import pytest

import config_loader as cl

# Only the fields that have been required since the earliest installs and have no
# default in config_loader. Everything a later version added (businesses,
# email_filters, relationship_radar, voice/entity relpaths, active-threads
# heading, timezone, role_description, name_variants, ...) is deliberately OMITTED
# so this stands in for a collaborator's older config.
MINIMAL = {
    "user": {"full_name": "Old User", "first_name": "Old"},
    "obsidian": {
        "vault_path": tempfile.mkdtemp(prefix="van-gogh-test-oldvault-"),
        "hotcache_relpath": "wiki/hotcache.md",
        "sources_relpath": "wiki/sources",
        "weekly_relpath": "wiki/weekly",
        "hotcache_action_items_heading": "Action Items",
    },
    "accounts": [
        {"provider": "google", "email": "old@gmail.com", "label": "Gmail",
         "is_primary": True},
    ],
}

# Every public, zero-argument accessor. resolved_meta() exercises most of the
# path/identity surface; the rest cover filters, radar, voice, and accounts.
ACCESSORS = [
    "vault", "van_gogh_root", "hotcache_path", "sources_dir", "weekly_dir",
    "entities_dir", "hotcache_action_items_heading", "hotcache_active_threads_heading",
    "voice_guide_path", "tone_profile_path", "voice_snapshot_path",
    "voice_drafts_path", "relationship_radar_path",
    "user_name", "user_first_name", "user_role_description", "user_bio_descriptor",
    "user_name_variants", "user_self_entities", "user_tz", "user_secondary_tz",
    "user_name_regex",
    "accounts", "google_accounts", "microsoft_accounts", "primary_account",
    "account_labels", "account_emails", "account_emails_lower",
    "businesses", "business_tags", "project_pages", "meeting_routes",
    "clients", "client_tags",
    "follow_ups_horizon_days", "follow_ups_memory_path", "podcast_feeds",
    "allow_domains", "internal_domains", "internal_team_emails",
    "internal_team_names", "extra_spam_fragments", "deal_critical_domains",
    "radar_yellow_days", "radar_red_days", "radar_yellow_draft_cap",
    "radar_skip_tags", "radar_skip_entity_names",
    "voice_classifier_context",
    "travel_enabled", "travel_buffer_minutes", "travel_weather_lead_days",
    "workbench_port", "workbench_email_mode", "workbench_logo_fetch",
    "support_team_email", "alerts_enabled", "alert_cooldown_days",
    "memory_sync_enabled", "memory_source_dirs", "memory_target_dir",
    "judgment_enabled", "judgment_model",
    "repo_root", "workspace_week_md_path", "morning_coffee_md_path",
    "afternoon_tea_md_path", "logs_dir", "projects_dir", "vangogh_memory_path",
    "ingest_state_path", "full_sidecar_path", "done_log_path", "resolved_meta",
]


@pytest.fixture
def minimal_config():
    """Swap in the MINIMAL config for the duration of one test, then restore the
    full conftest fixture so the rest of the suite is unaffected."""
    saved = cl._config
    cl._config = MINIMAL
    try:
        yield
    finally:
        cl._config = saved


@pytest.mark.parametrize("name", ACCESSORS)
def test_accessor_resolves_on_minimal_config(name, minimal_config):
    getattr(cl, name)()  # must not raise: every newer field needs a safe default


def test_business_for_text_on_minimal_config(minimal_config):
    # No businesses configured -> no match, not a KeyError.
    assert cl.business_for_text("anything at all") is None


def test_business_tag_for_text_on_minimal_config(minimal_config):
    assert cl.business_tag_for_text("anything at all") is None


def test_business_priorities_on_minimal_config(minimal_config):
    # An install with no buckets asks for a bucket's priorities and gets an
    # empty list, never a KeyError.
    assert cl.business_priorities("anything") == []
