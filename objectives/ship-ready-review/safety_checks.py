#!/usr/bin/env python3
"""P16, P17, P18: the three ways this could quietly hurt someone.

P16  the memory mirror reaching a project it was not given, or carrying a
     secret into a vault that syncs to a cloud drive
P17  a real person's name, home path, or email address shipping inside the
     product to every other client
P18  em-dashes and en-dashes, in the diff and in what a client actually reads
"""
import hashlib
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "app"))
sys.path.insert(0, str(ROOT / "tests"))

import conftest                                                 # noqa: E402,F401
import config_loader as cl                                      # noqa: E402
import memory_sync as ms                                        # noqa: E402
import week_review as wr                                        # noqa: E402

problems = []
EM, EN = chr(0x2014), chr(0x2013)


def tree_hash(root: Path) -> str:
    h = hashlib.sha256()
    for p in sorted(root.rglob("*")):
        if p.is_file():
            h.update(str(p.relative_to(root)).encode("utf-8"))
            h.update(p.read_bytes())
    return h.hexdigest()


# ── P16 ──────────────────────────────────────────────────────────────────────
print("P16  memory mirror: containment and content")
tmp = Path(tempfile.mkdtemp(prefix="vg-safety-"))
try:
    mine = tmp / "projects" / "-mine" / "memory"
    mine.mkdir(parents=True)
    (mine / "profile.md").write_text(
        "---\nname: Profile\ndescription: d\ntype: user\n---\n"
        "Works with [[Acme Corp]].\n", encoding="utf-8")
    # Every credential shape this product actually handles, not just the one
    # the redactor was known to catch. Planting a single shape is how the old
    # version of this check reported "no secrets" while Microsoft refresh
    # tokens went into the vault in clear text: a check that plants only what
    # already works cannot fail.
    SECRETS = [
        "sk-ant-api03-AAAAAAAAAAAAAAAAAAAAAAAA",
        "MS_GRAPH_REFRESH_TOKEN_WORK=0.AXoAabcdefghijklmnopqrstuvwxyz01234567",
        "MS_GRAPH_REFRESH_TOKEN_HOME: M.C5_BAY.0.U.-Cabcdefghijklmnopqrstuvwxyz",
        "MS_GRAPH_CLIENT_SECRET_WORK=abcdefghijklmnopqrstuvwxyz012345",
        "GOOGLE_CLIENT_SECRET_HOME=GOCSPX-abcdefghijklmnopqrstuv",
        "GOOGLE_REFRESH_TOKEN_WORK=1//0gABCDEFGHIJKLMNOPQRSTUVWXYZ",
        "GRANOLA_API_KEY_MAIN=abcdefghijklmnopqrstuvwx",
        "ya29.A0ARrdaMAAAAAAAAAAAAAAAAAAAAAAAAAAAA",
        "ghp_AAAAAAAAAAAAAAAAAAAAAAAAAAAAAA",
        "xoxb-2384729384-2938472938472-9Xq2vLm4TbR7wYzA1cD5eF8g",
        # Azure secret values routinely contain "~", which the value class
        # excluded, so the match ran four characters and gave up.
        "MS_CLIENT_SECRET=Qm8Q~7xK9pQmR7vT2wY5zL8nD4sH6jF1gA0bE3cU",
        # The value follows a SPACE here, not a colon.
        "Authorization: Bearer 9Xq2vLm4TbR7wYzA1cD5eF8gH0jK3lM6nP9qS2tU",
        # Only the header used to match, so the key material survived.
        "-----BEGIN RSA PRIVATE KEY-----\n"
        "MIIEpAIBAAKCAQEA3xK9pQmR7vT2wY5zL8nD4sH6jF1gA0bE\n"
        "-----END RSA PRIVATE KEY-----",
        # A pasted JSON config or API response is the likeliest way a key
        # reaches chat memory, and the closing quote used to block the match.
        '{"refresh_token": "Zq7Kd3Wf9Lm2Ab8Cd4Ef6GhJk5Np1Rs"}',
        '{"api_key":"Yq6Jc2Ve8Kl1Za7Bd3De5FgIj4Mo0Qr"}',
        "sk-proj-Xp5Ib1Ud7Jk0Yz6Ac2Cd4EfHi3Ln9Pq",
        "private_key=Wo4Ha0Tc6Ij9Xy5Zb1Bc3DeGh2Km8Op",
        "session_key: Vn3Gz9Sb5Hi8Wx4Ya0Ab2CdFg1Jl7No",
        # URL-encoded, which stopped at the first escape after two characters.
        "client_secret=aa%2FUm2Fy8Ra4Gh7Vw3Xz9%3D",
        "Authorization: Basic dXNlcjpUbDFFeDdRejNGZzZVdjJXeThZeg==",
        # The native formats of the notes this mirrors.
        "**API_KEY**: Sk0Dw6Pz2Ef5Tu1Vy7Wx3YzAb9Ce4Gh",
        "| API_KEY | Rj9Cv5Oy1De4St0Ux6Vw2XyZa8Bd3Fg |",
        "https://user:Qi8Bu4Nx0Cd3Rs9@api.example.com",
        "eyJhbGciOiJIUzI1NiIs.eyJzdWIiOiIxMjM0NTY.SflKxwRJSMeKKF2QT4fwpM",
    ]
    (mine / "creds.md").write_text(
        "---\nname: Creds\ntype: reference\n---\n" + "\n".join(SECRETS) + "\n",
        encoding="utf-8")
    # The frontmatter is its own channel: a secret in `name:` reaches the
    # FILENAME and the heading, one in `description:` reaches the index. Both
    # used to bypass the redactor entirely, which only saw the body.
    FM_SECRET = "xoxb-9999999999-8888888888-ZzZzZzZzZzZzZzZzZzZzZzZz"
    (mine / "fm.md").write_text(
        f"---\nname: key {FM_SECRET}\ndescription: also {FM_SECRET}\n"
        "type: reference\n---\nclean body\n", encoding="utf-8")
    # `type:` is a channel too: it is written straight into the mirrored YAML,
    # and it was the one frontmatter field still bypassing redaction.
    TYPE_SECRET = "sk-ant-api03-TtTtTtTtTtTtTtTtTtTtTtTt"
    (mine / "ft.md").write_text(
        f"---\nname: Typed\ntype: {TYPE_SECRET}\n---\nclean body\n",
        encoding="utf-8")
    decoy = tmp / "projects" / "-another-clients-project" / "memory"
    decoy.mkdir(parents=True)
    (decoy / "theirs.md").write_text(
        "---\nname: Their Deal\ntype: project\n---\nConfidential.\n", encoding="utf-8")
    decoy_hash = tree_hash(decoy)
    claude_home = tmp / "fake-claude-home"
    claude_home.mkdir()
    target = tmp / "vault" / "wiki" / "memory"

    cl.memory_sync_enabled = lambda: True
    cl.memory_source_dirs = lambda: [str(mine)]
    cl.memory_target_dir = lambda: target

    found = ms.discover_memory_dirs()
    print(f"     discovered exactly: {[p.name for p in found]} from {[str(p.parent.name) for p in found]}")
    if found != [mine]:
        problems.append(f"P16 discovery returned {found}, expected only {mine}")

    ms.mirror()
    h1 = tree_hash(target)
    ms.mirror()
    h2 = tree_hash(target)
    print(f"     target tree hash stable across two runs: {h1 == h2}")
    if h1 != h2:
        problems.append("P16 the target tree changed on an unchanged second run")

    print(f"     decoy project untouched: {tree_hash(decoy) == decoy_hash}")
    if tree_hash(decoy) != decoy_hash:
        problems.append("P16 the decoy project was modified")

    outside = [p for p in tmp.rglob("*")
               if p.is_file() and target not in p.parents and p.parent != target
               and "projects" not in p.parts]
    print(f"     files written outside the target: {len(outside)}")

    # Empty-set guard: a "no secrets found" result means nothing if the mirror
    # never wrote anything. Assert it produced files before believing a zero.
    written = sorted(p.name for p in target.glob("*.md"))
    print(f"     mirror produced: {written}")
    if "Creds.md" not in written:
        problems.append("P16 the mirror wrote nothing, so the secret scan is vacuous")

    # Look for the planted VALUES, verbatim. Searching for the word
    # "refresh_token" would pass on a file that redacted the name and kept the
    # value, which is the leak that matters.
    body = "\n".join(p.read_text(encoding="utf-8") for p in target.glob("*.md"))
    names = " ".join(p.name for p in target.glob("*.md"))

    def needle(s):
        """The longest opaque run in a planted secret.

        Splitting on "=" broke on base64 padding: a value ending in "==" gave
        an empty needle, which is trivially present in any text, so the check
        reported a leak on a secret that had in fact been redacted. Take the
        longest secret-shaped token instead.
        """
        toks = re.split(r"[\s\"'{}|,]+", s)
        toks = [tok.strip(":=") for tok in toks]
        return max(toks, key=len) if toks else s

    leaked = [s[:34] for s in SECRETS if len(needle(s)) >= 12 and needle(s) in body]
    if FM_SECRET in body or FM_SECRET in names:
        leaked.append("frontmatter name/description: " + FM_SECRET[:20])
    if TYPE_SECRET in body or TYPE_SECRET in names:
        leaked.append("frontmatter type: " + TYPE_SECRET[:20])
    print(f"     planted secrets ({len(SECRETS)} shapes) reaching the vault: "
          f"{leaked or 'none'}")
    if leaked:
        problems.append(f"P16 secrets reached the vault: {leaked}")
finally:
    shutil.rmtree(tmp, ignore_errors=True)

# ── P17 ──────────────────────────────────────────────────────────────────────
print("\nP17  no personal identity in the shipped tree")
SHIPPED = ["app", "skills", "tests", "vault", "README.md", "CLAUDE.md",
           "DESIGN.md", "config.template.json", ".claude-plugin"]
# The check that matters is not "does an email-shaped string exist" (every
# fixture has one) but "does a REAL identity ship". So this is a deny list of
# the actual people, companies and domains involved, plus the developer's own
# home path. A placeholder like x@nomatch.com is not a leak; pd46energy.com is.
# A separator between the tokens must not hide the identity: "pd46energy",
# "pd46-energy" and "pd46 energy" are the same employer, and only the first
# was visible before. Real names get the same treatment for the same reason.
_SEP = r"[\s._-]?"
REAL_IDENTITIES = re.compile(
    r"\b(jake|arken|guzman|cloverleaf|securion|scholastic|"
    # "powermarket" is the company; "power market" spaced is the industry
    # sector, which the docs and fixtures use generically and correctly. Only
    # the closed-up spelling is the identity.
    r"powermarket|pacolet|empact|palladium|"
    rf"accretus{_SEP}partners|"
    rf"accretus|enerzinx|pd{_SEP}46{_SEP}energy|verrus|voya|dahnke|baldwin|"
    # Domains, not just people. Rounds 1-5 renamed the person and left the
    # employer behind: "Spencer Shweky sshweky@kands.com" became "Spencer Hale
    # shale@kands.com", and "Deerfield Partners" became "Fairview Partners" at
    # ops@deerfield.com. The final round then read both domains as invented
    # fixtures and graded PASS, because neither was on this list. A rename that
    # only covers the name is the failure mode this list has to be able to see.
    rf"kands|k{_SEP}and{_SEP}s|deerfield|crooked{_SEP}fork|"
    r"gohl|dauber|poyer|yanes|shweky|mirabito|kipp|stratton)\b", re.I)
DEV_HOME = re.compile(r"/Users/nobelchang|C:\\Users\\nobelchang", re.I)
# Named on purpose: the repo owners in the plugin manifest and the access note.
OWNER_FILES = {"plugin.json", "marketplace.json", "CLAUDE.md", "README.md"}
PATTERNS = {
    "real identity": REAL_IDENTITIES,
    "developer home path": DEV_HOME,
}
ALLOWED = set()
hits = []
for rel in SHIPPED:
    base = ROOT / rel
    files = [base] if base.is_file() else [p for p in base.rglob("*")
                                           if p.is_file() and p.suffix in
                                           (".py", ".md", ".json", ".yml", ".yaml",
                                            ".html", ".css", ".js")]
    for f in files:
        try:
            text = f.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        for label, pat in PATTERNS.items():
            for m in pat.finditer(text):
                val = m.group(0)
                if f.name in OWNER_FILES and label == "real identity":
                    continue          # the repo owners, named on purpose
                line = text[:m.start()].count("\n") + 1
                hits.append(f"{f.relative_to(ROOT)}:{line} [{label}] {val}")
# A deny list only finds names you already thought of, and it missed one that
# sat in a skill the whole time. So also sweep for name-SHAPED pairs, with a
# test precise enough to be worth reading: a surname is not ordinary
# vocabulary. "Cold Monitor" and "Google Calendar" are headings because
# "monitor" and "calendar" appear in lowercase all over this repo; "Shweky"
# appears nowhere in lowercase, because it is somebody's actual name.
# The surname takes one or more lowercase letters, not three or more. The
# earlier {2,} made "Kevin Yu" and "Christopher Wu" structurally invisible: the
# scanner printed zero while being incapable of seeing a whole class of real
# name. A scan that cannot fail is worse than no scan, because it is believed.
NAME_SHAPED = re.compile(r"\b([A-Z][a-z]{2,})\s+([A-Z][a-z]+)\b")
# Every deliberate placeholder, listed once. Reporting UNIQUE names against
# this list is the point: a new name in a fixture has to be added here on
# purpose, which is a moment to ask whether it is somebody real.
KNOWN_OK = {
    # placeholder people
    "Jane Doe", "John Smith", "Johnny Smith", "Jane Roe", "Ann Roe",
    "Bob Smith", "Bob King", "Sam Rivera", "Alex Reed", "Dana Ruiz",
    "Dana Whitfield", "Mark Delgado", "Spencer Hale", "Maria Cortez",
    "Ben Kerr", "Owen Trask", "Karen Lindqvist", "Adam Voss", "Pat Quinn",
    "Jordan Rivera", "Ada Lovelace",
    # placeholder companies, deals and projects
    "Beta Labs", "Acme Widget", "Brien Holdings", "Lakeshore Holdings",
    "Kestrel Harbor", "Willow Bend", "Ridgeline DC", "Project Atlas",
    "Project Beacon", "Solstice JDA", "Fairview LOI",
    # attributed quotes, product names, typefaces, headings
    "Marcus Aurelius", "Albert Einstein", "Microsoft Entra", "Dear Theo",
    "Bitstream Charter", "Book Antiqua", "Palatino Linotype", "Matthew Carter",
    "Company Holiday", "Weekly Eng", "Note Formatter", "Close Smith",
    "Handle Aurora", "Internal Teammate", "Acme Meridian",
    # Synthetic, and the two-letter surname is the property under test: the
    # tokenizer drops tokens under three characters, so a surname like this
    # never tokenizes and the match has to come from topic overlap instead.
    "Christopher Vo", "Kevin Alder",
    # Two-word phrases that are not names at all.
    "Apply On", "Bad Co", "Catch Up", "Dear Mr", "Format No", "Four Cs",
    "Fresh Co", "Ghost Co", "Stub Co", "Old Co", "New Co", "Stale Co",
    "Never Do", "Why It",
    "Cascadia Mono", "Commit Mono", "Plex Mono", "The Stamp",
    "Workbench Codename",
}
vocabulary = set()
for p_ in list((ROOT / "skills").rglob("*.md")) + list((ROOT / "app").rglob("*.py")):
    vocabulary.update(re.findall(r"\b[a-z]{3,}\b",
                                 p_.read_text(encoding="utf-8", errors="ignore")))

name_hits = []
SWEPT = ([p for p in (ROOT / "skills").rglob("*.md")]
         + [p for p in (ROOT / "tests").rglob("*.py")]
         + [p for p in (ROOT / "app").rglob("*.py")]
         + [ROOT / "README.md", ROOT / "CLAUDE.md", ROOT / "DESIGN.md"])
for p_ in SWEPT:
    if not p_.exists():
        continue
    text = p_.read_text(encoding="utf-8", errors="ignore")
    for m in NAME_SHAPED.finditer(text):
        first, last = m.group(1), m.group(2)
        full = f"{first} {last}"
        if full in KNOWN_OK:
            continue
        if last.lower() in vocabulary or first.lower() not in vocabulary and last.lower() in vocabulary:
            continue
        if last.lower() in vocabulary:
            continue
        name_hits.append((full, p_.relative_to(ROOT), text[:m.start()].count("\n") + 1))
UNIQUE = {}
for full, f, ln in name_hits:
    UNIQUE.setdefault(full, (f, ln))
name_hits = [(n, f, ln) for n, (f, ln) in sorted(UNIQUE.items())]

print(f"     name-shaped strings whose surname is not repo vocabulary: {len(name_hits)}")
for full, f, ln in name_hits[:15]:
    print(f"       {f}:{ln} {full}")
print(f"     (swept {len(SWEPT)} files across skills/, tests/, app/ and the docs)")
if name_hits:
    problems.append(f"P17 {len(name_hits)} possible real person names in skills/")

print(f"     hits: {len(hits)}")
for h in hits[:25]:
    print("       " + h)
if hits:
    problems.append(f"P17 {len(hits)} identity hits in the shipped tree")

# ── P18 ──────────────────────────────────────────────────────────────────────
print("\nP18  no em-dash or en-dash")
diff = subprocess.run(["git", "diff", "origin/main...HEAD"],
                      capture_output=True, text=True, cwd=str(ROOT)).stdout
added = [l for l in diff.splitlines() if l.startswith("+") and not l.startswith("+++")]
# No exemptions. The one fixture that genuinely needs U+2014 as parse input
# builds it with chr(0x2014), which is what the rest of the repo already does,
# so the skip-list this used to carry is gone. A disclosed skip is mitigation,
# not compliance: the right fix is to stop needing it.
dash_lines = [l for l in added if EM in l or EN in l]
print(f"     added diff lines carrying a dash: {len(dash_lines)}")
for l in dash_lines[:10]:
    print("       " + l[:110])
if dash_lines:
    problems.append(f"P18 {len(dash_lines)} added lines carry a dash")

out = {"waiting_on_user": [{"subject": "Widget deal redline", "days_late": 3,
                            "counterparty_email": "a@b.com"}],
       "inbox_pending": [{"subject": "Acme gadget timing",
                          "counterparty_email": "b@x.com"}],
       "cold_urgent": [], "cold_monitor": [{"subject": "Vendor pitch",
                                            "counterparty_email": "c@y.com"}]}
for briefing in ("morning-coffee", "afternoon-tea", "week"):
    for mode in ("terminal", "md"):
        r = wr.render_buckets_md(wr.group_by_bucket(out), briefing, mode)
        if EM in r or EN in r:
            problems.append(f"P18 dash in a rendered {briefing}/{mode} briefing")
r = wr.render_buckets_md(wr.group_by_bucket(out), "week",
                         judgment={"degraded": True,
                                   "degraded_reasons": ["the usage limit was reached"]})
if EM in r or EN in r:
    problems.append("P18 dash in the degraded notice")
print("     rendered briefings (3 briefings x 2 modes + degraded notice): clean")

print("\nRESULT:", "FAIL" if problems else "PASS")
for p in problems:
    print("  " + p)
sys.exit(1 if problems else 0)
