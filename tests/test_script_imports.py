"""Smoke-test that the OAuth-migrated scripts import cleanly under the fixture.

Before the migration, afternoon_tea/meeting_prep/cos_voice_calibration/
calendar_stub_check read a required secondary-account CLI config-dir key at
module load, so they could not be imported with the CI fixture (which no longer
carries that key). After migration they resolve accounts by label alone, so a
bare import must succeed. A failure here flags a leftover hard config dependency
or a broken reference introduced while rewiring to the clients.

conftest.py puts app/ on sys.path and primes config_loader before collection.
"""
import importlib

import pytest

_MIGRATED_MODULES = [
    "afternoon_tea",
    "meeting_prep",
    "cos_voice_calibration",
    "calendar_stub_check",
    "week_retro",
    "scheduler_setup",
    "five_fifteen",
    "follow_up_radar",
    "podcast_transcribe",
    "migrate_from_legacy",
    "workbench_data",
    "workbench_store",
    "workbench_serve",
    "brain_graph",
    "memory_sync",
    "self_anneal",
    "skill_run",
    "draft_email",
    "priority_judge",
    "install_oauth_credentials",
    "notetaker",
    "notetaker_granola",
    "notetaker_grain",
    "meeting_fetch",
    "meeting_ingest",
    "run_ledger",
    "audit_evidence",
    "audit_ledger",
    "interview_capture",
    "note_watch",
    "note_send",
]


@pytest.mark.parametrize("module_name", _MIGRATED_MODULES)
def test_migrated_script_imports(module_name):
    mod = importlib.import_module(module_name)
    assert mod is not None


def test_clients_are_wired_in():
    """Each migrated script binds the direct-OAuth client factories."""
    import afternoon_tea
    import calendar_stub_check
    import cos_voice_calibration
    import meeting_prep
    import week_retro

    for mod in (afternoon_tea, meeting_prep, cos_voice_calibration,
                calendar_stub_check, week_retro):
        assert hasattr(mod, "google_client"), f"{mod.__name__} missing google_client"
        assert hasattr(mod, "microsoft_client"), f"{mod.__name__} missing microsoft_client"
