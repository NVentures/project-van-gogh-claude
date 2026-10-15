#!/usr/bin/env python3
"""timing_harness.py: machine-time ledger for the fifteen-minute install (P2).

Runs the warm-start pipeline end to end against a throwaway state dir and a
throwaway vault, with the real refresh tokens, and writes one JSON ledger per
run: seconds, return code and a few counts per stage, plus a content hash of
every meeting page the run filed. Two ledgers (one per arm) are then compared
with `--compare`, which is the P2 verdict.

    .venv/bin/python objectives/fifteen-minute-install/timing_harness.py \
        --arm serial --out evidence/harness-serial-base.json
    .venv/bin/python objectives/fifteen-minute-install/timing_harness.py \
        --arm fast --out evidence/harness-fast.json
    .venv/bin/python objectives/fifteen-minute-install/timing_harness.py \
        --compare evidence/harness-serial-base.json evidence/harness-fast.json
    .venv/bin/python objectives/fifteen-minute-install/timing_harness.py --selftest

Isolation, in the order it is enforced:

  1. A temp state dir holds copies of ONLY `.env`, `google_client_secret.json`
     and `ms_client_secret.json`; every child gets `VAN_GOGH_STATE_DIR` pointing
     at it. (Known gap, fixed by another lane: auth_bootstrap, google_client and
     microsoft_client still read the two app-credential files from the real
     `~/.config/van-gogh/`. The copies are byte-identical, so both paths read
     the same content.)
  2. A temp vault is scaffolded with `vault/scaffold.py` and pointed at with
     `app/migrate_to_vault.py --vault`, which writes the vault pointer into
     the temp state dir.
  3. `config.json` is a copy of the real one with `obsidian.vault_path` and
     every other absolute or `~` path rewritten under the temp vault, and every
     scheduled or sending feature switched off.
  4. BEFORE the first pipeline write, a child python with the same env prints
     `config_loader.vault()`, `van_gogh_root()` and `logs_dir()`; all three
     must resolve under the temp vault or the run stops. A copied config can
     still point home: the pointer only locates the config file, the paths
     come from a field inside it. `--selftest` proves this check can fail.
  5. The real `.env` and the real `config.json` are hashed before and after;
     a changed hash fails the run.

Nothing here sends mail: the measured scripts (week_review, warm_start,
meeting_ingest) have no send path, and the digest, prep, notes, advisor and
alert features are all disabled in the temp config.

stdlib only, Python 3.10 compatible, no em or en dash anywhere.
"""

import argparse
import copy
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent.parent
APP = REPO / "app"
REAL_STATE = Path(os.environ.get("VAN_GOGH_REAL_STATE_DIR") or
                  (Path.home() / ".config" / "van-gogh"))
CRED_FILES = (".env", "google_client_secret.json", "ms_client_secret.json")

STAGES = ("scan", "propose", "apply", "ingest", "first_briefing_data")
LEDGER_VERSION = 1

# Floors (P2): the run must have done real work before its time means anything.
FLOOR_THREADS = 50
FLOOR_DEALS = 1
MEETING_LIMIT = 10

# Compare targets (P2): the fast arm in 6:00 or less and at most half the serial arm.
FAST_MAX_SECONDS = 360
FAST_MAX_RATIO = 0.5

SCAN_TIMEOUT_S = 20 * 60
SCAN_POLL_S = 2
STAGE_TIMEOUT_S = 60 * 60

# Features that must not fire from a throwaway vault. Each is `section.key`.
FEATURE_FLAGS_OFF = (
    ("digest", "enabled"),
    ("support", "alerts_enabled"),
    ("support", "job_watch_enabled"),
    ("support", "repair_agent_enabled"),
    ("advisor", "enabled"),
    ("memory_sync", "enabled"),
    ("workbench", "autostart"),
    ("meeting_prep", "enabled"),
    ("notes", "enabled"),
    ("kpi", "enabled"),
    ("finance", "enabled"),
    ("contact_capture", "enabled"),
)
# Every skill with a cadence entry (config_loader.SKILL_SCHEDULE_DEFAULTS).
CADENCE_SKILLS = ("calendar-stub-check", "relationship-radar", "five-fifteen",
                  "voice-calibration", "voice-generator")

# Names migrate_to_vault moves OUT of the repo root. The harness refuses to
# run while any exists there, or the migration would delete a dev file.
MIGRATE_NAMES = ("config.json", "week.md", "morning-coffee.md",
                 "afternoon-tea.md", "logs", "projects", ".ingest_state.json")


class HarnessError(Exception):
    """A run that cannot be trusted. Message names the cause."""


class IsolationError(HarnessError):
    """A child process resolved a path outside the temp dirs."""


# ── small helpers ────────────────────────────────────────────────────────────

def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 16), b""):
            h.update(chunk)
    return h.hexdigest()


def sha256_text(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def real_vault():
    """The real vault, from the real pointer. Read only."""
    pointer = REAL_STATE / "vault-pointer"
    raw = pointer.read_text(encoding="utf-8").strip()
    if not raw:
        raise HarnessError(f"real vault pointer is empty: {pointer}")
    return Path(os.path.expanduser(raw)).resolve()


def real_config_path():
    return real_vault() / "van-gogh" / "config.json"


def code_sha():
    try:
        return subprocess.run(["git", "rev-parse", "HEAD"], cwd=str(REPO),
                              capture_output=True, text=True, check=True,
                              encoding="utf-8").stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return ""


def tree_state():
    """Whether the measured checkout is exactly its commit.

    A ledger stamped with a sha is a lie about the code it measured when the
    tree carries uncommitted work, which is exactly the case while other
    lanes build the speedup in the same checkout. `--repo` can point the
    harness at a clean worktree of the pinned base for the honest control.
    """
    try:
        out = subprocess.run(["git", "status", "--porcelain", "--", "."], cwd=str(REPO),
                             capture_output=True, text=True, check=True,
                             encoding="utf-8").stdout
    except (OSError, subprocess.CalledProcessError):
        return {"dirty": None, "modified_files": None}
    rows = [ln for ln in out.splitlines() if ln.strip()]
    return {"dirty": bool(rows), "modified_files": len(rows)}


def _say(*parts):
    print(*parts, flush=True)


def _is_under(path, root):
    try:
        Path(path).resolve().relative_to(Path(root).resolve())
        return True
    except ValueError:
        return False


def mmss(seconds):
    seconds = int(round(seconds or 0))
    return f"{seconds // 60}:{seconds % 60:02d}"


# ── config rewrite (pure, tested) ────────────────────────────────────────────

def _looks_like_path(value):
    if not isinstance(value, str) or not value:
        return False
    if value.startswith(("/", "~")):
        return True
    return bool(re.match(r"^[A-Za-z]:[\\/]", value))


def _rewrite_path(value, real_vault_path, temp_vault):
    expanded = Path(os.path.expanduser(value))
    try:
        rel = expanded.resolve().relative_to(Path(real_vault_path).resolve())
        return str(Path(temp_vault) / rel)
    except ValueError:
        pass
    # A path outside the real vault (a memory source dir, say) is redirected
    # to a mirror folder under the temp vault so no child reads or writes it.
    tail = str(expanded).lstrip("/\\")
    tail = re.sub(r"^[A-Za-z]:[\\/]", "", tail)
    return str(Path(temp_vault) / "redirected" / tail)


def _rewrite_paths(obj, real_vault_path, temp_vault):
    if isinstance(obj, dict):
        return {k: _rewrite_paths(v, real_vault_path, temp_vault)
                for k, v in obj.items()}
    if isinstance(obj, list):
        return [_rewrite_paths(v, real_vault_path, temp_vault) for v in obj]
    if _looks_like_path(obj):
        return _rewrite_path(obj, real_vault_path, temp_vault)
    return obj


def build_config(real_cfg, real_vault_path, temp_vault):
    """The temp config: every path under the temp vault, every feature off.

    Pure: takes the parsed real config and returns a new dict. The vault path
    is set LAST and explicitly, after the generic rewrite, so a real config
    whose `vault_path` is written with `~` or a symlinked prefix still lands
    exactly on the temp vault and not on a rewritten guess at it.
    """
    cfg = _rewrite_paths(copy.deepcopy(real_cfg), real_vault_path, temp_vault)
    cfg.setdefault("obsidian", {})["vault_path"] = str(temp_vault)
    for section, key in FEATURE_FLAGS_OFF:
        block = cfg.get(section)
        if not isinstance(block, dict):
            block = {}
            cfg[section] = block
        block[key] = False
    cadence = cfg.get("cadence")
    if not isinstance(cadence, dict):
        cadence = {}
        cfg["cadence"] = cadence
    for skill in set(CADENCE_SKILLS) | set(cadence.keys()):
        row = cadence.get(skill)
        if not isinstance(row, dict):
            row = {}
            cadence[skill] = row
        row["enabled"] = False
    return cfg


# ── isolation (tested) ───────────────────────────────────────────────────────

PROBE_CODE = r"""
import json, sys
sys.path.insert(0, sys.argv[1])
import config_loader, user_state
print(json.dumps({
    "vault": str(config_loader.vault()),
    "van_gogh_root": str(config_loader.van_gogh_root()),
    "logs_dir": str(config_loader.logs_dir()),
    "state_dir": str(user_state.state_dir()),
    "env_file": str(user_state.env_file()),
}))
"""


def probe_paths(env):
    """Ask a child python, under `env`, where it would read and write."""
    r = subprocess.run([sys.executable, "-c", PROBE_CODE, str(APP)],
                       capture_output=True, text=True, encoding="utf-8",
                       env=env, cwd=str(REPO), timeout=120)
    if r.returncode != 0:
        raise HarnessError("isolation probe failed: " + (r.stderr or "")[-800:])
    line = (r.stdout or "").strip().splitlines()[-1]
    return json.loads(line)


def check_isolation(paths, temp_vault, temp_state):
    """Raise IsolationError unless every resolved path sits under the temp dirs."""
    bad = []
    for key in ("vault", "van_gogh_root", "logs_dir"):
        if not _is_under(paths.get(key, ""), temp_vault):
            bad.append(f"{key}={paths.get(key)!r} is not under {temp_vault}")
    for key in ("state_dir", "env_file"):
        if not _is_under(paths.get(key, ""), temp_state):
            bad.append(f"{key}={paths.get(key)!r} is not under {temp_state}")
    if bad:
        raise IsolationError("; ".join(bad))
    return paths


# ── pages (tested) ───────────────────────────────────────────────────────────

_DATE_RE = re.compile(
    r"\d{4}-\d{2}-\d{2}(?:[T ]\d{2}:\d{2}(?::\d{2})?(?:\.\d+)?(?:Z|[+-]\d{2}:?\d{2})?)?")
_RUN_ID_RE = re.compile(r"\b\d{8}-\d{6}(?:-[0-9a-fA-F]{4})?\b")
_CLOCK_RE = re.compile(r"\b\d{1,2}:\d{2}(?::\d{2})?\s*(?:[AaPp]\.?[Mm]\.?)?\b")


def normalize_page(text):
    """Markdown with dates, clock times and run ids replaced by placeholders."""
    text = text.replace("\r\n", "\n")
    text = _RUN_ID_RE.sub("<run>", text)
    text = _DATE_RE.sub("<date>", text)
    text = _CLOCK_RE.sub("<time>", text)
    lines = [ln.rstrip() for ln in text.split("\n")]
    return "\n".join(lines).strip() + "\n"


def tilde_paths(value):
    """A copy of `value` with every string's home-folder prefix replaced by "~"."""
    home = str(Path.home())
    if isinstance(value, dict):
        return {k: tilde_paths(v) for k, v in value.items()}
    if isinstance(value, list):
        return [tilde_paths(v) for v in value]
    if isinstance(value, str) and home and home in value:
        return value.replace(home, "~")
    return value


def page_key(name):
    """A page is keyed by a hash of its filename, never the filename itself.

    Meeting pages are titled after real meetings and the people in them, and
    this ledger ships inside the plugin tree, which the publish mirror copies
    to the public repo. A consistent hash keeps the compare exact (same name
    on both arms gives the same key) without carrying a single title.
    """
    return hashlib.sha256(name.encode("utf-8")).hexdigest()[:16]


def page_hashes(vault_dir):
    """{page_key(filename): sha256(normalized markdown)} for wiki/sources/*.md."""
    out = {}
    sources = Path(vault_dir) / "wiki" / "sources"
    if not sources.is_dir():
        return out
    for path in sorted(sources.glob("*.md")):
        out[page_key(path.name)] = sha256_text(normalize_page(
            path.read_text(encoding="utf-8", errors="replace")))
    return out


_HEADING_RE = re.compile(r"^#{1,6}\s+(.*)$", re.MULTILINE)
_FM_KEYS_CODE = ("type", "title", "meeting_id", "meeting_source", "attendees")


def page_structure(text):
    """What the pipeline decides about a page, as per-field hashes.

    The summary body is written by a model and no two live runs word it the
    same, so a byte compare of two arms can never pass, and anything derived
    from the prose (headings when a synthesis fell back to the vendor
    summary, wikilinks, the inferred business tag) moves with it. What both
    arms must agree on is what code and the vendor decide: the page type,
    its title, the meeting identity and the attendees. `synthesis` and the
    section headings are carried beside those so a fallback on one arm is
    visible, without failing the compare. Every value is hashed so the
    ledger carries no title or name (it ships in the public mirror).
    """
    text = normalize_page(text)
    fields = {}
    if text.startswith("---\n"):
        end = text.find("\n---", 4)
        block = text[4:end] if end > 0 else ""
        for line in block.splitlines():
            if ":" in line and not line.startswith(" "):
                key, value = line.split(":", 1)
                # An attendee is linked `[[Name]]` only when their entity page
                # exists at filing time, and that page may have been stubbed
                # from an earlier meeting's prose, so the brackets follow the
                # model; the attendee set itself is the vendor's.
                fields[key.strip()] = value.strip().replace("[[", "").replace("]]", "")
    headings = [h.strip() for h in _HEADING_RE.findall(text)]
    return {
        "identity": sha256_text("|".join(fields.get(k, "") for k in _FM_KEYS_CODE))[:16],
        "synthesis": fields.get("synthesis", ""),
        "sections": sha256_text("|".join(headings))[:16],
    }


def page_structure_hashes(vault_dir):
    """{page_key(filename): sha256(json(page_structure))} for wiki/sources/*.md."""
    out = {}
    sources = Path(vault_dir) / "wiki" / "sources"
    if not sources.is_dir():
        return out
    for path in sorted(sources.glob("*.md")):
        out[page_key(path.name)] = page_structure(
            path.read_text(encoding="utf-8", errors="replace"))
    return out


_MEETING_ID_RE = re.compile(r"^(?:meeting_id|granola_id):\s*(\S+)", re.MULTILINE)


def count_meeting_pages(vault_dir):
    sources = Path(vault_dir) / "wiki" / "sources"
    if not sources.is_dir():
        return 0
    n = 0
    for path in sources.glob("*.md"):
        try:
            if _MEETING_ID_RE.search(path.read_text(encoding="utf-8", errors="replace")):
                n += 1
        except OSError:
            pass
    return n


# ── floors and compare (tested) ──────────────────────────────────────────────

def threads_scanned(sidecar):
    """Every thread the scan classified, across the sections it writes."""
    sections = ("waiting_on_user", "inbox_pending", "filtered_pending",
                "cold_urgent", "cold_monitor", "suppressed")
    per = {s: len(sidecar.get(s) or []) for s in sections}
    return sum(per.values()), per


def check_floors(ledger):
    """The floors P2 names. Returns (checked lines, failure lines)."""
    stages = ledger.get("stages") or {}
    scan = (stages.get("scan") or {}).get("counts") or {}
    propose = (stages.get("propose") or {}).get("counts") or {}
    ingest = (stages.get("ingest") or {}).get("counts") or {}
    threads = int(scan.get("threads_scanned") or 0)
    deals = int(propose.get("deals") or 0)
    filed = int(ingest.get("filed") or 0)
    available = ingest.get("available")
    want = min(MEETING_LIMIT, int(available)) if available is not None else None

    checked, failures = [], []
    checked.append(f"threads scanned {threads} >= {FLOOR_THREADS}")
    if threads < FLOOR_THREADS:
        failures.append(f"threads scanned {threads} < {FLOOR_THREADS}")
    checked.append(f"deals proposed {deals} >= {FLOOR_DEALS}")
    if deals < FLOOR_DEALS:
        failures.append(f"deals proposed {deals} < {FLOOR_DEALS}")
    if want is None:
        checked.append(f"meetings filed {filed} == min({MEETING_LIMIT}, available) "
                       "(available unknown)")
        failures.append("meetings available is unknown, so the filed floor cannot be judged")
    else:
        checked.append(f"meetings filed {filed} == min({MEETING_LIMIT}, {available}) = {want}")
        if filed != want:
            failures.append(f"meetings filed {filed} != {want}")
    for name in STAGES:
        rc = (stages.get(name) or {}).get("rc")
        if rc != 0:
            failures.append(f"stage {name} rc={rc!r}")
    return checked, failures


def compare(serial, fast):
    """Stage table plus the P2 assertions. Returns (table lines, failures)."""
    lines, failures = [], []
    if serial.get("arm") != "serial" or fast.get("arm") != "fast":
        failures.append(f"expected arms serial then fast, got "
                        f"{serial.get('arm')!r} and {fast.get('arm')!r}")
    s_st, f_st = serial.get("stages") or {}, fast.get("stages") or {}
    lines.append(f"{'stage':<22}{'serial':>10}{'fast':>10}{'ratio':>8}")
    for name in STAGES:
        a = float((s_st.get(name) or {}).get("seconds") or 0)
        b = float((f_st.get(name) or {}).get("seconds") or 0)
        ratio = f"{b / a:.2f}" if a else "n/a"
        lines.append(f"{name:<22}{a:>10.1f}{b:>10.1f}{ratio:>8}")
    s_total = float(serial.get("total_seconds") or 0)
    f_total = float(fast.get("total_seconds") or 0)
    ratio = f"{f_total / s_total:.2f}" if s_total else "n/a"
    lines.append(f"{'total':<22}{s_total:>10.1f}{f_total:>10.1f}{ratio:>8}")
    lines.append(f"serial {mmss(s_total)}, fast {mmss(f_total)}, "
                 f"target fast <= {mmss(FAST_MAX_SECONDS)} and <= {FAST_MAX_RATIO:.0%} of serial")

    if f_total > FAST_MAX_SECONDS:
        failures.append(f"fast total {f_total:.1f}s > {FAST_MAX_SECONDS}s")
    if s_total <= 0:
        failures.append("serial total is 0, nothing to compare against")
    elif f_total > s_total * FAST_MAX_RATIO:
        failures.append(f"fast total {f_total:.1f}s > half of serial {s_total:.1f}s")
    for label, led in (("serial", serial), ("fast", fast)):
        floors = led.get("floors") or {}
        if not floors.get("ok"):
            failures.append(f"{label} ledger failed its floors: "
                            + "; ".join(floors.get("failures") or ["no floors recorded"]))

    s_pages, f_pages = serial.get("pages") or {}, fast.get("pages") or {}
    mode = "content"
    if serial.get("pages_structure") and fast.get("pages_structure"):
        # Both arms carry structural hashes: compare what code decides and
        # let the model's wording differ (see page_structure).
        s_pages, f_pages = serial["pages_structure"], fast["pages_structure"]
        mode = "structure"
    lines.append(f"page compare mode: {mode}")
    only_s = sorted(set(s_pages) - set(f_pages))
    only_f = sorted(set(f_pages) - set(s_pages))
    if only_s:
        failures.append(f"pages only in serial: {only_s}")
    if only_f:
        failures.append(f"pages only in fast: {only_f}")
    shared = set(s_pages) & set(f_pages)
    if mode == "structure":
        differ = sorted(n for n in shared if s_pages[n]["identity"] != f_pages[n]["identity"])
        if differ:
            failures.append(f"pages whose identity (type, title, meeting, attendees) differs: {differ}")
        fell_back = sorted(n for n in shared if s_pages[n]["synthesis"] != f_pages[n]["synthesis"])
        if fell_back:
            lines.append(f"note: synthesis fell back on one arm for {len(fell_back)} page(s) "
                         f"(a model-side difference, not a pipeline one): {fell_back}")
        sections = sorted(n for n in shared if s_pages[n]["sections"] != f_pages[n]["sections"])
        if sections:
            lines.append(f"note: section headings differ on {len(sections)} page(s), "
                         "which follows a synthesis fallback or a vendor summary with no fixed sections")
    else:
        differ = sorted(n for n in shared if s_pages[n] != f_pages[n])
        if differ:
            failures.append(f"pages whose normalized content differs: {differ}")
    lines.append(f"pages: serial {len(s_pages)}, fast {len(f_pages)}, "
                 f"shared {len(shared)}, differing {len(differ)}")
    return lines, failures


# ── the run ──────────────────────────────────────────────────────────────────

def child_env(state_dir, arm):
    env = dict(os.environ)
    env["VAN_GOGH_STATE_DIR"] = str(state_dir)
    env["VAN_GOGH_UNATTENDED"] = "1"
    env["VAN_GOGH_SCHEDULED"] = ""
    if arm == "serial":
        env["VAN_GOGH_SERIAL"] = "1"
    else:
        env.pop("VAN_GOGH_SERIAL", None)
    return env


def _run(cmd, env, timeout=STAGE_TIMEOUT_S):
    t0 = time.monotonic()
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8",
                           errors="replace", env=env, cwd=str(REPO), timeout=timeout)
        rc, out, err = r.returncode, r.stdout or "", r.stderr or ""
    except subprocess.TimeoutExpired as exc:
        rc = -1
        out = (exc.stdout or b"").decode("utf-8", "replace") if isinstance(exc.stdout, bytes) else (exc.stdout or "")
        err = f"timed out after {timeout}s"
    return time.monotonic() - t0, rc, out, err


def _json_from(text):
    """The first balanced JSON object in `text`, or None."""
    start = text.find("{")
    while start != -1:
        depth, in_str, esc = 0, False, False
        for i in range(start, len(text)):
            ch = text[i]
            if in_str:
                if esc:
                    esc = False
                elif ch == "\\":
                    esc = True
                elif ch == '"':
                    in_str = False
                continue
            if ch == '"':
                in_str = True
            elif ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    try:
                        return json.loads(text[start:i + 1])
                    except ValueError:
                        break
        start = text.find("{", start + 1)
    return None


def _pid_alive(pid):
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False


def build_env(state_dir, vault_dir, log):
    """Scaffold the throwaway install. Returns the written config dict."""
    for name in MIGRATE_NAMES:
        if (REPO / name).exists():
            raise HarnessError(f"refusing to run: {REPO / name} exists and "
                               "migrate_to_vault would move it out of the repo")
    state_dir.mkdir(parents=True, exist_ok=True)
    for name in CRED_FILES:
        src = REAL_STATE / name
        if src.exists():
            shutil.copy2(src, state_dir / name)
            log(f"copied {name}")
        else:
            log(f"missing in real state dir, not copied: {name}")
    if not (state_dir / ".env").exists():
        raise HarnessError("no .env to copy; the harness needs real refresh tokens")

    env = child_env(state_dir, "serial")
    r = subprocess.run([sys.executable, str(REPO / "vault" / "scaffold.py"), str(vault_dir)],
                       capture_output=True, text=True, encoding="utf-8", env=env,
                       cwd=str(REPO), timeout=300)
    if r.returncode != 0:
        raise HarnessError("scaffold failed: " + (r.stderr or r.stdout)[-800:])

    real_cfg = json.loads(real_config_path().read_text(encoding="utf-8"))
    cfg = build_config(real_cfg, real_vault(), vault_dir)
    (vault_dir / "van-gogh").mkdir(parents=True, exist_ok=True)
    (vault_dir / "van-gogh" / "config.json").write_text(
        json.dumps(cfg, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    r = subprocess.run([sys.executable, str(APP / "migrate_to_vault.py"), "--vault", str(vault_dir)],
                       capture_output=True, text=True, encoding="utf-8", env=env,
                       cwd=str(REPO), timeout=300)
    if r.returncode != 0:
        raise HarnessError("migrate_to_vault failed: " + (r.stderr or r.stdout)[-800:])
    pointer = state_dir / "vault-pointer"
    pointed = pointer.read_text(encoding="utf-8").strip()
    if Path(pointed).resolve() != vault_dir.resolve():
        raise HarnessError(f"pointer {pointer} says {pointed!r}, expected {vault_dir}")
    return cfg


def run_pipeline(arm, keep, out_path, log=_say):
    started = datetime.now()
    wall0 = time.monotonic()
    real_targets = {"env": REAL_STATE / ".env", "config": real_config_path()}
    real_sources = real_vault() / "wiki" / "sources"
    before = {k: sha256_file(p) for k, p in real_targets.items()}
    before["real_sources_count"] = len(list(real_sources.glob("*.md"))) if real_sources.is_dir() else 0

    state_dir = Path(tempfile.mkdtemp(prefix="vg-harness-state-")).resolve()
    vault_dir = Path(tempfile.mkdtemp(prefix="vg-harness-vault-")).resolve()
    ledger = {
        "version": LEDGER_VERSION,
        "arm": arm,
        "code_sha": code_sha(),
        "repo": str(REPO),
        "tree": tree_state(),
        "started": started.isoformat(timespec="seconds"),
        "python": sys.executable,
        "temp": {"state_dir": str(state_dir), "vault": str(vault_dir), "kept": bool(keep)},
        "isolation": {},
        "real_hashes": {"before": before, "after": {}, "unchanged": None},
        "stages": {},
        "total_seconds": 0.0,
        "wall_seconds": 0.0,
        "floors": {"checked": [], "failures": [], "ok": False},
        "pages": {},
        "errors": [],
    }
    scan_pid = None

    def finish(exit_code):
        after = {k: sha256_file(p) for k, p in real_targets.items()}
        after["real_sources_count"] = len(list(real_sources.glob("*.md"))) if real_sources.is_dir() else 0
        ledger["real_hashes"]["after"] = after
        ledger["real_hashes"]["unchanged"] = after == before
        if after != before:
            ledger["errors"].append("real .env, config.json or wiki/sources changed during the run")
            exit_code = 1
        ledger["wall_seconds"] = round(time.monotonic() - wall0, 1)
        ledger["finished"] = datetime.now().isoformat(timespec="seconds")
        out_path.parent.mkdir(parents=True, exist_ok=True)
        # Paths are written with the home folder as "~": this ledger ships
        # inside the plugin tree, which the publish mirror copies verbatim,
        # and a username in a path is one more thing a client need not see.
        out_path.write_text(json.dumps(tilde_paths(ledger), indent=2, ensure_ascii=False) + "\n",
                            encoding="utf-8")
        log(f"ledger written: {out_path}")
        if scan_pid and _pid_alive(scan_pid):
            try:
                os.kill(scan_pid, 15)
            except OSError:
                pass
        if keep:
            log(f"kept state dir: {state_dir}")
            log(f"kept vault:     {vault_dir}")
        else:
            shutil.rmtree(state_dir, ignore_errors=True)
            shutil.rmtree(vault_dir, ignore_errors=True)
        return exit_code

    try:
        build_env(state_dir, vault_dir, log)
        env = child_env(state_dir, arm)
        paths = probe_paths(env)
        check_isolation(paths, vault_dir, state_dir)
        ledger["isolation"] = paths
        log("isolation: " + ", ".join(f"{k}={v}" for k, v in paths.items()))
    except HarnessError as exc:
        ledger["errors"].append(f"{type(exc).__name__}: {exc}")
        log(f"FAIL before any pipeline write: {exc}")
        return finish(1)

    stages = ledger["stages"]
    sidecar = vault_dir / "van-gogh" / "logs" / "week_review_latest.full.json"

    def record(name, seconds, rc, counts, error=None):
        stages[name] = {"seconds": round(seconds, 1), "rc": rc, "counts": counts,
                        "error": error}
        ledger["total_seconds"] = round(sum(s["seconds"] for s in stages.values()), 1)
        log(f"{name:<20} {mmss(seconds):>6}  rc={rc}  {json.dumps(counts)}"
            + (f"  ERROR {error}" if error else ""))

    # scan: warm_start spawns week_review detached; wait for its sidecar.
    t0 = time.monotonic()
    wall_t0 = time.time()
    _, rc, out, err = _run([sys.executable, str(APP / "warm_start.py"), "scan", "--since", "60"], env)
    started_json = _json_from(out) or {}
    scan_pid = started_json.get("pid")
    # Fast arm: the install starts the meeting summaries the moment the
    # notetaker key is known, while the scan is still running, so the two
    # overlap exactly as they do in the real install. The control arm keeps
    # the old skill's order: scan, then propose, then the whole ingest.
    prefetch = None
    overlap = (arm == "fast" and "ingest-start" in
               (APP / "warm_start.py").read_text(encoding="utf-8", errors="replace"))
    if overlap:
        p_t0 = time.monotonic()
        _, p_rc, p_out, p_err = _run([sys.executable, str(APP / "warm_start.py"),
                                      "ingest-start", "--limit", str(MEETING_LIMIT)], env)
        prefetch = {"t0": p_t0, "rc": p_rc, "json": _json_from(p_out) or {},
                    "err": p_err[-400:]}
        log(f"prefetch started: rc={p_rc} pid={prefetch['json'].get('pid')}")
    scan_error = None
    sidecar_data = None
    if rc != 0 or not scan_pid:
        scan_error = f"scan did not start: rc={rc} {err[-400:]}"
    else:
        deadline = t0 + SCAN_TIMEOUT_S
        while time.monotonic() < deadline:
            if sidecar.exists() and sidecar.stat().st_mtime >= wall_t0 - 1:
                try:
                    sidecar_data = json.loads(sidecar.read_text(encoding="utf-8"))
                    break
                except ValueError:
                    pass  # mid-write
            if not _pid_alive(scan_pid):
                time.sleep(1)
                if sidecar.exists() and sidecar.stat().st_mtime >= wall_t0 - 1:
                    try:
                        sidecar_data = json.loads(sidecar.read_text(encoding="utf-8"))
                    except ValueError:
                        sidecar_data = None
                if sidecar_data is None:
                    scan_error = "week_review exited without writing the sidecar"
                break
            time.sleep(SCAN_POLL_S)
        if sidecar_data is None and scan_error is None:
            scan_error = f"sidecar not written within {SCAN_TIMEOUT_S}s"
    scan_seconds = time.monotonic() - t0
    counts = {}
    if sidecar_data is not None:
        total, per = threads_scanned(sidecar_data)
        counts = {"threads_scanned": total, "sections": per,
                  "since_days": sidecar_data.get("since_days"),
                  "errors": len(sidecar_data.get("errors") or [])}
    record("scan", scan_seconds, 0 if sidecar_data is not None else (rc or 1), counts, scan_error)
    if sidecar_data is None:
        return finish(1)

    # propose
    seconds, rc, out, err = _run([sys.executable, str(APP / "warm_start.py"), "propose"], env)
    proposal = _json_from(out) or {}
    deals = proposal.get("deals") or []
    entities = proposal.get("entities") or []
    record("propose", seconds, rc, {"deals": len(deals), "entities": len(entities)},
           None if rc == 0 else err[-400:])
    if rc != 0:
        return finish(1)

    # apply: tick every proposed row (the harness's --tick-all).
    deal_idx = ",".join(str(i) for i in range(len(deals)))
    ent_idx = ",".join(str(i) for i in range(len(entities)))
    seconds, rc, out, err = _run([sys.executable, str(APP / "warm_start.py"), "apply",
                                  "--deals", deal_idx, "--entities", ent_idx], env)
    written = _json_from(out) or {}
    record("apply", seconds, rc, {"deals_written": written.get("deals"),
                                  "entities_written": written.get("entities"),
                                  "ticked_deals": len(deals), "ticked_entities": len(entities)},
           None if rc == 0 else err[-400:])
    if rc != 0:
        return finish(1)

    # ingest: how many meetings are there to file (untimed listing), then file them.
    list_seconds, list_rc, list_out, list_err = _run(
        [sys.executable, str(APP / "meeting_ingest.py"), "--meeting", "uningested",
         "--dry-run", "--limit", str(MEETING_LIMIT)], env, timeout=600)
    available = None
    m = re.search(r"Found (\d+) not yet in wiki/sources", list_out)
    if m:
        available = int(m.group(1))
    elif re.search(r"already ingested", list_out):
        available = 0
    if prefetch is not None:
        # Wait for the summaries that have been running since the scan
        # started, then file them. The stage's time is the whole span from
        # ingest-start to the end of filing, which overlaps the scan.
        staged_dir = Path(prefetch["json"].get("staged_dir") or (state_dir / "setup" / "staged"))
        manifest_path = staged_dir / "manifest.json"
        p_pid = prefetch["json"].get("pid")
        deadline = prefetch["t0"] + STAGE_TIMEOUT_S
        manifest = None
        while time.monotonic() < deadline:
            if manifest_path.exists():
                try:
                    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
                    break
                except ValueError:
                    pass
            if p_pid and not _pid_alive(p_pid):
                time.sleep(1)
                if manifest_path.exists():
                    try:
                        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
                    except ValueError:
                        manifest = None
                break
            time.sleep(SCAN_POLL_S)
        summaries_seconds = time.monotonic() - prefetch["t0"]
        f_seconds, rc, out, err = _run([sys.executable, str(APP / "warm_start.py"),
                                        "ingest-finish"], env)
        result = _json_from(out) or {}
        seconds = summaries_seconds + f_seconds
        filed = count_meeting_pages(vault_dir)
        ingest_counts = {
            "available": available,
            "list_seconds": round(list_seconds, 1),
            "list_rc": list_rc,
            "filed": filed,
            "overlapped_with_scan": True,
            "summaries_seconds": round(summaries_seconds, 1),
            "filing_seconds": round(f_seconds, 1),
            "prefetch_width": (manifest or {}).get("width"),
            "summary_timings": sorted((manifest or {}).get("timings", {}).values()),
            "model": (manifest or {}).get("model"),
            "staged": {"filed": len((manifest or {}).get("filed") or []),
                       "failed": len((manifest or {}).get("failed") or []),
                       "deferred": len((manifest or {}).get("deferred") or [])},
            "ingested_ok": not result.get("pending") and rc == 0,
            "backfilled": result.get("backfilled"),
            "degraded": result.get("degraded"),
            "errors": list(result.get("errors") or []) + (
                [] if manifest is not None else ["no manifest from the summaries job"]),
        }
    else:
        seconds, rc, out, err = _run([sys.executable, str(APP / "warm_start.py"), "ingest",
                                      "--limit", str(MEETING_LIMIT)], env)
        result = _json_from(out) or {}
        tail = result.get("ingest_tail") or ""
        done = re.search(r"Done\. (\d+) ingested, (\d+) skipped", tail)
        filed = count_meeting_pages(vault_dir)
        ingest_counts = {
            "available": available,
            "list_seconds": round(list_seconds, 1),
            "list_rc": list_rc,
            "filed": filed,
            "reported_ingested": int(done.group(1)) if done else None,
            "reported_skipped": int(done.group(2)) if done else None,
            "ingested_ok": result.get("ingested"),
            "backfilled": result.get("backfilled"),
            "errors": result.get("errors") or [],
        }
    error = None
    if rc != 0:
        error = err[-400:]
    elif result.get("errors"):
        error = "; ".join(str(e) for e in result["errors"])
    record("ingest", seconds, rc, ingest_counts, error)
    if list_rc != 0:
        ledger["errors"].append("meeting listing failed: " + (list_err or list_out)[-400:])

    # first_briefing_data: the data behind the two first briefings. On the base
    # commit that is what Morning Coffee runs as its data subprocess (a second
    # full fetch). On the new code the install runs `warm_start.py
    # first-briefing` for Week and for the time-of-day briefing, both from the
    # saved scan, so that is what this stage times when the repo supports it.
    warm = (APP / "warm_start.py").read_text(encoding="utf-8", errors="replace")
    if "first-briefing" in warm:
        seconds = 0.0
        rc = 0
        err = ""
        counts = {"path": "first-briefing (reuse-scan)", "runs": {}}
        for which in ("week", "auto"):
            s, r, out, e = _run([sys.executable, str(APP / "warm_start.py"),
                                 "first-briefing", "--which", which], env)
            seconds += s
            rc = rc or r
            err = err or e
            info = _json_from(out) or {}
            counts["runs"][which] = {"seconds": round(s, 1), "rc": r,
                                     "which": info.get("which") or info.get("briefing"),
                                     "data": bool(info.get("data"))}
        record("first_briefing_data", seconds, rc, counts, None if rc == 0 else err[-400:])
    else:
        seconds, rc, out, err = _run([sys.executable, str(APP / "week_review.py"),
                                      "--since", "14", "--no-extensions"], env)
        data = _json_from(out) or {}
        total, per = threads_scanned(data) if data else (0, {})
        record("first_briefing_data", seconds, rc,
               {"path": "week_review --since 14", "threads": total, "sections": per,
                "calendar": len(data.get("calendar") or []),
                "errors": len(data.get("errors") or [])},
               None if rc == 0 else err[-400:])

    ledger["pages"] = page_hashes(vault_dir)
    ledger["pages_structure"] = page_structure_hashes(vault_dir)
    ledger["stage_sum_seconds"] = ledger["total_seconds"]
    if prefetch is not None:
        # Overlapping stages: the pipeline's time is the critical path from
        # the scan's start to the end of the first briefing data, which is
        # what a client waits for. The stage sum is kept beside it.
        ledger["total_seconds"] = round(time.monotonic() - t0, 1)
        ledger["overlap"] = True
    checked, failures = check_floors(ledger)
    ledger["floors"] = {"checked": checked, "failures": failures, "ok": not failures}
    log("floors checked:")
    for line in checked:
        log(f"  {line}")
    if failures:
        log("FLOOR FAILURES:")
        for line in failures:
            log(f"  {line}")
    log(f"total {mmss(ledger['total_seconds'])} across {len(stages)} stages, arm={arm}")
    return finish(0 if not failures else 1)


# ── selftest ─────────────────────────────────────────────────────────────────

def selftest(home_vault=None, log=_say):
    """Prove the isolation assertion can fail: a copied config that points home.

    Builds a temp state dir and temp vault whose config.json names `home_vault`
    (the real vault by default) as `obsidian.vault_path`, runs the real probe,
    and expects IsolationError. Then the control: the same config pointed at
    the temp vault passes. Returns True only when both halves behave.
    """
    home_vault = Path(home_vault) if home_vault else real_vault()
    state_dir = Path(tempfile.mkdtemp(prefix="vg-selftest-state-")).resolve()
    vault_dir = Path(tempfile.mkdtemp(prefix="vg-selftest-vault-")).resolve()
    try:
        (vault_dir / "van-gogh").mkdir(parents=True)
        (state_dir / "vault-pointer").write_text(str(vault_dir) + "\n", encoding="utf-8")
        env = child_env(state_dir, "serial")
        base = {"user": {"full_name": "Self Test"}, "obsidian": {"vault_path": str(home_vault)}}
        (vault_dir / "van-gogh" / "config.json").write_text(json.dumps(base), encoding="utf-8")
        raised = False
        try:
            check_isolation(probe_paths(env), vault_dir, state_dir)
        except IsolationError as exc:
            raised = True
            log(f"selftest: pointing home raised as expected: {exc}")
        if not raised:
            log("selftest FAIL: a config pointing home passed the isolation check")
            return False
        base["obsidian"]["vault_path"] = str(vault_dir)
        (vault_dir / "van-gogh" / "config.json").write_text(json.dumps(base), encoding="utf-8")
        try:
            paths = check_isolation(probe_paths(env), vault_dir, state_dir)
        except IsolationError as exc:
            log(f"selftest FAIL: the control (config pointing at the temp vault) raised: {exc}")
            return False
        log(f"selftest: control passed, vault={paths['vault']}")
        return True
    finally:
        shutil.rmtree(state_dir, ignore_errors=True)
        shutil.rmtree(vault_dir, ignore_errors=True)


# ── CLI ──────────────────────────────────────────────────────────────────────

def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--arm", choices=("serial", "fast"), default="fast",
                    help="serial sets VAN_GOGH_SERIAL=1 in every child (the control)")
    ap.add_argument("--out", help="ledger path (default evidence/harness-<arm>-<stamp>.json)")
    ap.add_argument("--keep", action="store_true", help="keep the temp dirs and print their paths")
    ap.add_argument("--tick-all", action="store_true", default=True,
                    help="apply every proposed deal and entity (always on; kept for the record)")
    ap.add_argument("--compare", nargs=2, metavar=("SERIAL_JSON", "FAST_JSON"),
                    help="compare two ledgers and assert the P2 targets")
    ap.add_argument("--selftest", action="store_true",
                    help="prove the isolation assertion fails on a config that points home")
    ap.add_argument("--repo", help="measure this plugin checkout instead of the one the "
                                   "harness lives in (a clean worktree of the base commit)")
    args = ap.parse_args(argv)

    if args.repo:
        global REPO, APP
        REPO = Path(args.repo).resolve()
        APP = REPO / "app"
        if not (APP / "warm_start.py").exists():
            print(f"--repo {REPO} has no app/warm_start.py", flush=True)
            return 2

    if args.compare:
        a = json.loads(Path(args.compare[0]).read_text(encoding="utf-8"))
        b = json.loads(Path(args.compare[1]).read_text(encoding="utf-8"))
        lines, failures = compare(a, b)
        for line in lines:
            print(line)
        if failures:
            print("COMPARE FAIL:")
            for f in failures:
                print(f"  {f}")
            return 1
        print("COMPARE PASS")
        return 0

    if args.selftest:
        return 0 if selftest() else 1

    out = Path(args.out) if args.out else (
        HERE / "evidence" / f"harness-{args.arm}-{datetime.now().strftime('%Y%m%d-%H%M%S')}.json")
    return run_pipeline(args.arm, args.keep, out.resolve())


if __name__ == "__main__":
    sys.exit(main())
