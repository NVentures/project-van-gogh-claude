"""Tests for podcast_transcribe pure helpers (no network, no Whisper)."""

from datetime import datetime, timedelta, timezone

import podcast_transcribe as pt

SAMPLE_FEED = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0">
  <channel>
    <title>Currents</title>
    <item>
      <title>Ep351: Challenges and Opportunities in Distributed Solar</title>
      <enclosure url="https://api.spreaker.com/download/episode/72334361/ep351.mp3" type="audio/mpeg"/>
      <pubDate>Thu, 04 Jun 2026 12:00:00 +0000</pubDate>
      <guid>ep351-guid</guid>
    </item>
    <item>
      <title>Ep350: How Lenders Manage Credit Risk with Insurance</title>
      <enclosure url="https://api.spreaker.com/download/episode/72198077/ep350.mp3" type="audio/mpeg"/>
      <pubDate>Thu, 28 May 2026 12:00:00 +0000</pubDate>
      <guid>ep350-guid</guid>
    </item>
    <item>
      <title>Item with no audio</title>
      <pubDate>Thu, 21 May 2026 12:00:00 +0000</pubDate>
    </item>
  </channel>
</rss>"""


def test_parse_feed_extracts_episodes_with_audio_only():
    eps = pt.parse_feed(SAMPLE_FEED)
    assert len(eps) == 2  # the item with no enclosure is dropped
    assert eps[0]["title"].startswith("Ep351")
    assert eps[0]["audio_url"].endswith("ep351.mp3")
    assert eps[0]["guid"] == "ep351-guid"
    assert eps[0]["published"].tzinfo is not None


def test_parse_feed_sorts_newest_first():
    eps = pt.parse_feed(SAMPLE_FEED)
    assert eps[0]["published"] > eps[1]["published"]


def test_pick_recent_respects_window_and_limit():
    eps = pt.parse_feed(SAMPLE_FEED)
    # Both sample episodes are dated June 2026; with a huge window we still cap at limit.
    assert len(pt.pick_recent(eps, since_days=100000, limit=1)) == 1
    assert len(pt.pick_recent(eps, since_days=100000, limit=5)) == 2


def test_pick_recent_filters_old_episodes():
    now = datetime.now(timezone.utc)
    eps = [
        {"title": "fresh", "audio_url": "a", "guid": "a", "published": now - timedelta(days=2)},
        {"title": "stale", "audio_url": "b", "guid": "b", "published": now - timedelta(days=40)},
    ]
    picked = pt.pick_recent(eps, since_days=10, limit=10)
    assert [e["title"] for e in picked] == ["fresh"]


def test_select_episodes_match_targets_specific_title_ignoring_window():
    eps = pt.parse_feed(SAMPLE_FEED)  # both episodes dated June 2026
    # --match finds an older/specific episode regardless of the recency window.
    picked = pt.select_episodes(eps, match="ep350", since_days=1, limit=5)
    assert len(picked) == 1
    assert picked[0]["title"].startswith("Ep350")


def test_select_episodes_no_match_falls_back_to_recency():
    eps = pt.parse_feed(SAMPLE_FEED)
    picked = pt.select_episodes(eps, match="", since_days=100000, limit=1)
    assert len(picked) == 1
    assert picked[0]["title"].startswith("Ep351")  # newest


def test_slugify():
    assert pt.slugify("Ep351: Distributed Solar!") == "ep351-distributed-solar"
    assert pt.slugify("") == "episode"


def test_resolve_feed_known_key_and_passthrough():
    import config_loader
    saved = config_loader._config
    try:
        config_loader._config = {
            **saved,
            "podcasts": {"feeds": {"myshow": "https://feeds.example.com/myshow.rss"}},
        }
        assert pt.resolve_feed("myshow") == "https://feeds.example.com/myshow.rss"
        assert pt.resolve_feed("MyShow") == "https://feeds.example.com/myshow.rss"
        assert pt.resolve_feed("https://example.com/rss") == "https://example.com/rss"
    finally:
        config_loader._config = saved


def test_resolve_feed_url_passthrough_with_no_config_block():
    # No podcasts block configured -> any argument passes through as a URL.
    assert pt.resolve_feed("https://example.com/rss") == "https://example.com/rss"
