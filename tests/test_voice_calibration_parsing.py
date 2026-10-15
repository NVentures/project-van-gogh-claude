"""Parsing tests for cos_voice_calibration email-body helpers.

The migrated `fetch_sent_emails` now calls
`gclient.gmail.users().messages().get(format="full")`, which returns the native
Gmail payload shape these helpers parse. These tests pin that contract.

conftest.py primes config_loader and puts app/ on sys.path.
"""
import base64

import cos_voice_calibration as vc


def _b64(text: str) -> str:
    return base64.urlsafe_b64encode(text.encode("utf-8")).decode("ascii").rstrip("=")


def test_extract_body_plain_text():
    payload = {"mimeType": "text/plain", "body": {"data": _b64("Hello there, let's sync.")}}
    assert vc.extract_body(payload) == "Hello there, let's sync."


def test_extract_body_multipart_recurses():
    payload = {
        "mimeType": "multipart/alternative",
        "parts": [
            {"mimeType": "text/html", "body": {"data": _b64("<p>ignored</p>")}},
            {"mimeType": "text/plain", "body": {"data": _b64("the real body")}},
        ],
    }
    assert vc.extract_body(payload) == "the real body"


def test_strip_quoted_reply_removes_chain():
    body = "Sounds good, shipping today.\nOn Mon, May 26, 2026 at 9:00 AM Someone wrote:\n> old text"
    assert vc.strip_quoted_reply(body) == "Sounds good, shipping today."


def test_strip_quoted_reply_drops_gt_lines():
    assert vc.strip_quoted_reply("keep this\n> quoted\nand this") == "keep this\nand this"


def test_is_noise_flags_warmup_and_calendar():
    assert vc.is_noise("[lemwarmup] ping", "x@y.com") is True
    assert vc.is_noise("Accepted: Lunch", "x@y.com") is True
    assert vc.is_noise("Real subject", "real@client.com") is False
