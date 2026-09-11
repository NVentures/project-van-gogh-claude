"""
podcast_transcribe.py — download a podcast's latest episode(s) and transcribe
them locally with Whisper, writing the transcript into the vault's
`van-gogh/podcast-inbox/` folder where the weekly /podcast-trends task reads it.

This is the "real audio" fallback for shows that don't publish a transcript
(most shows have YouTube captions or companion write-ups; audio-only shows are
the gap this fills). No cloud cost — Whisper runs on the local machine.

Contract: emits a JSON summary to stdout (and a sidecar in logs/), and writes one
markdown transcript per transcribed episode to podcast-inbox/. Idempotent: an
episode already transcribed (tracked by guid) is skipped unless --force.

    python app/podcast_transcribe.py <show-key>            # latest episode
    python app/podcast_transcribe.py <show-key> --since 14 --limit 2
    python app/podcast_transcribe.py https://feed.url/rss --model medium

Show keys come from the `podcasts.feeds` config block ({key: rss_url}); a raw
RSS URL always works without config.

faster-whisper is an OPTIONAL dependency (deliberately not in
requirements.txt — it pulls a large ML stack every install would pay for).
It is imported lazily inside transcribe() so the module imports cheaply (and
the CI import smoke-test stays green without the heavy dep loaded). It decodes
audio via bundled PyAV/ffmpeg, so no system ffmpeg is required.
"""

import argparse
import json
import os
import re
import sys
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path

# Windows blocks symlink creation without admin/Developer Mode, which makes the
# HuggingFace model cache fall over (WinError 1314). Tell hf_hub to copy blobs
# into the cache instead of symlinking — set before faster-whisper (hence
# huggingface_hub) is imported in transcribe(). Harmless on macOS/Linux.
os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS", "1")

import config_loader as C

def show_feeds() -> dict:
    """Show-key → RSS feed registry from the `podcasts.feeds` config block.

    Personal data lives in config, never in code — the plugin cache is wiped on
    every update, so a hardcoded registry could not be user-extended anyway.
    """
    return dict(C.podcast_feeds())

_UA = "Mozilla/5.0 (Project Van Gogh podcast-transcribe)"


def inbox_dir() -> Path:
    """`van-gogh/podcast-inbox/`, created on demand."""
    d = C.van_gogh_root() / "podcast-inbox"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _state_path() -> Path:
    return inbox_dir() / ".transcribe_state.json"


def load_state() -> dict:
    p = _state_path()
    if p.exists():
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return {}
    return {}


def save_state(state: dict) -> None:
    _state_path().write_text(json.dumps(state, indent=2), encoding="utf-8")


def slugify(text: str) -> str:
    s = re.sub(r"[^\w\s-]", "", (text or "").lower()).strip()
    s = re.sub(r"[\s_-]+", "-", s)
    return s[:80] or "episode"


def parse_feed(xml_text: str) -> list[dict]:
    """Parse a podcast RSS feed into a list of episode dicts (newest first).

    Each dict: title, audio_url, published (aware datetime or None), guid.
    Uses only stdlib — standard podcast RSS puts the audio in <enclosure url=…>.
    """
    root = ET.fromstring(xml_text)
    channel = root.find("channel")
    if channel is None:
        return []
    episodes = []
    for item in channel.findall("item"):
        enc = item.find("enclosure")
        audio_url = enc.get("url") if enc is not None else None
        if not audio_url:
            continue
        title_el = item.find("title")
        title = (title_el.text or "").strip() if title_el is not None else "Untitled"
        guid_el = item.find("guid")
        guid = (guid_el.text or "").strip() if guid_el is not None and guid_el.text else audio_url
        pub_el = item.find("pubDate")
        published = None
        if pub_el is not None and pub_el.text:
            try:
                published = parsedate_to_datetime(pub_el.text.strip())
                if published.tzinfo is None:
                    published = published.replace(tzinfo=timezone.utc)
            except (TypeError, ValueError):
                published = None
        episodes.append(
            {"title": title, "audio_url": audio_url, "published": published, "guid": guid}
        )
    episodes.sort(key=lambda e: e["published"] or datetime.min.replace(tzinfo=timezone.utc), reverse=True)
    return episodes


def pick_recent(episodes: list[dict], since_days: int, limit: int) -> list[dict]:
    """The newest `limit` episodes published within `since_days` of now."""
    cutoff = datetime.now(timezone.utc).timestamp() - since_days * 86400
    recent = [
        e for e in episodes
        if e["published"] is None or e["published"].timestamp() >= cutoff
    ]
    return recent[:limit]


def select_episodes(episodes: list[dict], match: str, since_days: int, limit: int) -> list[dict]:
    """Choose which episodes to transcribe.

    With `match`, pick episodes whose title contains the substring (case-
    insensitive), ignoring the recency window — for targeting a specific older
    episode. Without it, fall back to the newest within `since_days`.
    """
    if match:
        m = match.lower()
        return [e for e in episodes if m in e["title"].lower()][:limit]
    return pick_recent(episodes, since_days, limit)


def _require_http_url(url: str) -> str:
    """Reject any URL that isn't http(s).

    The feed URL is user-supplied, but enclosure/audio URLs come from the remote
    feed body (third-party controlled). Without this guard a compromised feed
    could hand back `file://…/.env` (exfiltrating a local secret into the audio
    tmp and the transcript frontmatter) or a localhost URL (SSRF).
    """
    scheme = urllib.parse.urlparse(url).scheme.lower()
    if scheme not in ("http", "https"):
        raise ValueError(f"refusing non-http(s) URL from feed: {url[:80]}")
    return url


def fetch(url: str) -> bytes:
    req = urllib.request.Request(_require_http_url(url), headers={"User-Agent": _UA})
    with urllib.request.urlopen(req, timeout=120) as resp:  # noqa: S310 (scheme-checked)
        return resp.read()


def download_audio(url: str, dest: Path) -> Path:
    req = urllib.request.Request(_require_http_url(url), headers={"User-Agent": _UA})
    with urllib.request.urlopen(req, timeout=300) as resp, open(dest, "wb") as f:  # noqa: S310
        while True:
            chunk = resp.read(1 << 16)
            if not chunk:
                break
            f.write(chunk)
    return dest


def transcribe(audio_path: Path, model_size: str, device: str, compute_type: str) -> str:
    """Transcribe an audio file to text via faster-whisper (lazy-imported)."""
    try:
        from faster_whisper import WhisperModel
    except ImportError as e:  # pragma: no cover - environment guard
        raise SystemExit(
            "faster-whisper is not installed (optional dependency). Install it "
            "into the Van Gogh venv:\n"
            '  macOS/Linux:  "$HOME/.config/van-gogh/venv/bin/python" -m pip install faster-whisper\n'
            '  Windows:      & "$HOME\\.config\\van-gogh\\venv\\Scripts\\python.exe" -m pip install faster-whisper'
        ) from e
    model = WhisperModel(model_size, device=device, compute_type=compute_type)
    segments, _info = model.transcribe(str(audio_path), vad_filter=True)
    return " ".join(seg.text.strip() for seg in segments).strip()


def write_transcript(show: str, ep: dict, text: str) -> Path:
    date_str = ep["published"].strftime("%Y-%m-%d") if ep["published"] else "undated"
    out = inbox_dir() / f"{slugify(show)}-{date_str}-{slugify(ep['title'])}.md"
    # Quote feed-controlled values (title, audio_url, show) as JSON — a valid
    # YAML flow scalar — so a title containing a newline or ": " can't break out
    # of the frontmatter and inject keys into a note downstream skills parse.
    front = (
        "---\n"
        f"show: {json.dumps(show)}\n"
        f"title: {json.dumps(ep['title'])}\n"
        f"date: {date_str}\n"
        f"source: {json.dumps(ep['audio_url'])}\n"
        "transcribed_by: whisper\n"
        "---\n\n"
    )
    out.write_text(front + text + "\n", encoding="utf-8")
    return out


def resolve_feed(feed_arg: str) -> str:
    feeds = {k.lower(): v for k, v in show_feeds().items()}
    return feeds.get(feed_arg.lower(), feed_arg)


def main() -> int:
    C.force_utf8_io()
    ap = argparse.ArgumentParser(description="Download + locally transcribe a podcast's latest episodes.")
    ap.add_argument("feed", help="Show key (e.g. 'currents') or an RSS feed URL.")
    ap.add_argument("--since", type=int, default=10, help="Only episodes published within N days (default 10). Ignored when --match is set.")
    ap.add_argument("--limit", type=int, default=1, help="Max episodes to transcribe (default 1).")
    ap.add_argument("--match", default="", help="Transcribe episodes whose title contains this substring (case-insensitive), ignoring the recency window. Targets a specific older episode.")
    ap.add_argument("--model", default="small", help="Whisper model size: tiny|base|small|medium|large-v3 (default small).")
    ap.add_argument("--device", default="cpu", help="cpu (default) or cuda.")
    ap.add_argument("--compute-type", default="int8", help="faster-whisper compute type (default int8 for CPU).")
    ap.add_argument("--force", action="store_true", help="Re-transcribe even if already done.")
    args = ap.parse_args()

    feed_url = resolve_feed(args.feed)
    # `show` labels the transcript/filename; use the lowercased key when the arg
    # resolved to a configured feed, else the raw arg (a URL).
    show = args.feed.lower() if feed_url != args.feed else args.feed

    episodes = parse_feed(fetch(feed_url).decode("utf-8", errors="replace"))
    candidates = select_episodes(episodes, args.match, args.since, args.limit)

    state = load_state()
    done_guids = set(state.get("transcribed", []))

    results = []
    cache = C.logs_dir()
    cache.mkdir(parents=True, exist_ok=True)
    for ep in candidates:
        if ep["guid"] in done_guids and not args.force:
            results.append({"title": ep["title"], "status": "skipped (already transcribed)"})
            continue
        audio_tmp = cache / f"_podcast_{slugify(ep['title'])}.mp3"
        try:
            download_audio(ep["audio_url"], audio_tmp)
            text = transcribe(audio_tmp, args.model, args.device, args.compute_type)
            out = write_transcript(show, ep, text)
            done_guids.add(ep["guid"])
            results.append({
                "title": ep["title"],
                "status": "transcribed",
                "transcript_path": str(out),
                "chars": len(text),
            })
        except SystemExit:
            # Missing faster-whisper: nothing will transcribe — let it surface.
            raise
        except Exception as e:
            # One bad episode (download/transcribe error) must not abort the run
            # or discard guids already transcribed this run.
            results.append({"title": ep["title"], "status": f"failed: {e}"})
        finally:
            if audio_tmp.exists():
                audio_tmp.unlink()
            # Persist after every episode: a later failure can't lose the guids
            # of episodes already transcribed (re-transcription is expensive).
            state["transcribed"] = sorted(done_guids)
            save_state(state)

    summary = {
        "show": show,
        "feed_url": feed_url,
        "inbox_dir": str(inbox_dir()),
        "model": args.model,
        "episodes": results,
    }
    sidecar = C.logs_dir() / "podcast_transcribe_latest.json"
    sidecar.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
