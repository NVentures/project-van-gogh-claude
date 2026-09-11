#!/usr/bin/env python3
"""memory_sync.py: mirror Claude Code chat memory into the vault.

Chat memory is a cache; the vault is canonical. A fact learned in conversation
that lives only in `~/.claude/projects/<slug>/memory/` is invisible to every
briefing, every search, and every other device. This copies it into the vault
where the rest of the knowledge base can see it.

Three limits are deliberate, and the first one is the reason this ships off:

* **Discovery is scoped, never a scan.** One machine can hold Claude Code
  projects for several unrelated clients. A blanket walk of
  `~/.claude/projects/*` would copy one client's memory into another client's
  vault. So the source list is either what the user configured explicitly or
  the two directories that provably belong to this install: the vault and the
  plugin repo.
* **The mirror is one way.** It writes into the vault and never back into
  `~/.claude`. A two-way sync between a machine-written cache and a
  hand-edited vault has no correct conflict rule.
* **Merging is a proposal, never a write.** `merge_candidates` suggests which
  fact belongs on which existing page. A human confirms each one through the
  vault-audit skill.
"""

from __future__ import annotations

import hashlib
import re
from pathlib import Path

import config_loader

# Claude Code names a project directory by replacing every character that is
# not a letter or a digit with a hyphen. "<home>/Documents/Obsidian - Second
# Brain" becomes "-Users-x-Documents-Obsidian---Second-Brain": the run of three
# hyphens is space, hyphen, space, which is why a naive "collapse repeats"
# version of this transform silently fails to find a real directory.
_NON_ALNUM_RE = re.compile(r"[^A-Za-z0-9]")

# Windows forbids these in a filename, and a trailing dot or space is also
# illegal there while being perfectly legal on macOS. A mirror that writes an
# unopenable file on one platform is not a mirror.
_ILLEGAL_RE = re.compile(r'[<>:"/\\|?*\x00-\x1f]')

_FRONTMATTER_RE = re.compile(r"\A---\s*\n(?P<fm>.*?)\n---\s*\n?(?P<body>.*)\Z", re.S)

# A vault is not a safe place for a credential. It syncs to Dropbox or iCloud,
# it gets shared, it gets indexed, and unlike ~/.claude nobody thinks of it as
# sensitive. Chat memory can absolutely contain a key someone pasted into a
# conversation, so anything key-shaped is replaced on the way in. The rule is
# deliberately blunt: over-redacting a memory note costs a reader nothing,
# and under-redacting one puts a live credential in a synced folder.
# The characters a secret value can contain. "~" is here because Azure client
# secret values routinely include it, and excluding it made the value look four
# characters long, under the length floor, so the whole secret was passed
# through untouched. "=" is base64 padding.
# "%" is here for URL-encoded values, which otherwise stop at the first escape
# after two characters and fall under the length floor.
_VALUE = r"[\"']?[A-Za-z0-9_\-./+~=%]{12,}"

# What can sit between a key name and its value. A JSON dump pastes as
# {"refresh_token": "..."}, so the closing quote has to be allowed before the
# colon; markdown notes write **API_KEY**: and | API_KEY | value |, which are
# the native formats of the files this mirrors.
_SEP_TO_VALUE = r"[\"'*`\s|]*(?:[:=]|\|)[\"'*`\s|]*"

_SECRET_PATTERNS = [
    re.compile(r"sk-ant-[A-Za-z0-9_\-]{8,}"),                    # Anthropic
    re.compile(r"sk-(?:proj-)?[A-Za-z0-9_\-]{20,}"),             # OpenAI, incl. project keys
    re.compile(r"ya29\.[A-Za-z0-9_\-]{10,}"),                    # Google access
    re.compile(r"1//[A-Za-z0-9_\-]{20,}"),                       # Google refresh
    re.compile(r"AIza[A-Za-z0-9_\-]{20,}"),                      # Google API key
    re.compile(r"gh[pousr]_[A-Za-z0-9]{20,}"),                   # GitHub
    # Four providers this codebase does not integrate with. They cost one line
    # each, their prefixes are distinctive enough to carry no over-redaction
    # risk, and a person's chat memory is not limited to the services their
    # briefing tool happens to use.
    re.compile(r"github_pat_[A-Za-z0-9_]{20,}"),                 # GitHub fine-grained
    re.compile(r"sk_(?:live|test)_[A-Za-z0-9]{16,}"),            # Stripe
    re.compile(r"\bAKIA[A-Z0-9]{16}\b"),                        # AWS access key id
    re.compile(r"SG\.[A-Za-z0-9_\-]{16,}\.[A-Za-z0-9_\-]{16,}"),  # SendGrid
    re.compile(r"xox[baprs]-[A-Za-z0-9\-]{10,}"),                # Slack
    # All three JWT segments. Stopping at the third dot replaced the header and
    # payload and left the signature in the file.
    re.compile(r"eyJ[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]*"),
    # The whole PEM block, not just its header. Matching the header alone
    # replaced the words "BEGIN RSA PRIVATE KEY" and left the key material
    # underneath sitting in the vault.
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?"
               r"(?:-----END [A-Z ]*PRIVATE KEY-----|\Z)", re.S),
    re.compile(r"0\.A[A-Za-z0-9_\-]{20,}"),                     # Microsoft refresh
    re.compile(r"M\.C\d+_[A-Z]{3}\.[A-Za-z0-9_\-.]{20,}"),       # Microsoft consumer
    re.compile(r"GOCSPX-[A-Za-z0-9_\-]{10,}"),                  # Google client secret
    # The key-name form. It matches the WHOLE variable name containing a
    # secret word, on both sides. An underscore is a word character, so a
    # bare \b around the keyword matched neither MS_GRAPH_REFRESH_TOKEN_WORK
    # nor GRANOLA_API_KEY_MAIN: both are this product's own naming convention,
    # and both were writing their values into the vault in clear text.
    re.compile(r"(?i)\b[A-Za-z0-9_-]*"
               r"(?:api[_-]?key|private[_-]?key|session[_-]?key|secret|"
               r"password|passwd|refresh[_-]?token|client[_-]?secret|"
               r"access[_-]?token|bearer|token|credential)"
               r"[A-Za-z0-9_-]*" + _SEP_TO_VALUE + _VALUE),
    # "Authorization: Bearer <token>" and "Basic <cred>" put the value after a
    # SPACE, not after the colon, so the key pattern stopped at the scheme word
    # and judged six characters too short to be a secret.
    re.compile(r"(?i)\b(?:bearer|basic)\s+" + _VALUE),
    # A credential in a URL's userinfo.
    re.compile(r"(?i)\bhttps?://[A-Za-z0-9._%-]+:[^@/\s]{6,}@"),
]
REDACTED = "[redacted: this looked like a credential]"


def redact_secrets(text: str) -> tuple:
    """Replace anything credential-shaped. Returns (text, count)."""
    total = 0
    for pat in _SECRET_PATTERNS:
        text, n = pat.subn(REDACTED, text)
        total += n
    return text, total
_INDEX_BEGIN = "<!-- van-gogh:memory-index:begin -->"
_INDEX_END = "<!-- van-gogh:memory-index:end -->"


def project_slug(path: Path | str) -> str:
    """The Claude Code project-directory name for a filesystem path.

    Pure: no filesystem access, so it is testable without a real ~/.claude.
    """
    return _NON_ALNUM_RE.sub("-", str(path))


def discover_memory_dirs() -> list:
    """The memory directories this install is allowed to read.

    Returns [] when the feature is off. Configured directories win; otherwise
    the two derived from this install's own paths. Never a wildcard.
    """
    if not config_loader.memory_sync_enabled():
        return []

    configured = config_loader.memory_source_dirs()
    if configured:
        candidates = [Path(d).expanduser() for d in configured]
    else:
        root = Path.home() / ".claude" / "projects"
        candidates = []
        for own in (config_loader.vault(), config_loader.repo_root()):
            try:
                candidates.append(root / project_slug(own))
            except Exception:                                   # noqa: BLE001
                continue

    out = []
    for c in candidates:
        d = c if c.name == "memory" else c / "memory"
        if d.is_dir() and d not in out:
            out.append(d)
    return out


def _parse_frontmatter(text: str) -> tuple:
    m = _FRONTMATTER_RE.match(text)
    if not m:
        return {}, text
    fm = {}
    key = None
    for line in m.group("fm").splitlines():
        if re.match(r"^\s+", line) and key:
            sub = line.strip()
            if ":" in sub:
                k, v = sub.split(":", 1)
                fm[f"{key}.{k.strip()}"] = v.strip()
            continue
        if ":" in line:
            key, v = line.split(":", 1)
            key = key.strip()
            fm[key] = v.strip()
    return fm, m.group("body")


def parse_memory_file(path: Path) -> dict | None:
    """Read one memory file into {name, description, type, body}.

    Tolerates three shapes because all three exist in the wild: a nested
    `metadata.type`, a flat legacy `type:`, and neither, in which case the
    filename prefix is the last resort. A bare `MEMORY.md` is the index, not a
    fact, and is reported as type "index" so mirror can skip it.
    """
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None

    fm, body = _parse_frontmatter(text)
    if path.name.upper() == "MEMORY.md".upper():
        return {"name": path.stem, "description": "", "type": "index",
                "body": body, "redacted": 0, "source": str(path)}

    mtype = (fm.get("metadata.type") or fm.get("type") or "").strip()
    if not mtype:
        prefix = path.stem.split("_", 1)[0].lower()
        mtype = prefix if prefix in {"user", "feedback", "project", "reference"} else "note"

    name, name_n = redact_secrets((fm.get("name") or path.stem).strip())
    desc, desc_n = redact_secrets((fm.get("description") or "").strip())
    mtype, type_n = redact_secrets(mtype)
    body_text, body_n = redact_secrets(body.strip())
    return {
        "name": name,
        "description": desc,
        "type": mtype,
        "body": body_text,
        "redacted": name_n + desc_n + type_n + body_n,
        "source": str(path),
    }


def _safe_filename(name: str) -> str:
    cleaned = _ILLEGAL_RE.sub("-", name).rstrip(" .")
    return (cleaned or "untitled") + ".md"


def _render(entry: dict) -> str:
    """One mirrored file. Wikilinks in the body are preserved verbatim (they
    are how the mirrored fact joins the vault's graph); anything that looks
    like a credential is not."""
    lines = [
        "---",
        "type: memory",
        f"memory_type: {entry['type']}",
        f"name: {entry['name']}",
    ]
    if entry["description"]:
        lines.append(f"description: {entry['description']}")
    lines += ["source: chat memory (mirrored, do not hand-edit)"]
    if entry.get("redacted"):
        lines.append(f"redacted: {entry['redacted']}")
    lines += ["---", "", f"# {entry['name']}", "", entry["body"], ""]
    return "\n".join(lines)


def _index_block(entries: list) -> str:
    rows = [f"- [[{e['name']}]]: {e['description']}" if e["description"]
            else f"- [[{e['name']}]]" for e in entries]
    return "\n".join([_INDEX_BEGIN, "", *rows, "", _INDEX_END])


def _write_index(target: Path, entries: list) -> None:
    """Regenerate the machine-owned block, preserving anything a human wrote
    around it."""
    path = target / "index.md"
    existing = path.read_text(encoding="utf-8") if path.exists() else ""
    block = _index_block(entries)
    if _INDEX_BEGIN in existing and _INDEX_END in existing:
        head = existing[:existing.index(_INDEX_BEGIN)]
        tail = existing[existing.index(_INDEX_END) + len(_INDEX_END):]
        out = head + block + tail
    else:
        out = (existing + "\n\n" if existing.strip() else
               "# Chat memory (mirrored)\n\n") + block + "\n"
    # newline="\n" is load-bearing, not tidiness. Python's text mode translates
    # "\n" to "\r\n" on Windows, so the bytes on disk stop matching the string
    # that produced them, the content hash below never matches, and every run
    # rewrites every file forever. Windows CI caught exactly that.
    path.write_text(out, encoding="utf-8", newline="\n")


def mirror(dry_run: bool = False) -> dict:
    """Copy every discovered memory file into the vault. One way, idempotent.

    A second run writes nothing: each file is compared by content hash first.
    """
    result = {"written": 0, "skipped": 0, "errors": []}
    dirs = discover_memory_dirs()
    if not dirs:
        return result

    target = config_loader.memory_target_dir()
    if not dry_run:
        target.mkdir(parents=True, exist_ok=True)

    entries, claimed = [], {}
    for d in dirs:
        for path in sorted(d.glob("*.md")):
            entry = parse_memory_file(path)
            if entry is None:
                result["errors"].append(f"unreadable: {path}")
                continue
            if entry["type"] == "index":
                result["skipped"] += 1
                continue

            fname = _safe_filename(entry["name"])
            if fname in claimed and claimed[fname] != entry["source"]:
                # Never silently overwrite one project's fact with another's.
                fname = _safe_filename(f"{Path(d).parent.name}-{entry['name']}")
                result["errors"].append(
                    f"name collision on {entry['name']!r}; wrote {fname}")
            claimed[fname] = entry["source"]

            rendered = _render(entry)
            dest = target / fname
            if dest.exists():
                try:
                    # Compare BYTES on both sides. Reading as text would let the
                    # platform normalize line endings back and hide the same bug
                    # in the other direction.
                    same = (hashlib.sha256(dest.read_bytes()).hexdigest()
                            == hashlib.sha256(rendered.encode("utf-8")).hexdigest())
                except OSError:
                    same = False
                if same:
                    result["skipped"] += 1
                    entries.append(entry)
                    continue
            if not dry_run:
                try:
                    dest.write_text(rendered, encoding="utf-8", newline="\n")
                except OSError as e:
                    result["errors"].append(f"write failed for {dest}: {e}")
                    continue
            result["written"] += 1
            entries.append(entry)

    if entries and not dry_run:
        try:
            _write_index(target, entries)
        except OSError as e:
            result["errors"].append(f"index write failed: {e}")
    return result


_WIKILINK_RE = re.compile(r"\[\[([^\]|#]+)")


def merge_candidates() -> list:
    """Facts that look like they belong on an existing vault page.

    Proposals only. Nothing here writes anything, anywhere; the vault-audit
    skill puts each one in front of a human, one at a time.
    """
    if not config_loader.memory_sync_enabled():
        return []

    pages = {}
    for d in (config_loader.entities_dir(), config_loader.projects_dir()):
        try:
            for p in Path(d).glob("**/*.md"):
                pages[p.stem.lower()] = str(p)
        except OSError:
            continue
    if not pages:
        return []

    out = []
    for d in discover_memory_dirs():
        for path in sorted(d.glob("*.md")):
            entry = parse_memory_file(path)
            if not entry or entry["type"] == "index":
                continue
            names = {n.strip().lower() for n in _WIKILINK_RE.findall(entry["body"])}
            for name in sorted(names):
                if name in pages:
                    out.append({
                        "fact": entry["name"],
                        "target_page": pages[name],
                        "reason": f"body links to [[{name}]]",
                    })
    return out
