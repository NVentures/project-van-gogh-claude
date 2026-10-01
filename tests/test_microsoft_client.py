"""Unit tests for the MicrosoftClient.patch()/delete() Graph methods.

delete() treats 404 as idempotent success (the resource is already gone) but
must still raise on any other error — the branch a refactor silently breaks.
Fully offline: requests is monkeypatched, no client construction/auth.
"""
import pytest

import microsoft_client as mc


def _client(monkeypatch):
    client = object.__new__(mc.MicrosoftClient)
    monkeypatch.setattr(type(client), "_headers", lambda self: {}, raising=False)
    return client


class _Resp:
    def __init__(self, status_code, text="", payload=None):
        self.status_code = status_code
        self.text = text
        self._payload = payload or {}

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def json(self):
        return self._payload


def test_delete_is_idempotent_on_404(monkeypatch):
    monkeypatch.setattr(mc.requests, "delete", lambda *a, **k: _Resp(404))
    _client(monkeypatch).delete("/me/events/gone")  # must not raise


def test_delete_raises_on_other_errors(monkeypatch):
    monkeypatch.setattr(mc.requests, "delete", lambda *a, **k: _Resp(500))
    with pytest.raises(RuntimeError):
        _client(monkeypatch).delete("/me/events/x")


def test_patch_sends_json_and_returns_body(monkeypatch):
    seen = {}

    def fake_patch(url, headers=None, json=None, timeout=None):
        seen["url"] = url
        seen["json"] = json
        seen["ctype"] = headers.get("Content-Type")
        return _Resp(200, text="{}", payload={"ok": True})

    monkeypatch.setattr(mc.requests, "patch", fake_patch)
    out = _client(monkeypatch).patch("/me/messages/1", {"isRead": True})
    assert out == {"ok": True}
    assert seen["json"] == {"isRead": True}
    assert seen["ctype"] == "application/json"


def test_patch_empty_body_returns_empty_dict(monkeypatch):
    monkeypatch.setattr(mc.requests, "patch", lambda *a, **k: _Resp(200, text=""))
    assert _client(monkeypatch).patch("/me/messages/1", {"x": 1}) == {}
