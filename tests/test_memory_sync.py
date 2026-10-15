"""memory_sync: the mirror must never reach a project it was not given.

One machine holds Claude Code projects for several unrelated clients. The
decoy test below is the important one in this file: if discovery ever widens
to a wildcard, one client's memory lands in another client's vault, and no
other test in the suite would notice.
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))

import config_loader as cl                                      # noqa: E402
import memory_sync as ms                                        # noqa: E402


# ── The slug transform (pure: no filesystem) ─────────────────────────────────

def test_slug_replaces_every_non_alphanumeric_character():
    assert ms.project_slug("/Users/x/Documents/Van Gogh Claude") == \
        "-Users-x-Documents-Van-Gogh-Claude"


def test_slug_does_not_collapse_repeated_separators():
    # "Obsidian - Second Brain" has space, hyphen, space: three hyphens, not
    # one. A "collapse repeats" version of this silently finds no directory.
    assert ms.project_slug("/Users/x/Documents/Obsidian - Second Brain") == \
        "-Users-x-Documents-Obsidian---Second-Brain"


def test_slug_replaces_dots():
    assert ms.project_slug("/Users/x/NorthwindPartners.com") == \
        "-Users-x-NorthwindPartners-com"


# ── Discovery ────────────────────────────────────────────────────────────────

@pytest.fixture
def sources(tmp_path, monkeypatch):
    """Two real memory dirs plus a decoy that must never be discovered."""
    mine = tmp_path / "projects" / "-mine" / "memory"
    mine.mkdir(parents=True)
    (mine / "user_profile.md").write_text(
        "---\nname: Profile\ndescription: who they are\ntype: user\n---\n"
        "Works with [[Acme Corp]] on the widget line.\n", encoding="utf-8")
    (mine / "MEMORY.md").write_text("- [Profile](user_profile.md)\n", encoding="utf-8")

    decoy = tmp_path / "projects" / "-someone-elses-client" / "memory"
    decoy.mkdir(parents=True)
    (decoy / "their_secret.md").write_text(
        "---\nname: Their Secret\ntype: project\n---\nConfidential.\n", encoding="utf-8")

    target = tmp_path / "vault" / "wiki" / "memory"
    monkeypatch.setattr(cl, "memory_target_dir", lambda: target)
    return {"mine": mine, "decoy": decoy, "target": target}


def _enable(monkeypatch, dirs):
    monkeypatch.setattr(cl, "memory_sync_enabled", lambda: True)
    monkeypatch.setattr(cl, "memory_source_dirs", lambda: [str(d) for d in dirs])


def test_disabled_discovers_nothing(sources, monkeypatch):
    monkeypatch.setattr(cl, "memory_sync_enabled", lambda: False)
    assert ms.discover_memory_dirs() == []


def test_disabled_returns_no_source_dirs_even_when_configured(monkeypatch):
    monkeypatch.setattr(cl, "memory_sync_enabled", lambda: False)
    monkeypatch.setitem(cl.cfg(), "memory_sync",
                        {"enabled": False, "claude_project_dirs": ["/somewhere"]})
    assert cl.memory_source_dirs() == []


def test_decoy_project_is_never_discovered(sources, monkeypatch):
    _enable(monkeypatch, [sources["mine"]])
    found = ms.discover_memory_dirs()
    assert found == [sources["mine"]]
    assert sources["decoy"] not in found


def test_mirror_never_copies_the_decoy(sources, monkeypatch):
    _enable(monkeypatch, [sources["mine"]])
    ms.mirror()
    written = {p.name for p in sources["target"].glob("*.md")}
    assert "Their Secret.md" not in written


# ── Parsing ──────────────────────────────────────────────────────────────────

def test_parses_flat_legacy_type(tmp_path):
    p = tmp_path / "a.md"
    p.write_text("---\nname: A\ndescription: d\ntype: feedback\n---\nbody\n",
                 encoding="utf-8")
    assert ms.parse_memory_file(p)["type"] == "feedback"


def test_parses_nested_metadata_type(tmp_path):
    p = tmp_path / "b.md"
    p.write_text("---\nname: B\nmetadata:\n  type: project\n---\nbody\n",
                 encoding="utf-8")
    assert ms.parse_memory_file(p)["type"] == "project"


def test_falls_back_to_the_filename_prefix(tmp_path):
    p = tmp_path / "feedback_naming.md"
    p.write_text("no frontmatter here\n", encoding="utf-8")
    assert ms.parse_memory_file(p)["type"] == "feedback"


def test_bare_memory_md_is_an_index(tmp_path):
    p = tmp_path / "MEMORY.md"
    p.write_text("- [A](a.md)\n", encoding="utf-8")
    assert ms.parse_memory_file(p)["type"] == "index"


def test_unreadable_file_returns_none(tmp_path):
    assert ms.parse_memory_file(tmp_path / "missing.md") is None


# ── Mirroring ────────────────────────────────────────────────────────────────

def test_index_file_is_skipped_not_mirrored(sources, monkeypatch):
    _enable(monkeypatch, [sources["mine"]])
    result = ms.mirror()
    assert result["written"] == 1
    assert not (sources["target"] / "MEMORY.md").exists()


def test_second_run_writes_nothing(sources, monkeypatch):
    _enable(monkeypatch, [sources["mine"]])
    ms.mirror()
    again = ms.mirror()
    assert again["written"] == 0


def test_dry_run_writes_nothing_at_all(sources, monkeypatch):
    _enable(monkeypatch, [sources["mine"]])
    result = ms.mirror(dry_run=True)
    assert result["written"] == 1
    assert not sources["target"].exists()


def test_wikilinks_survive_the_mirror(sources, monkeypatch):
    _enable(monkeypatch, [sources["mine"]])
    ms.mirror()
    body = (sources["target"] / "Profile.md").read_text(encoding="utf-8")
    assert "[[Acme Corp]]" in body


def test_illegal_filename_characters_are_sanitized(tmp_path, monkeypatch):
    src = tmp_path / "memory"
    src.mkdir()
    (src / "x.md").write_text('---\nname: A/B: "C"?\ntype: note\n---\nbody\n',
                              encoding="utf-8")
    target = tmp_path / "out"
    monkeypatch.setattr(cl, "memory_target_dir", lambda: target)
    _enable(monkeypatch, [src])
    ms.mirror()
    written = [p.name for p in target.glob("*.md") if p.name != "index.md"]
    assert written and not any(c in written[0] for c in '<>:"/\\|?*')


def test_name_collision_records_an_error_and_does_not_overwrite(tmp_path, monkeypatch):
    a = tmp_path / "one" / "memory"
    b = tmp_path / "two" / "memory"
    for d, text in ((a, "from one"), (b, "from two")):
        d.mkdir(parents=True)
        (d / "m.md").write_text(f"---\nname: Same\ntype: note\n---\n{text}\n",
                                encoding="utf-8")
    target = tmp_path / "out"
    monkeypatch.setattr(cl, "memory_target_dir", lambda: target)
    _enable(monkeypatch, [a, b])
    result = ms.mirror()
    assert any("collision" in e for e in result["errors"])
    assert "from one" in (target / "Same.md").read_text(encoding="utf-8")
    assert result["written"] == 2


def test_index_regeneration_preserves_hand_written_content(sources, monkeypatch):
    _enable(monkeypatch, [sources["mine"]])
    ms.mirror()
    idx = sources["target"] / "index.md"
    text = idx.read_text(encoding="utf-8")
    idx.write_text(text + "\n\nMy own note below the block.\n", encoding="utf-8")
    (sources["mine"] / "user_profile.md").write_text(
        "---\nname: Profile\ndescription: changed\ntype: user\n---\nnew body\n",
        encoding="utf-8")
    ms.mirror()
    assert "My own note below the block." in idx.read_text(encoding="utf-8")


# ── Merge proposals ──────────────────────────────────────────────────────────

def test_merge_candidates_proposes_and_never_writes(sources, monkeypatch, tmp_path):
    entities = tmp_path / "vault" / "wiki" / "entities"
    entities.mkdir(parents=True)
    (entities / "Acme Corp.md").write_text("# Acme Corp\n", encoding="utf-8")
    monkeypatch.setattr(cl, "entities_dir", lambda: entities)
    monkeypatch.setattr(cl, "projects_dir", lambda: tmp_path / "vault" / "projects")
    _enable(monkeypatch, [sources["mine"]])

    before = sorted(p.name for p in entities.iterdir())
    candidates = ms.merge_candidates()
    assert candidates and candidates[0]["target_page"].endswith("Acme Corp.md")
    assert sorted(p.name for p in entities.iterdir()) == before


def test_merge_candidates_empty_when_disabled(monkeypatch):
    monkeypatch.setattr(cl, "memory_sync_enabled", lambda: False)
    assert ms.merge_candidates() == []


# ── Credentials must not reach the vault ─────────────────────────────────────
#
# A vault is not a safe place for a key. It syncs to Dropbox or iCloud, it gets
# shared with an assistant, and unlike ~/.claude nobody treats it as sensitive.
# Chat memory can contain a credential someone pasted into a conversation, so
# the mirror has to assume it will.

import pytest as _pytest                                        # noqa: E402


@_pytest.mark.parametrize("secret", [
    "sk-ant-api03-AAAAAAAAAAAAAAAAAAAAAAAA",
    "ya29.A0ARrdaM-AAAAAAAAAAAAAAAAAAAAAAAA",
    "1//0eAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA",
    "AIzaSyAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA",
    "ghp_AAAAAAAAAAAAAAAAAAAAAAAAAAAAAA",
    "xoxb-1234567890-AAAAAAAAAAAA",
    "refresh_token: 1//0eAAAAAAAAAAAAAAAAAAAA",
    "client_secret = AAAAAAAAAAAAAAAAAAAA",
    "-----BEGIN RSA PRIVATE KEY-----",
])
def test_credential_shaped_strings_are_redacted(secret):
    out, n = ms.redact_secrets(f"the key is {secret} use it")
    assert n >= 1
    assert secret not in out


def test_ordinary_prose_is_untouched():
    text = "Met [[Acme Corp]] about the widget deal. Password protected PDF sent."
    out, n = ms.redact_secrets(text)
    assert out == text and n == 0


def test_secret_never_reaches_the_mirrored_file(tmp_path, monkeypatch):
    src = tmp_path / "memory"
    src.mkdir()
    (src / "creds.md").write_text(
        "---\nname: Creds\ntype: reference\n---\n"
        "anthropic sk-ant-api03-AAAAAAAAAAAAAAAAAAAAAAAA\n", encoding="utf-8")
    target = tmp_path / "out"
    monkeypatch.setattr(cl, "memory_target_dir", lambda: target)
    _enable(monkeypatch, [src])
    ms.mirror()
    written = (target / "Creds.md").read_text(encoding="utf-8")
    assert "sk-ant-api03" not in written
    assert "redacted" in written


def test_mirrored_files_are_written_with_unix_newlines(sources, monkeypatch):
    """The Windows idempotency bug, pinned.

    Text mode translates "\\n" to "\\r\\n" on Windows, so the bytes on disk stop
    matching the string that produced them, the content hash never matches, and
    every run rewrites every file forever. This passes trivially on macOS and
    fails loudly on the Windows CI job, which is the one that found it.
    """
    _enable(monkeypatch, [sources["mine"]])
    ms.mirror()
    for f in sources["target"].glob("*.md"):
        assert b"\r\n" not in f.read_bytes(), f"{f.name} has CRLF line endings"


@_pytest.mark.parametrize("secret", [
    # Every shape this product's own naming convention produces. An underscore
    # is a word character, so the old \b-anchored key pattern matched neither a
    # prefixed nor a suffixed variable name, and Microsoft refresh tokens went
    # into the vault in clear text.
    "MS_GRAPH_REFRESH_TOKEN_WORK=0.AXoAabcdefghijklmnopqrstuvwxyz01234567",
    "MS_GRAPH_REFRESH_TOKEN_HOME: M.C5_BAY.0.U.-Cabcdefghijklmnopqrstuvwxyz",
    "MS_GRAPH_CLIENT_SECRET_WORK=abcdefghijklmnopqrstuvwxyz012345",
    "GOOGLE_CLIENT_SECRET_HOME=GOCSPX-abcdefghijklmnopqrstuv",
    "GOOGLE_REFRESH_TOKEN_WORK=1//0gABCDEFGHIJKLMNOPQRSTUVWXYZ",
    "GRANOLA_API_KEY_MAIN=abcdefghijklmnopqrstuvwx",
    "my_access_token_v2 : abcdefghijklmnopqrst",
])
def test_suffixed_and_prefixed_credential_names_are_redacted(secret):
    out, n = ms.redact_secrets(f"note to self: {secret}")
    assert n >= 1, f"not redacted: {secret}"
    assert secret.split("=")[-1].split(": ")[-1] not in out


@_pytest.mark.parametrize("prose", [
    "The secret to the deal was patience and a good lawyer.",
    "Password protected PDF sent to the buyer.",
    "Met [[Acme Corp]] about the widget deal today.",
])
def test_ordinary_prose_survives_the_broader_pattern(prose):
    out, n = ms.redact_secrets(prose)
    assert (out, n) == (prose, 0)


@_pytest.mark.parametrize("secret", [
    # An Azure secret value routinely contains "~", which the value class
    # excluded, so the match ran four characters, fell under the length floor,
    # and the whole secret passed through.
    "MS_CLIENT_SECRET=Qm8Q~7xK9pQmR7vT2wY5zL8nD4sH6jF1gA0bE3cU",
    # The value follows a space here, not a colon, so the pattern stopped at
    # the word Bearer and judged six characters too short to be a secret.
    "Authorization: Bearer 9Xq2vLm4TbR7wYzA1cD5eF8gH0jK3lM6nP9qS2tU",
])
def test_awkwardly_shaped_secrets_are_redacted(secret):
    out, n = ms.redact_secrets(secret)
    assert n >= 1
    assert not any(len(tok) > 20 and tok in out for tok in secret.split())


def test_a_private_key_body_does_not_survive_its_header():
    pem = ("-----BEGIN RSA PRIVATE KEY-----\n"
           "MIIEpAIBAAKCAQEA3xK9pQmR7vT2wY5zL8nD4sH6jF1gA0bE\n"
           "-----END RSA PRIVATE KEY-----")
    out, n = ms.redact_secrets(pem)
    assert n >= 1
    assert "MIIEpAIBAAKCAQEA" not in out, "the header was replaced, the key was not"


def test_a_secret_in_the_frontmatter_never_reaches_the_vault(tmp_path, monkeypatch):
    """The frontmatter is its own channel. Redacting only the body let a
    secret in `name:` reach the FILENAME and the heading, and one in
    `description:` reach the index."""
    src = tmp_path / "memory"
    src.mkdir()
    secret = "xoxb-9999999999-8888888888-ZzZzZzZzZzZzZzZzZzZzZzZz"
    (src / "a.md").write_text(
        f"---\nname: key {secret}\ndescription: also {secret}\n"
        "type: reference\n---\nclean body\n", encoding="utf-8")
    target = tmp_path / "out"
    monkeypatch.setattr(cl, "memory_target_dir", lambda: target)
    _enable(monkeypatch, [src])
    ms.mirror()
    written = list(target.glob("*.md"))
    assert written
    assert not any(secret in p.name for p in written), "secret reached a filename"
    for p in written:
        assert secret not in p.read_text(encoding="utf-8")


@_pytest.mark.parametrize("secret", [
    # A pasted JSON config or API response is the likeliest way a key reaches
    # chat memory at all, and the closing quote blocked the match.
    '{"refresh_token": "Zq7Kd3Wf9Lm2Ab8Cd4Ef6GhJk5Np1Rs"}',
    '{"api_key":"Yq6Jc2Ve8Kl1Za7Bd3De5FgIj4Mo0Qr"}',
    "sk-proj-Xp5Ib1Ud7Jk0Yz6Ac2Cd4EfHi3Ln9Pq",
    "private_key=Wo4Ha0Tc6Ij9Xy5Zb1Bc3DeGh2Km8Op",
    "session_key: Vn3Gz9Sb5Hi8Wx4Ya0Ab2CdFg1Jl7No",
    "client_secret=aa%2FUm2Fy8Ra4Gh7Vw3Xz9%3D",
    "Authorization: Basic dXNlcjpUbDFFeDdRejNGZzZVdjJXeThZeg==",
    # The native formats of the notes this mirrors.
    "**API_KEY**: Sk0Dw6Pz2Ef5Tu1Vy7Wx3YzAb9Ce4Gh",
    "| API_KEY | Rj9Cv5Oy1De4St0Ux6Vw2XyZa8Bd3Fg |",
    "https://user:Qi8Bu4Nx0Cd3Rs9@api.example.com",
])
def test_awkward_paste_formats_are_redacted(secret):
    out, n = ms.redact_secrets(secret)
    assert n >= 1, f"not redacted: {secret}"
    import re as _re
    live = [t for t in _re.split(r"[\s\"'{}|,]+", secret)
            if len(t.strip(":=")) >= 18 and t.strip(":=") in out]
    assert not live, f"value survived: {live}"


def test_a_jwt_signature_does_not_survive_its_payload():
    jwt = "eyJhbGciOiJIUzI1NiIs.eyJzdWIiOiIxMjM0NTY.SflKxwRJSMeKKF2QT4fwpM"
    out, n = ms.redact_secrets(jwt)
    assert n >= 1 and "SflKxwRJSMeKKF2QT4fwpM" not in out


def test_a_secret_in_the_type_field_never_reaches_the_vault(tmp_path, monkeypatch):
    """`type:` is written straight into the mirrored YAML. It was the one
    frontmatter field still bypassing redaction after name and description
    were covered."""
    src = tmp_path / "memory"
    src.mkdir()
    secret = "sk-ant-api03-TtTtTtTtTtTtTtTtTtTtTtTt"
    (src / "a.md").write_text(
        f"---\nname: Typed\ntype: {secret}\n---\nclean body\n", encoding="utf-8")
    target = tmp_path / "out"
    monkeypatch.setattr(cl, "memory_target_dir", lambda: target)
    _enable(monkeypatch, [src])
    ms.mirror()
    for p in target.glob("*.md"):
        assert secret not in p.read_text(encoding="utf-8")


@_pytest.mark.parametrize("prose", [
    "Password policy: rotate quarterly",
    "The secret to the pitch was brevity",
    "api_key rotation is documented in the runbook",
    "The token of appreciation was a nice touch",
    "Bearer of good news arrived today",
    "We hold the private key to the partnership",
    "Basic hygiene on the deal was missing",
    "| Deal | Acme Corp |",
    "| Owner | Dana Ruiz |",
])
def test_business_prose_survives_the_widened_patterns(prose):
    # A mirror that mangles ordinary notes is broken in the other direction.
    assert ms.redact_secrets(prose) == (prose, 0)


@_pytest.mark.parametrize("secret,prose", [
    ("github_pat_11ABCDEFG0abcdefghijklmnopqrstuvwxyz",
     "We use the github_pat naming convention in the runbook"),
    ("sk_live_51ABCDEFGHIJKLMNOP", "Move to sk_live once the pilot closes"),
    ("AKIAIOSFODNN7EXAMPLE", "We use AKIA naming for the runbook"),
    ("SG.abcdefghijklmnopqrst.uvwxyz0123456789ABCD",
     "Ship SG. deliveries on Friday"),
])
def test_third_party_prefixes_redact_without_touching_prose(secret, prose):
    # These four services are not integrated here, but a person's chat memory
    # is not limited to the services their briefing tool happens to use.
    out, n = ms.redact_secrets(secret)
    assert n >= 1 and secret not in out
    assert ms.redact_secrets(prose) == (prose, 0)
