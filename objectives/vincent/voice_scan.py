#!/usr/bin/env python3
"""The repo's voice check, pointed at Vincent's two rule files.

The enforced list lives in `objectives/ship-ready-review/voice_check.py`
(DESIGN.md, "Written Voice", names that file as its home). That script scans
rendered briefings and cannot be aimed at a file, so this one reads the same
list out of it and scans the files named on the command line. The list is
read from that file every run, never copied here, so the two cannot drift.

    voice_scan.py                 scans agents/vincent.md and skills/vincent/SKILL.md
    voice_scan.py --selftest      proves the scan sees a planted phrase first
    voice_scan.py <file> ...

Exit 0 PASS, 1 FAIL (hits listed), 2 SKIP (nothing was scanned).
"""

from __future__ import annotations

import ast
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve()
ROOT = HERE.parents[2]
SOURCE = ROOT / "objectives" / "ship-ready-review" / "voice_check.py"
DEFAULT = [ROOT / "agents" / "vincent.md", ROOT / "skills" / "vincent" / "SKILL.md"]


def ai_tells() -> list:
    for node in ast.parse(SOURCE.read_text(encoding="utf-8")).body:
        if isinstance(node, ast.Assign) and getattr(node.targets[0], "id", "") == "AI_TELLS":
            return [str(v) for v in ast.literal_eval(node.value)]
    raise SystemExit(f"{SOURCE} no longer defines AI_TELLS")


def scan(paths: list, tells: list) -> tuple:
    hits, lines = [], 0
    for path in paths:
        try:
            text = Path(path).read_text(encoding="utf-8")
        except OSError as exc:
            raise SystemExit(f"cannot read {path}: {exc}")
        for i, line in enumerate(text.splitlines(), start=1):
            lines += 1
            low = line.lower()
            for tell in tells:
                if tell.lower() in low:
                    hits.append((Path(path).name, i, tell, line.strip()[:100]))
    return hits, lines


def main(argv: list) -> int:
    tells = ai_tells()
    if "--selftest" in argv:
        with tempfile.TemporaryDirectory() as tmp:
            planted = Path(tmp) / "vincent.md"
            planted.write_text(DEFAULT[0].read_text(encoding="utf-8")
                               + "\nIt is worth noting that this is robust.\n",
                               encoding="utf-8")
            hits, _ = scan([planted], tells)
        found = {h[2] for h in hits}
        ok = {"it is worth noting", "robust"} <= found
        print(f"selftest: {'found both planted phrases' if ok else 'MISSED a plant'}: {sorted(found)}")
        return 0 if ok else 1
    paths = [Path(a) for a in argv] or DEFAULT
    hits, lines = scan(paths, tells)
    print(f"scanned {lines} lines in {len(paths)} files against {len(tells)} phrases")
    if not lines:
        print("SKIP: nothing was scanned, and a scan over nothing proves nothing")
        return 2
    for name, lineno, tell, line in hits:
        print(f"  {name}:{lineno} [{tell}] {line}")
    print("PASS: 0 hits" if not hits else f"FAIL: {len(hits)} hits")
    return 1 if hits else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
