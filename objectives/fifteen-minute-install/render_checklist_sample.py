#!/usr/bin/env python3
"""Render the pre-call checklist for an invented client, for the cold read (P20).

The cold reader gets two things a real client holds: this page and the
installer's own text (`cold_read_plant.py --render`). The checklist template
and its renderer live at the internal repo root (`templates/`, `tools/`), so
this only works in the internal checkout, never in the public mirror.

Usage: render_checklist_sample.py [--microsoft] > checklist.md
The sample client is Jane Doe of Acme Energy with one Google work account
(add --microsoft for a Microsoft work account as well). Every value is
invented; the two client ids are made-up identifiers of the right shape.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]


def main(argv: list | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    tool = REPO_ROOT / "tools" / "precall.py"
    if not tool.exists():
        print("tools/precall.py not found: run this from the internal checkout", file=sys.stderr)
        return 2
    sys.path.insert(0, str(tool.parent))
    spec = importlib.util.spec_from_file_location("precall", tool)
    precall = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(precall)
    accounts = [{"provider": "google", "email_hint": "jane@acme-energy.example"}]
    if "--microsoft" in argv:
        accounts.append({"provider": "microsoft", "email_hint": "jane@acme-energy.example"})
    details = {
        "full_name": "Jane Doe", "first_name": "Jane", "company": "Acme Energy",
        "timezone": "America/New_York", "notetaker": "granola", "accounts": accounts,
        "support_email": "team@example.com",
        "key_file_name": "van-gogh-key-jane-doe.json",
    }
    credentials = {
        "google_client_secret": {"installed": {
            "client_id": "482915530771-k3v9q2m7xw.apps.googleusercontent.com"}},
        "ms_client_secret": {"client_id": "7c1f4b2e-93a5-4d18-b6e0-2f9a5c8d1e47"},
    }
    sys.stdout.write(precall.render(details, credentials))
    return 0


if __name__ == "__main__":
    sys.exit(main())
