#!/usr/bin/env python3
"""P6: completions through the seam under host codex, live, plus controls.

  --runs 8     the priority judge's smallest fixture through claude_cli.run_claude
               with VAN_GOGH_HOST=codex, a reply schema attached (--output-schema),
               each timed; PASS when the reply parses as the judge's list shape
  --control-a  the binary absent: the existing error class, naming codex
  --control-b  a Codex home with no auth.json: fails inside the timeout, auth class

The scratch CODEX_HOME holds the real auth.json for the runs; control B uses
an empty home. Nothing here touches the real ~/.codex.
"""
from __future__ import annotations

import argparse
import json
import os
import statistics
import sys
import time
from pathlib import Path

S = Path(os.environ["VG_SCRATCH"])
WT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(WT / "tests")); sys.path.insert(0, str(WT / "app"))
os.environ.setdefault("VAN_GOGH_STATE_DIR", str(S / "latency-state"))
(S / "latency-state").mkdir(exist_ok=True)
os.environ["VAN_GOGH_HOST"] = "codex"
os.environ["CODEX_HOME"] = str(S / "codex-home")
os.environ["VAN_GOGH_CODEX_BIN"] = str(S / "codex-npm/node_modules/@openai/codex-darwin-arm64/vendor/aarch64-apple-darwin/bin/codex")
import conftest  # noqa: E402,F401
import test_priority_judge as t  # noqa: E402
import priority_judge as pj  # noqa: E402
import claude_cli  # noqa: E402
import failure_class  # noqa: E402
import host  # noqa: E402

# The judge's reply shape, as a JSON Schema for --output-schema.
ITEM = {"type": "object", "additionalProperties": False,
        "properties": {"id": {"type": "integer"}, "relevant": {"type": "boolean"},
                       "priority": {"type": "string"}, "reason": {"type": "string"},
                       "slip_cost": {"type": "string"}, "slip_reason": {"type": "string"}},
        "required": ["id", "relevant", "priority", "reason", "slip_cost", "slip_reason"]}
# One verdict per item is what the prompt asks; the floor keeps an empty
# list (seen once in eight runs) from counting as an answer.
SCHEMA = {"type": "array", "items": ITEM, "minItems": 1}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--runs", type=int, default=0)
    ap.add_argument("--control-a", action="store_true")
    ap.add_argument("--control-b", action="store_true")
    a = ap.parse_args()
    prompt = pj.build_prompt(t.BUCKET, t.ITEMS)
    bad = 0
    if a.runs:
        times = []
        for i in range(1, a.runs + 1):
            t0 = time.monotonic()
            r = claude_cli.run_claude(prompt, claude_cli.HAIKU_MODEL, timeout=120, schema=SCHEMA)
            dt = time.monotonic() - t0; times.append(dt)
            try:
                parsed = json.loads(r.stdout)
                ok = (isinstance(parsed, list) and len(parsed) == len(t.ITEMS)
                      and all(set(ITEM["required"]) <= set(x) for x in parsed))
            except ValueError:
                parsed, ok = None, False
            ok = ok and r.returncode == 0
            bad += 0 if ok else 1
            print(f"run {i}: {'PASS' if ok else 'FAIL'} {dt:.1f}s rc={r.returncode} "
                  f"reply={r.stdout[:70]!r} err={(r.stderr or '')[:120]!r}")
        print(f"median {statistics.median(times):.1f}s max {max(times):.1f}s; {a.runs - bad} of {a.runs} PASS")
    if a.control_a:
        import shutil
        real_which, real_cands = host.shutil.which, host.codex_candidates
        os.environ.pop("VAN_GOGH_CODEX_BIN", None)
        host.shutil.which = lambda name: None
        host.codex_candidates = lambda: []
        try:
            claude_cli.run_claude(prompt, claude_cli.HAIKU_MODEL, timeout=30)
            print("control A: FAIL (no error raised)"); bad += 1
        except FileNotFoundError as exc:
            okA = "codex" in str(exc)
            print(f"control A: {'PASS' if okA else 'FAIL'} FileNotFoundError: {str(exc)[:90]}")
            bad += 0 if okA else 1
        finally:
            host.shutil.which, host.codex_candidates = real_which, real_cands
    if a.control_b:
        empty = S / "noauth-home"; empty.mkdir(exist_ok=True)
        os.environ["CODEX_HOME"] = str(empty)
        t0 = time.monotonic()
        r = claude_cli.run_claude(prompt, claude_cli.HAIKU_MODEL, timeout=120)
        dt = time.monotonic() - t0
        text = (r.stdout or "") + (r.stderr or "")
        cls = failure_class.classify(text) if hasattr(failure_class, "classify") else None
        auth = failure_class.AUTH_RE.search(text) is not None
        okB = r.returncode != 0 and dt < 120 and auth and (cls in (None, "auth"))
        print(f"control B: {'PASS' if okB else 'FAIL'} {dt:.1f}s rc={r.returncode} class={cls} "
              f"auth_re={auth} stderr={(r.stderr or '')[:140]!r}")
        bad += 0 if okB else 1
        os.environ["CODEX_HOME"] = str(S / "codex-home")
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
