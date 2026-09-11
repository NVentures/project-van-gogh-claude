"""Test infrastructure for Project Van Gogh.

Runs at collection time (pytest imports conftest before collecting/importing
test modules). Two jobs, both at module scope so they happen BEFORE any test
module imports an app/ script:

1. Put the repo's app/ directory on sys.path so `import config_loader`,
   `import week_review`, etc. resolve the same way they do at runtime
   (where scripts are run as `python app/X.py` and app/ is sys.path[0]).
2. Prime config_loader._config with a complete fixture so every accessor is
   deterministic and the suite passes with NO config.json present. Many scripts
   call accessors (accounts(), vault(), business_tags(), ...) at MODULE LOAD
   time, so this priming must run before those scripts are imported.
"""

import atexit
import os
import shutil
import sys
import tempfile
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
_APP_DIR = _REPO_ROOT / "app"

# 1. app/ on sys.path (bare imports like `import config_loader` work).
if str(_APP_DIR) not in sys.path:
    sys.path.insert(0, str(_APP_DIR))

# Point the per-user state dir (user_state.py: .env, vault-pointer, venv) at a
# throwaway temp dir so the suite never reads or writes the developer's real
# ~/.config/van-gogh/ — and stays CI-safe with no state present at all.
os.environ["VAN_GOGH_STATE_DIR"] = tempfile.mkdtemp(prefix="van-gogh-test-state-")

# The fixture vault gets the same treatment, and for the same reason. It was a
# hardcoded "/tmp/vault": one fixed path, in a world-writable directory, shared
# by every run on the machine. Tests that walk the vault (context_pack reads
# entities_dir() and iterates sources_dir()) therefore read whatever a previous
# run, a parallel run, or another user left there, which is how a green suite
# fails once and then passes twelve times. mkdtemp gives each session its own.
_FIXTURE_VAULT = tempfile.mkdtemp(prefix="van-gogh-test-vault-")

# Both temp trees go at interpreter exit. Without this every run leaves one
# behind: ten had already accumulated on this machine before anyone looked.
atexit.register(lambda: shutil.rmtree(_FIXTURE_VAULT, ignore_errors=True))
atexit.register(lambda: shutil.rmtree(os.environ["VAN_GOGH_STATE_DIR"],
                                     ignore_errors=True))

# Complete, generic, non-personal config covering the whole schema:
# secondary account, two businesses, and filter entries for routing/dedup tests.
FIXTURE = {
    "user": {
        "full_name": "Test User",
        "first_name": "Test",
        "name_variants": ["Test User", "Test", "tuser"],
        "self_entities": ["Test User", "Test", "me"],
        "role_description": "Founder of Acme",
    },
    "accounts": [
        {"provider": "google", "email": "primary@gmail.com", "label": "Gmail",
         "is_primary": True, "sent_folder_id": ""},
        {"provider": "google", "email": "sec@work.com", "label": "Work",
         "is_primary": False, "sent_folder_id": ""},
        {"provider": "microsoft", "email": "user@company.com", "label": "Outlook",
         "is_primary": False, "sent_folder_id": "FOLDERID"},
    ],
    "obsidian": {
        "vault_path": _FIXTURE_VAULT,
        "hotcache_relpath": "wiki/hotcache.md",
        "sources_relpath": "wiki/sources",
        "weekly_relpath": "wiki/weekly",
        "entities_relpath": "wiki/entities",
        "hotcache_action_items_heading": "Test's Action Items",
        "hotcache_active_threads_heading": "Active Threads",
        "voice_guide_relpath": "wiki/sources/voice-guide.md",
        "tone_profile_relpath": "wiki/sources/tone-profile.md",
        "voice_snapshot_relpath": "wiki/sources/Voice Snapshot.md",
        "voice_drafts_relpath": "wiki/sources/voice-drafts.md",
        "relationship_radar_relpath": "wiki/sources/relationship-radar.md",
    },
    "businesses": [
        {
            "tag": "ACME",
            "display_name": "Acme Corp",
            "project_page": "wiki/projects/Acme.md",
            "meeting_route": "10-Acme/Meetings",
            "keywords": ["acme", "widget"],
            "priorities": [
                {"name": "Close the widget deal", "detail": "", "added": "2026-08-01"},
                {"name": "Ship the gadget line", "detail": "", "added": "2026-08-01"},
            ],
        },
        {
            "tag": "BETA",
            "display_name": "Beta Labs",
            "project_page": "wiki/projects/Beta.md",
            "meeting_route": "10-Beta/Meetings",
            "keywords": ["beta", "gadget"],
        },
    ],
    "email_filters": {
        "internal_domains": ["company.com"],
        "internal_team_emails": ["teammate@company.com"],
        "internal_team_names": ["Jane Doe"],
        "allow_domains": [],
        "extra_spam_fragments": ["promo-xyz"],
        "deal_critical_domains": ["counsel.com"],
    },
    "digest": {
        "enabled": False,
        "sender_label": "",
        "recipient_email": "",
        "briefings": {},
    },
    "relationship_radar": {
        "yellow_days": 30,
        "red_days": 60,
        "yellow_draft_cap": 5,
        "skip_tags": ["company", "org", "stub"],
        "skip_entity_names": ["Acme Corp", "BigCo"],
    },
    "support": {
        "team_email": "support@example.com",
        "alerts_enabled": False,
        "alert_cooldown_days": 7,
    },
    # OFF in the fixture on purpose: with it on, any test that drives the gate
    # would shell out to a real model. Tests that want the layer turn it on
    # and inject a stub.
    "judgment": {"enabled": False, "model": ""},
    "memory_sync": {
        "enabled": False,
        "claude_project_dirs": [],
        "target_relpath": "wiki/memory",
    },
}

# 2. Prime the cache BEFORE any test module imports an app/ script.
import config_loader  # noqa: E402

config_loader._config = FIXTURE
