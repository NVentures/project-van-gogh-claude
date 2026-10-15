#!/usr/bin/env python3
"""P25, second half: the real SKILL.md, run headless, over a real script run.

The script half is `live_check.py`. This one hands the same JSON to a headless
`claude -p` pointed at the real skills/morning-coffee/SKILL.md, and runs the
identical check over what it renders, plus the control: the same prompt with
the front-page sections stripped out of the skill it reads. If the control
passes the check too, the check is measuring the model's memory rather than
the skill, and it proves nothing.
"""
import json
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(HERE))
import tier2_env                                                # noqa: E402

OUT = Path(sys.argv[1] if len(sys.argv) > 1
           else Path.home() / "Downloads" / "proof-briefing-front-page" / "live")
OUT.mkdir(parents=True, exist_ok=True)
problems = []

PROMPT = """Read the render instructions in {skill} (the sections "Step 1 -
Render the briefing" through "THE WEB PAGE") and the briefing JSON at {json}.

Render the terminal briefing exactly as those instructions say, and print only
the rendered briefing. Do not run any script, do not draft anything, do not
publish anything, do not write any file. If the instructions tell you to paste
a key verbatim, paste that key's value from the JSON verbatim.
"""


def run_claude(prompt: str, timeout=600) -> str:
    cmd = ["claude", "-p", prompt, "--disable-slash-commands",
           "--strict-mcp-config", "--mcp-config", '{"mcpServers":{}}',
           "--permission-mode", "acceptEdits"]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout,
                          cwd=str(ROOT), stdin=subprocess.DEVNULL)
    return proc.stdout


def checks(text, businesses):
    block = text.split("DO TODAY", 1)[-1].split("THE REST", 1)[0]
    items = re.findall(r"\[ \]\s+(.+)", block)
    non_late = [i for i in items if "late" not in i]
    rows = text.split("THE REST", 1)[-1]
    named = [b for b in businesses if b.upper() in rows]
    return {
        "has DO TODAY": "DO TODAY" in text,
        "at most 7 non-late items": len(non_late) <= 7 and bool(items),
        "one row per business": len(named) == len(businesses),
        "_counts": {"items": len(items), "non_late": len(non_late),
                    "businesses": len(named)},
    }


tmp = Path(tempfile.mkdtemp(prefix="van-gogh-skilllive-"))
try:
    ctx = tier2_env.build(tmp, n_items=100, front_page_cap=7)
    names = [b["display_name"] for b in ctx["config"]["businesses"]]
    rc, out, err = tier2_env.run_script("morning_coffee.py", ctx["env"],
                                        ["--input", str(ctx["input"])])
    data = tier2_env.first_json(out)
    payload = tmp / "briefing.json"
    payload.write_text(json.dumps(data, indent=2, default=str), encoding="utf-8")

    # Treatment: the real skill.
    skill = ROOT / "skills/morning-coffee/SKILL.md"
    print("running the real SKILL.md through claude -p")
    treatment = run_claude(PROMPT.format(skill=skill, json=payload))
    (OUT / "p25_skill_treatment.txt").write_text(treatment, encoding="utf-8")

    # Control: the same prompt over a copy of the skill with every front-page
    # instruction removed, AND a payload with the five front-page keys removed.
    # Stripping only the instructions is not a control: `front_page_md` in the
    # JSON literally contains the string "DO TODAY", so a model that pastes it
    # passes a check that was supposed to be measuring the skill.
    control_payload = tmp / "briefing_control.json"
    control_payload.write_text(json.dumps(
        {k: v for k, v in data.items()
         if k not in ("front_page", "front_page_md", "fold_rows_md",
                      "folded_md", "counts", "fold_rows")},
        indent=2, default=str), encoding="utf-8")
    text = skill.read_text(encoding="utf-8")
    stripped = text
    for start, end in (("### THE FRONT PAGE", "### YOUR DAY"),
                       ("### THE REST", "## Step 2")):
        if start in stripped and end in stripped:
            stripped = (stripped[:stripped.index(start)]
                        + stripped[stripped.index(end):])
    control_skill = tmp / "SKILL_control.md"
    control_skill.write_text(stripped, encoding="utf-8")
    print("running the control skill through claude -p")
    control = run_claude(PROMPT.format(skill=control_skill, json=control_payload))
    (OUT / "p25_skill_control.txt").write_text(control, encoding="utf-8")

    tres = checks(treatment, names)
    cres = checks(control, names)
    print("\ntreatment", json.dumps(tres["_counts"]))
    for k, v in tres.items():
        if k.startswith("_"):
            continue
        print(f"      {'ok ' if v else 'NO '} treatment: {k}")
        if not v:
            problems.append(f"treatment: {k}")
    print("control  ", json.dumps(cres["_counts"]))
    failed = [k for k, v in cres.items() if not k.startswith("_") and not v]
    print(f"      {'ok ' if failed else 'NO '} control fails the identical check: {failed}")
    if not failed:
        problems.append("the control passed the identical check; it proves nothing")
finally:
    shutil.rmtree(tmp, ignore_errors=True)

print("\nRESULT:", "FAIL" if problems else "PASS")
for p in problems:
    print("  " + p)
sys.exit(1 if problems else 0)
