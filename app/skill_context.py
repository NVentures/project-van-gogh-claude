#!/usr/bin/env python3
"""
skill_context.py — prints resolved config values as JSON.

Use this from any skill that needs vault paths, user name, account labels,
or business metadata without running a full data pull. Skills should always
prefer this over reading config.json directly.

Usage:
    python app/skill_context.py
"""

import json

from config_loader import resolved_meta


def main():
    print(json.dumps(resolved_meta(), indent=2))


if __name__ == "__main__":
    main()
