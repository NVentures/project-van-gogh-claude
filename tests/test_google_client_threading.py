"""Guard the thread-safety contract of GoogleClient.

`httplib2.Http` is not thread-safe: sharing one Http across a ThreadPoolExecutor
(as the afternoon-tea Gmail fetch does) corrupts its connection state and crashes
the interpreter (native segfault) or deadlocks. `GoogleClient.new_http()` exists
so each pooled request gets its own Http via `.execute(http=client.new_http())`.

These tests are fully offline: building credentials + AuthorizedHttp objects does
no network I/O, so a dummy refresh token is enough.
"""

import pytest


@pytest.fixture
def gclient(monkeypatch):
    monkeypatch.setenv("GOOGLE_OAUTH_CLIENT_ID", "dummy-id")
    monkeypatch.setenv("GOOGLE_OAUTH_CLIENT_SECRET", "dummy-secret")
    monkeypatch.setenv("GOOGLE_REFRESH_TOKEN_TESTLABEL", "dummy-refresh-token")
    import google_client
    return google_client.GoogleClient("TestLabel")


def test_new_http_returns_a_fresh_http_each_call(gclient):
    """Two calls must not share an httplib2.Http — that shared object is exactly
    what segfaults under concurrent use."""
    h1 = gclient.new_http()
    h2 = gclient.new_http()
    assert h1 is not h2
    # AuthorizedHttp wraps the underlying httplib2.Http as `.http`; those must
    # also be distinct instances, since the connection state lives there.
    assert h1.http is not h2.http


def test_afternoon_tea_gmail_fetch_uses_per_thread_http(gclient, monkeypatch):
    """The Gmail thread fetch must pass a distinct http into each .execute(), so
    the 8-worker pool never shares one Http. Records every http= passed and
    asserts they are all unique objects."""
    import afternoon_tea

    used_https = []

    class FakeExecutable:
        def execute(self, http=None):
            used_https.append(http)
            # Minimal thread payload the parser tolerates.
            return {"messages": []}

    class FakeThreads:
        def list(self, **kwargs):
            return _Const({"threads": [{"id": "a"}, {"id": "b"}, {"id": "c"}]})

        def get(self, **kwargs):
            return FakeExecutable()

    class FakeUsers:
        def threads(self):
            return FakeThreads()

    class FakeGmail:
        def users(self):
            return FakeUsers()

    class _Const:
        def __init__(self, val):
            self._val = val

        def execute(self, http=None):
            return self._val

    monkeypatch.setattr(type(gclient), "gmail", property(lambda self: FakeGmail()))

    afternoon_tea.fetch_gmail_sent_today(
        gclient, "primary@gmail.com", "TestLabel", "2026-07-01"
    )

    assert len(used_https) == 3, "each thread stub should trigger one .execute()"
    assert all(h is not None for h in used_https), "every request needs its own http"
    assert len({id(h) for h in used_https}) == len(used_https), "http objects must be unique"
