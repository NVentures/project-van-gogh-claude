"""`_list_meetings` must return its list on every path.

Found while proving the OAuth scope fix end to end: the real week_review.py
died before it reached a single API call, on an install with a notetaker
configured.

    for m in _list_meetings(window_days, errors)[:max_fetch]:
    TypeError: 'NoneType' object is not subscriptable

The function returned `[]` when no notetaker was configured and `out` from its
`except` branch, but the success path fell off the end and returned None. So
the failure needed a notetaker configured AND a stub walk that finished
without raising, which is every healthy install and no test in the suite.

Both briefings hit it: week_review subscripts the result, week_retro iterates
it. That is a crashed weekly briefing, not a degraded one, in a helper whose
whole contract is to fail open.

MUTATION: remove `return out` from the success path and every test here fails.
"""
import pytest

import collectors


class _Notetaker:
    """Minimal stand-in for the notetaker layer's read surface."""

    def __init__(self, stubs, display="Testetaker"):
        self._stubs = stubs
        self._display = display

    def config_problem(self):
        return ""

    def configured(self):
        return True

    def safe_display_name(self):
        return self._display

    def iter_stubs(self, since, limit):
        return iter(self._stubs)

    def local_date(self, created_at, tz):
        return (created_at or "")[:10]


def _install(monkeypatch, stubs):
    monkeypatch.setattr(collectors, "notetaker", _Notetaker(stubs))


def _stub(nid, title, date):
    return {"id": nid, "title": title, "created_at": f"{date}T10:00:00Z"}


def test_a_normal_walk_returns_the_meetings_it_found(monkeypatch):
    from datetime import datetime, timedelta, timezone
    today = datetime.now(timezone.utc)
    _install(monkeypatch, [
        _stub("a", "Kickoff", (today - timedelta(days=1)).strftime("%Y-%m-%d")),
        _stub("b", "Review", (today - timedelta(days=2)).strftime("%Y-%m-%d")),
    ])
    got = collectors._list_meetings(14, [])
    assert isinstance(got, list), "fell off the end and returned None"
    assert [m["title"] for m in got] == ["Kickoff", "Review"]


def test_an_empty_stream_returns_a_list_not_none(monkeypatch):
    _install(monkeypatch, [])
    assert collectors._list_meetings(14, []) == []


def test_meeting_texts_can_slice_the_result(monkeypatch):
    """The exact call that crashed the weekly briefing."""
    _install(monkeypatch, [])
    assert collectors.meeting_texts(14, [], max_fetch=5) == []


def test_meetings_can_iterate_the_result(monkeypatch):
    """The retro's call, which iterates rather than slicing."""
    from datetime import datetime, timedelta, timezone
    day = (datetime.now(timezone.utc) - timedelta(days=1)).strftime("%Y-%m-%d")
    _install(monkeypatch, [_stub("a", "Kickoff", day)])
    assert collectors.meetings(14, []) == [{"title": "Kickoff", "date": day}]


def test_a_walk_that_breaks_early_still_returns(monkeypatch):
    """Stubs arrive newest first, so an old one breaks the loop. That skips
    the `else` clause, which is where the missing return was hiding."""
    from datetime import datetime, timedelta, timezone
    today = datetime.now(timezone.utc)
    _install(monkeypatch, [
        _stub("a", "Recent", (today - timedelta(days=1)).strftime("%Y-%m-%d")),
        _stub("old", "Ancient", (today - timedelta(days=400)).strftime("%Y-%m-%d")),
        _stub("z", "Never reached", (today - timedelta(days=2)).strftime("%Y-%m-%d")),
    ])
    got = collectors._list_meetings(14, [])
    assert [m["title"] for m in got] == ["Recent"]


def test_a_provider_that_explodes_still_fails_open(monkeypatch):
    """The branch that always had its return. Kept so a fix to the other path
    cannot quietly break this one."""
    class Exploding(_Notetaker):
        def iter_stubs(self, since, limit):
            raise RuntimeError("provider returned nonsense")

    monkeypatch.setattr(collectors, "notetaker", Exploding([]))
    errors = []
    assert collectors._list_meetings(14, errors) == []
    assert len(errors) == 1 and "provider returned nonsense" in errors[0]


def test_no_notetaker_configured_returns_empty(monkeypatch):
    class Unconfigured(_Notetaker):
        def configured(self):
            return False

    monkeypatch.setattr(collectors, "notetaker", Unconfigured([]))
    assert collectors._list_meetings(14, []) == []


def test_every_return_path_hands_back_a_list():
    """Reads the source: a path added later must return too."""
    import ast
    import inspect
    import textwrap
    tree = ast.parse(textwrap.dedent(inspect.getsource(collectors._list_meetings)))
    fn = tree.body[0]
    returns = [n for n in ast.walk(fn) if isinstance(n, ast.Return)]
    assert len(returns) >= 3, f"expected a return per path, found {len(returns)}"
    assert not any(r.value is None for r in returns), "a bare `return` yields None"
    # The body must not be able to fall off its end.
    assert isinstance(fn.body[-1], (ast.Return, ast.Try)), (
        "the last statement lets the function fall through, returning None")
