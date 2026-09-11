#!/usr/bin/env python3
"""P22 and P25: live runs of the real scripts, each with its control.

Every check here runs the real `app/*.py` as a subprocess against a real
temporary vault through the real Tier 2 `--input` path. Each one is paired
with a control that must FAIL the identical check, because a live test with no
control launders a green result: it proves the check ran, not that it can tell
the difference.

Writes everything it captured into the directory given as argv[1] (default
~/Downloads/proof-briefing-front-page/live).
"""
import json
import re
import shutil
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


def save(name, text):
    (OUT / name).write_text(text, encoding="utf-8")


def check(label, ok, detail=""):
    print(f"      {'ok ' if ok else 'NO '} {label}{'  ' + detail if detail else ''}")
    if not ok:
        problems.append(f"{label}{': ' + detail if detail else ''}")
    return ok


# ── P25: the live end-to-end run, and the control that must fail it ──────────

def front_page_checks(text, businesses):
    """The identical check, run against the treatment and against the control."""
    results = {}
    results["has DO TODAY"] = "DO TODAY" in text
    block = text.split("DO TODAY", 1)[-1].split("THE REST", 1)[0]
    items = re.findall(r"·\s+\[ \]\s+(.+)", block)
    non_late = [i for i in items if "late" not in i]
    results["at most 7 non-late items"] = len(non_late) <= 7
    rows = text.split("THE REST", 1)[-1]
    named = [b for b in businesses if b.upper() in rows]
    results["one row per business"] = len(named) == len(businesses)
    results["_counts"] = {"items": len(items), "non_late": len(non_late),
                          "businesses_named": len(named)}
    return results


print("P25  live end to end on a four-business, 100-item Tier 2 fixture")
tmp = Path(tempfile.mkdtemp(prefix="van-gogh-live-"))
try:
    ctx = tier2_env.build(tmp, n_items=100, front_page_cap=7)
    names = [b["display_name"] for b in ctx["config"]["businesses"]]
    rc, out, err = tier2_env.run_script("morning_coffee.py", ctx["env"],
                                        ["--input", str(ctx["input"])])
    data = tier2_env.first_json(out)
    treatment = data["front_page_md"] + data["fold_rows_md"]
    save("p25_treatment.txt", treatment)
    save("p25_treatment.json", json.dumps(
        {k: v for k, v in data.items() if k in
         ("counts", "fold_rows", "front_page", "errors")}, indent=2, default=str))
    check("the run exits 0", rc == 0, f"rc={rc}")
    res = front_page_checks(treatment, names)
    for label, ok in res.items():
        if not label.startswith("_"):
            check(f"treatment: {label}", ok, json.dumps(res["_counts"]))

    # The control: the same data, rendered the way it was rendered before this
    # work existed. If the identical check passes here too, the check is
    # measuring nothing.
    control = data.get("buckets_md", "")
    save("p25_control.txt", control)
    cres = front_page_checks(control, names)
    failed = [k for k, v in cres.items() if not k.startswith("_") and not v]
    check("control fails the identical check", bool(failed),
          f"control failed: {failed or 'nothing, which is the bug'}")

    # ── P15: opening a fold pastes exactly what its row counted ─────────────
    print("\nP15  a function slice pastes exactly the count its row promised")
    sidecar = tmp / "sidecar.json"
    sidecar.write_text(json.dumps(data, indent=2, default=str), encoding="utf-8")
    import subprocess
    ONE_LINER = (
        "import sys, os, json; sys.path.insert(0, os.path.join("
        "os.environ['VG_APP'], 'app')); import week_review as wr; "
        "d=json.load(open(sys.argv[1])); "
        "keys={tuple(i['key']) for i in d['front_page']}; "
        "print(wr.fold_slice(d['buckets'], keys, sys.argv[2], "
        "sys.argv[3] if len(sys.argv)>3 else ''))")
    env = dict(ctx["env"])
    env["VG_APP"] = str(ROOT)
    captured = []
    mismatches = []
    for row in data["fold_rows"]:
        for fn in row["functions"]:
            proc = subprocess.run(
                [sys.executable, "-c", ONE_LINER, str(sidecar), row["tag"],
                 fn["name"]], capture_output=True, text=True, env=env,
                cwd=str(ROOT), timeout=120)
            pasted = proc.stdout.count("[ ]")
            captured.append(f"{row['tag']:12} {fn['name']:12} "
                            f"row {fn['count']:3}  slice {pasted:3}"
                            f"{'' if pasted == fn['count'] else '   MISMATCH'}")
            if pasted != fn["count"]:
                mismatches.append(f"{row['tag']}/{fn['name']}: "
                                  f"row {fn['count']}, slice {pasted}")
        proc = subprocess.run(
            [sys.executable, "-c", ONE_LINER, str(sidecar), row["tag"]],
            capture_output=True, text=True, env=env, cwd=str(ROOT), timeout=120)
        whole = proc.stdout.count("[ ]")
        captured.append(f"{row['tag']:12} {'(whole fold)':12} "
                        f"row {row['folded']:3}  slice {whole:3}"
                        f"{'' if whole == row['folded'] else '   MISMATCH'}")
        if whole != row["folded"]:
            mismatches.append(f"{row['tag']} whole: row {row['folded']}, "
                              f"slice {whole}")
    save("p15_slices.txt", "\n".join(captured) + "\n")
    check("every slice pastes its row's count", not mismatches,
          f"{len(captured)} slices checked; {mismatches or 'no mismatches'}")

    # ── P22: publishing fails open, with its control ─────────────────────────
    print("\nP22  publishing fails open, and says why")
    logs = ctx["vault"] / "van-gogh" / "logs"
    pages = logs / "briefing_pages.json"

    pages.write_text(json.dumps({"morning-coffee": {
        "url": "https://claude.ai/public/artifacts/example-page-id",
        "briefing_date": "2026-09-03", "last_attempt": "2026-09-04",
        "published": False,
        "reason": "this session has no publishing surface"}}, indent=2),
        encoding="utf-8")
    rc_fail, out_fail, _ = tier2_env.run_script(
        "morning_coffee.py", ctx["env"], ["--input", str(ctx["input"])])
    d_fail = tier2_env.first_json(out_fail)
    save("p22_unavailable.txt",
         f"rc={rc_fail}\npage_note: {d_fail.get('page_note')}\n\n"
         + d_fail.get("front_page_md", ""))
    check("exits 0 with publishing unavailable", rc_fail == 0, f"rc={rc_fail}")
    note = d_fail.get("page_note", "")
    check("carries one line naming why", "did not update" in note
          and "no publishing surface" in note, note)
    check("renders in full anyway", bool(d_fail.get("front_page_md"))
          and bool(d_fail.get("fold_rows_md")))

    pages.write_text(json.dumps({"morning-coffee": {
        "url": "https://claude.ai/public/artifacts/example-page-id",
        "briefing_date": "2026-09-04", "last_attempt": "2026-09-04",
        "published": True, "reason": ""}}, indent=2), encoding="utf-8")
    rc_ok, out_ok, _ = tier2_env.run_script(
        "morning_coffee.py", ctx["env"], ["--input", str(ctx["input"])])
    d_ok = tier2_env.first_json(out_ok)
    save("p22_available.txt",
         f"rc={rc_ok}\npage_note: {d_ok.get('page_note')!r}\n\n"
         + d_ok.get("front_page_md", ""))
    ok_note = d_ok.get("page_note", "")
    check("control: carries the link line", "https://" in ok_note, ok_note)
    check("control: and no failure line", "did not update" not in ok_note,
          ok_note)

    # ── P20 live: a second run reuses the URL ────────────────────────────────
    print("\nP20  a second run republishes to the same URL")
    before = json.loads(pages.read_text(encoding="utf-8"))
    rc2, out2, _ = tier2_env.run_script("morning_coffee.py", ctx["env"],
                                        ["--input", str(ctx["input"])])
    after = json.loads(pages.read_text(encoding="utf-8"))
    check("the stored URL is unchanged and there is only one",
          before == after and len(after) == 1, json.dumps(after))
    save("p20_pages.json", json.dumps(after, indent=2))

    # ── P18 live: rerunning creates no new ledger rows ───────────────────────
    print("\nP18  a rerun creates no new draft ledger rows")
    ledger = logs / "draft_ledger.json"
    seed = {"gmail|dana ruiz|meridian redline v4": {
        "web_link": "L", "id": "1", "provider": "google", "label": "Gmail",
        "created": "2026-09-04 06:40"}}
    ledger.write_text(json.dumps(seed, indent=2), encoding="utf-8")
    tier2_env.run_script("morning_coffee.py", ctx["env"],
                         ["--input", str(ctx["input"])])
    after_ledger = json.loads(ledger.read_text(encoding="utf-8"))
    check("zero new ledger rows", after_ledger == seed,
          f"{len(after_ledger)} rows")
finally:
    shutil.rmtree(tmp, ignore_errors=True)

print(f"\nartifacts in {OUT}")
print("\nRESULT:", "FAIL" if problems else "PASS")
for p in problems:
    print("  " + p)
sys.exit(1 if problems else 0)
