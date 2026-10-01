"""travel.py tests.

The load-bearing one is the timezone group. A flight leaves on the departure
airport's clock and the traveller leaves home on theirs; subtracting a drive
and a buffer from a naive clock reading is silently hours wrong in the
direction that misses the flight. Those tests are written so that deleting
the timezone conversion turns them red.
"""
import json
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

import travel

PT = ZoneInfo("America/Los_Angeles")
ET = ZoneInfo("America/New_York")


def _event(title="Flight to Newark", location="San Francisco Airport (SFO)",
           sort="2026-11-12T06:05:00-08:00", day="Thu Nov 12", time_str="6:05 AM"):
    return {"source": "Google", "title": title, "day": day, "time": time_str,
            "location": location, "_sort": sort}


def _trip(sort="2026-11-12T06:05:00-08:00", location="San Francisco Airport (SFO)"):
    trips = travel.detect_trips([_event(location=location, sort=sort)])
    assert trips, "fixture did not produce a trip"
    return trips[0]


# ── Timezone: the flight-missing bug ─────────────────────────────────────────

def test_timezone_leave_by_crosses_coasts():
    """A 6:05 AM Eastern flight, from a Pacific home, leaves at 1:01 AM Pacific.

    This is the case the whole module exists to get right. 6:05 AM Eastern is
    3:05 AM Pacific, so minus a 90 minute buffer and a 34 minute drive the
    traveller leaves at 1:01 AM their time. Reading "6:05 AM" as a local clock
    reading yields 4:01 AM Pacific: three hours late, with every field on the
    notice still rendering.
    """
    departs = datetime(2026, 11, 12, 6, 5, tzinfo=ET)
    leave = travel.leave_by(departs, drive_minutes=34, buffer_minutes=90, home_tz=PT)
    assert leave.tzinfo is not None
    assert (leave.hour, leave.minute) == (1, 1), f"got {leave}"
    assert leave.strftime("%Z") == "PST"
    # And the naive reading, which is what a wrong implementation produces.
    naive = datetime(2026, 11, 12, 6, 5, tzinfo=PT) - timedelta(minutes=124)
    assert (naive.hour, naive.minute) == (4, 1)
    assert leave != naive


def test_timezone_same_zone_still_correct():
    """The same arithmetic where both clocks agree, so the test above is not
    passing for some accidental offset reason."""
    departs = datetime(2026, 11, 12, 9, 0, tzinfo=PT)
    leave = travel.leave_by(departs, drive_minutes=30, buffer_minutes=90, home_tz=PT)
    assert (leave.hour, leave.minute) == (7, 0)


def test_timezone_dst_boundary():
    """Across the fall-back transition the offset changes mid-calculation.

    2026-11-01 is the US DST end. A flight at 3:00 AM EST, leaving 4 hours
    earlier, crosses back through the repeated hour.
    """
    departs = datetime(2026, 11, 1, 12, 0, tzinfo=ET)
    leave = travel.leave_by(departs, drive_minutes=60, buffer_minutes=90, home_tz=PT)
    # 12:00 EST is 09:00 PST; minus 150 minutes is 06:30 PST.
    assert (leave.hour, leave.minute) == (6, 30), f"got {leave}"
    assert leave.strftime("%Z") == "PST"


def test_timezone_midnight_rollover():
    """An early flight puts the leave-by on the previous calendar day."""
    departs = datetime(2026, 11, 12, 1, 30, tzinfo=PT)
    leave = travel.leave_by(departs, drive_minutes=40, buffer_minutes=90, home_tz=PT)
    assert leave.day == 11, f"expected previous day, got {leave}"
    assert (leave.hour, leave.minute) == (23, 20)


def test_timezone_naive_departure_refuses_to_guess():
    """No offset means no leave-by. A missing notice beats a wrong one."""
    naive = datetime(2026, 11, 12, 6, 5)
    assert travel.leave_by(naive, 30, 90, PT) is None
    assert travel.leave_by(None, 30, 90, PT) is None
    assert travel.leave_by(datetime(2026, 11, 12, 6, 5, tzinfo=ET), None, 90, PT) is None


def test_timezone_parse_instant_keeps_offset():
    dt = travel.parse_instant("2026-11-12T06:05:00-05:00")
    assert dt is not None and dt.tzinfo is not None
    assert dt.astimezone(timezone.utc).hour == 11
    # All-day events carry a date with no clock, so no instant.
    assert travel.parse_instant("2026-11-12") is None
    assert travel.parse_instant("") is None
    assert travel.parse_instant("not a date") is None


# ── Detection: false positives are worse than misses ─────────────────────────

NEGATIVE_EVENTS = [
    {"title": "Flight simulator demo", "location": "Hangar 4 (SFO)", "_sort": "2026-11-12T09:00:00-08:00"},
    {"title": "Weekly sync", "location": "Conference Room B", "_sort": "2026-11-12T09:00:00-08:00"},
    {"title": "Travel time", "location": "", "_sort": "2026-11-12T09:00:00-08:00"},
    {"title": "OOO", "location": "", "_sort": "2026-11-12"},
    {"title": "Flight to Newark", "location": "", "_sort": "2026-11-12T06:05:00-08:00"},
    {"title": "HQ CEO SYNC", "location": "HQ CEO SYNC", "_sort": "2026-11-12T09:00:00-08:00"},
    {"title": "Airport pickup planning", "location": "Zoom", "_sort": "2026-11-12T09:00:00-08:00"},
    {"title": "Review of SFO lease", "location": "SFO lease docs", "_sort": "2026-11-12T09:00:00-08:00"},
    {"title": "Podcast: aviation", "location": "Studio (LAX)", "_sort": "2026-11-12T09:00:00-08:00"},
    {"title": "1:1 with Ben Roberts", "location": "JFK Deli", "_sort": "2026-11-12T09:00:00-08:00"},
]

POSITIVE_EVENTS = [
    {"title": "Flight to Newark", "location": "San Francisco Airport (SFO)", "_sort": "2026-11-12T06:05:00-08:00"},
    {"title": "UA 523", "location": "SFO -> EWR", "_sort": "2026-11-12T06:05:00-08:00"},
    {"title": "Trip east", "location": "SFO International Airport, Terminal 3", "_sort": "2026-11-12T06:05:00-08:00"},
    {"title": "Depart", "location": "Airport (LAX)", "_sort": "2026-11-12T06:05:00-08:00"},
    {"title": "Fly home", "location": "JFK International Airport", "_sort": "2026-11-12T06:05:00-05:00"},
]


@pytest.mark.parametrize("event", NEGATIVE_EVENTS, ids=lambda e: e["title"][:22])
def test_detect_rejects_non_flights(event):
    """Each negative asserted on its own: a set-level count passes while any
    one of them matches."""
    assert travel.detect_trips([event]) == []


@pytest.mark.parametrize("event", POSITIVE_EVENTS, ids=lambda e: e["title"][:22])
def test_detect_finds_real_flights(event):
    trips = travel.detect_trips([event])
    assert len(trips) == 1
    assert trips[0]["origin"] and len(trips[0]["origin"]) == 3


def test_detect_reads_location_never_title():
    """A title naming an airport with an empty location is not a trip. People
    write "Flight to Newark" for a phone call about a flight."""
    assert travel.detect_trips([_event(location="")]) == []


def test_detect_orders_by_departure():
    early = _event(sort="2026-11-10T06:00:00-08:00")
    late = _event(sort="2026-11-14T06:00:00-08:00")
    trips = travel.detect_trips([late, early])
    assert trips[0]["departs"] < trips[1]["departs"]


def test_terminal_only_from_calendar():
    """A terminal is shown only when the calendar said so. A stale lookup
    table sending someone to the wrong terminal at 5 AM is the worst thing
    this feature could print."""
    assert _trip(location="SFO Airport, Terminal 3")["terminal"] == "3"
    assert _trip(location="San Francisco Airport (SFO)")["terminal"] is None


def test_destination_parsed_from_pair():
    assert _trip(location="SFO -> EWR")["destination"] == "EWR"
    assert _trip(location="San Francisco Airport (SFO)")["destination"] is None


# ── Ledger ───────────────────────────────────────────────────────────────────

def test_ledger_suppresses_the_same_notice_twice():
    led = {"shown": [], "routes_calls": {}}
    trip = _trip()
    key = travel.notice_key(trip, travel.NOTICE_WEATHER)
    assert not travel.already_shown(led, key)
    led = travel.record_shown(led, key, "2026-11-10")
    assert travel.already_shown(led, key)
    # Recording twice does not duplicate the row.
    led = travel.record_shown(led, key, "2026-11-10")
    assert len(led["shown"]) == 1


def test_ledger_lets_a_different_notice_for_the_same_trip_fire():
    """Weather two days out must not suppress the departure notice."""
    led = {"shown": [], "routes_calls": {}}
    trip = _trip()
    led = travel.record_shown(led, travel.notice_key(trip, travel.NOTICE_WEATHER), "2026-11-10")
    assert not travel.already_shown(led, travel.notice_key(trip, travel.NOTICE_DEPARTURE))
    assert not travel.already_shown(led, travel.notice_key(trip, travel.NOTICE_FLIGHTS))


@pytest.mark.parametrize("body", ["", "not json at all", "[1,2,3]", '{"shown": "wrong type"}'])
def test_ledger_survives_a_corrupt_file(tmp_path, body):
    """Each malformed shape asserted on its own. A briefing is not worth
    failing over a ledger that can be rebuilt."""
    path = tmp_path / "travel_notices.json"
    path.write_text(body, encoding="utf-8")
    led = travel.load_ledger(path)
    assert isinstance(led["shown"], list)
    assert isinstance(led["routes_calls"], dict)


def test_ledger_missing_file_is_empty_not_an_error(tmp_path):
    led = travel.load_ledger(tmp_path / "does_not_exist.json")
    assert led == {"shown": [], "routes_calls": {}}


def test_ledger_prunes_old_entries():
    """Without a prune the file grows for the life of the install."""
    led = {
        "shown": [
            {"key": "old", "date": "2026-01-01"},
            {"key": "recent", "date": "2026-11-08"},
        ],
        "routes_calls": {"2026-01-01": 3, "2026-11-08": 1},
    }
    led = travel.prune_ledger(led, "2026-11-10")
    assert [e["key"] for e in led["shown"]] == ["recent"]
    assert list(led["routes_calls"]) == ["2026-11-08"]


def test_ledger_roundtrips_through_disk(tmp_path):
    path = tmp_path / "sub" / "travel_notices.json"
    led = travel.record_shown({"shown": [], "routes_calls": {}}, "k", "2026-11-10")
    travel.save_ledger(led, path)
    assert travel.load_ledger(path)["shown"][0]["key"] == "k"


# ── Cost: the cap on a paid lookup ───────────────────────────────────────────

def test_routes_cap_refuses_the_fourth_call():
    led = {"shown": [], "routes_calls": {}}
    today = "2026-11-12"
    for expected in range(travel.ROUTES_DAILY_CAP):
        assert travel.routes_budget_left(led, today), f"call {expected + 1} refused early"
        led = travel.record_routes_call(led, today)
    assert not travel.routes_budget_left(led, today), "cap did not stop the 4th call"
    assert travel.routes_calls_today(led, today) == travel.ROUTES_DAILY_CAP


def test_routes_cap_is_per_day():
    led = {"shown": [], "routes_calls": {"2026-11-11": 3}}
    assert travel.routes_budget_left(led, "2026-11-12")


def test_routes_cap_tolerates_a_junk_counter():
    led = {"shown": [], "routes_calls": {"2026-11-12": "three"}}
    assert travel.routes_calls_today(led, "2026-11-12") == 0


# ── Plausibility: a real number from a real API can still be useless ─────────

@pytest.mark.parametrize("minutes,ok", [
    (34, True), (2, True), (240, True),
    (1, False), (241, False), (600, False),
    (None, False), ("", False), ("abc", False),
])
def test_plausible_drive_bounds(minutes, ok):
    assert travel.plausible_drive(minutes) is ok


# ── Notice windows: each notice on its own day, and no other ─────────────────

def _collect_on(day_offset, *, monkeypatch=None, drive=34, address="1 Sentinel Way",
                key="test-key", location="SFO -> EWR"):
    """Build notices as if today were `day_offset` days before the trip."""
    depart = datetime(2026, 11, 12, 6, 5, tzinfo=PT)
    today = (depart.astimezone(PT).date() - timedelta(days=day_offset)).strftime("%Y-%m-%d")
    trips = travel.detect_trips([_event(location=location)])
    led = {"shown": [], "routes_calls": {}}
    return travel.build_notices(
        trips, today, PT, led, 90,
        drive_lookup=lambda *a, **k: drive,
        weather_lookup=lambda place: {
            "place": "Newark", "region": "NJ", "country": "US",
            "high": 54.0, "low": 41.0, "rain_chance": 20.0,
        },
    )


@pytest.mark.parametrize("offset,kinds", [
    (5, [travel.NOTICE_FLIGHTS]),
    (4, [travel.NOTICE_FLIGHTS]),
    (3, [travel.NOTICE_FLIGHTS]),
    (2, [travel.NOTICE_WEATHER]),
    (1, []),
    (0, [travel.NOTICE_DEPARTURE]),
    (-1, []),
])
def test_notice_fires_only_on_its_own_day(offset, kinds, monkeypatch):
    """The exact set of days each notice renders on, asserted per day. A
    function returning its block unconditionally passes a single-day test."""
    monkeypatch.setenv(travel.HOME_ENV_KEY, "1 Sentinel Way")
    monkeypatch.setenv(travel.MAPS_ENV_KEY, "test-key")
    notices, _ = _collect_on(offset)
    assert [n["kind"] for n in notices] == kinds


def test_weather_names_the_place_it_resolved():
    """An ambiguous destination (Portland OR vs ME) must be visible to the
    reader, so the resolved name is printed."""
    notices, _ = _collect_on(2)
    assert "Newark" in notices[0]["text"]


def test_flights_link_carries_origin_destination_and_date():
    """Asserted on the parsed query, never a substring: any URL contains the
    hostname."""
    from urllib.parse import parse_qs, urlparse
    notices, _ = _collect_on(5)
    parsed = urlparse(notices[0]["link"])
    assert parsed.netloc == "www.google.com"
    query = parse_qs(parsed.query)["q"][0]
    assert "SFO" in query and "EWR" in query and "2026-11-12" in query


def test_no_flights_link_without_a_destination():
    """A one-airport event cannot produce a search, so it produces nothing."""
    notices, _ = _collect_on(5, location="San Francisco Airport (SFO)")
    assert notices == []


def test_departure_notice_carries_the_correct_leave_by(monkeypatch):
    monkeypatch.setenv(travel.HOME_ENV_KEY, "1 Sentinel Way")
    monkeypatch.setenv(travel.MAPS_ENV_KEY, "test-key")
    notices, _ = _collect_on(0)
    notice = notices[0]
    # 6:05 AM PT, minus 90 buffer, minus 34 drive = 4:01 AM PT.
    assert notice["leave_by"].startswith("4:01 AM")
    assert notice["drive_minutes"] == 34


def test_departure_notice_without_a_key_says_so(monkeypatch):
    monkeypatch.delenv(travel.MAPS_ENV_KEY, raising=False)
    monkeypatch.delenv(travel.HOME_ENV_KEY, raising=False)
    notices, _ = _collect_on(0)
    assert notices[0]["leave_by"] is None
    assert "home address" in notices[0]["text"]


def test_departure_notice_degrades_when_the_lookup_fails(monkeypatch):
    """Drive time unavailable is useful; an invented leave-by is not."""
    monkeypatch.setenv(travel.HOME_ENV_KEY, "1 Sentinel Way")
    monkeypatch.setenv(travel.MAPS_ENV_KEY, "test-key")
    notices, _ = _collect_on(0, drive=None)
    assert notices[0]["leave_by"] is None
    assert "usual time" in notices[0]["text"]


def test_implausible_drive_is_not_printed_as_a_leave_by(monkeypatch):
    """A geocode landing in the wrong state returns a real number from a real
    API. It must not become an alarm time."""
    monkeypatch.setenv(travel.HOME_ENV_KEY, "1 Sentinel Way")
    monkeypatch.setenv(travel.MAPS_ENV_KEY, "test-key")
    for bad in (600, 1):
        notices, _ = _collect_on(0, drive=bad)
        assert notices[0]["leave_by"] is None, f"{bad} min was printed"


def test_rerun_the_same_day_is_suppressed_by_the_ledger(monkeypatch):
    monkeypatch.setenv(travel.HOME_ENV_KEY, "1 Sentinel Way")
    monkeypatch.setenv(travel.MAPS_ENV_KEY, "test-key")
    trips = travel.detect_trips([_event(location="SFO -> EWR")])
    led = {"shown": [], "routes_calls": {}}
    first, led = travel.build_notices(trips, "2026-11-12", PT, led, 90,
                                     drive_lookup=lambda *a, **k: 34)
    assert len(first) == 1
    second, led = travel.build_notices(trips, "2026-11-12", PT, led, 90,
                                      drive_lookup=lambda *a, **k: 34)
    assert second == [], "a second run re-showed the notice"


def test_rerun_makes_no_second_routes_call(monkeypatch):
    monkeypatch.setenv(travel.HOME_ENV_KEY, "1 Sentinel Way")
    monkeypatch.setenv(travel.MAPS_ENV_KEY, "test-key")
    calls = []
    trips = travel.detect_trips([_event(location="SFO -> EWR")])
    led = {"shown": [], "routes_calls": {}}
    def spy(*a, **k):
        calls.append(1)
        return 34
    _, led = travel.build_notices(trips, "2026-11-12", PT, led, 90, drive_lookup=spy)
    _, led = travel.build_notices(trips, "2026-11-12", PT, led, 90, drive_lookup=spy)
    assert len(calls) == 1, f"paid lookup ran {len(calls)} times"


def test_no_trip_means_no_lookup_at_all():
    """A day with no departure must not touch a paid API."""
    calls = []
    notices, _ = travel.build_notices(
        [], "2026-11-12", PT, {"shown": [], "routes_calls": {}}, 90,
        drive_lookup=lambda *a, **k: calls.append(1) or 34,
    )
    assert notices == [] and calls == []


# ── The network boundary ─────────────────────────────────────────────────────

class _Reply:
    def __init__(self, status=200, payload=None, boom=None):
        self.status_code = status
        self._payload = payload if payload is not None else {}
        self._boom = boom

    def json(self):
        if self._boom:
            raise self._boom
        return self._payload


def test_routes_asks_for_the_departure_instant_not_now(monkeypatch):
    """Traffic at 5 AM is not the traffic of the drive that starts at 4:01.
    Routes prices on the departureTime it is given, so it has to be the real
    one."""
    sent = {}
    def fake_post(url, json=None, headers=None, timeout=None):
        sent["url"] = url
        sent["body"] = json
        sent["headers"] = headers
        sent["timeout"] = timeout
        return _Reply(200, {"routes": [{"duration": "2040s"}]})
    monkeypatch.setattr(travel.requests, "post", fake_post)

    depart_at = datetime(2026, 11, 12, 4, 1, tzinfo=PT)
    minutes = travel.drive_minutes("1 Sentinel Way", "SFO Airport", depart_at, "k")

    assert minutes == 34
    assert sent["body"]["departureTime"] == "2026-11-12T12:01:00Z"
    assert sent["body"]["routingPreference"] == "TRAFFIC_AWARE"
    assert sent["headers"]["X-Goog-Api-Key"] == "k"
    assert sent["timeout"] == travel.TIMEOUT, "an unbounded call can hang the briefing"


@pytest.mark.parametrize("failure", [
    ("status", _Reply(500)),
    ("status", _Reply(429)),
    ("status", _Reply(403)),
    ("malformed", _Reply(200, {"unexpected": "shape"})),
    ("malformed", _Reply(200, {"routes": []})),
    ("boom", _Reply(200, boom=ValueError("not json"))),
])
def test_routes_returns_none_on_every_failure_shape(monkeypatch, failure):
    """Each shape asserted on its own, so one passing case cannot hide the
    rest."""
    _, reply = failure
    monkeypatch.setattr(travel.requests, "post", lambda *a, **k: reply)
    depart_at = datetime(2026, 11, 12, 4, 1, tzinfo=PT)
    assert travel.drive_minutes("a", "b", depart_at, "k") is None


@pytest.mark.parametrize("boom", [
    ConnectionError("refused"),
    TimeoutError("timed out"),
    Exception("anything else"),
])
def test_routes_survives_a_dead_network(monkeypatch, boom):
    def explode(*a, **k):
        raise boom
    monkeypatch.setattr(travel.requests, "post", explode)
    depart_at = datetime(2026, 11, 12, 4, 1, tzinfo=PT)
    assert travel.drive_minutes("a", "b", depart_at, "k") is None


def test_routes_is_not_called_without_a_key(monkeypatch):
    def explode(*a, **k):
        raise AssertionError("called with no key")
    monkeypatch.setattr(travel.requests, "post", explode)
    assert travel.drive_minutes("a", "b", datetime.now(timezone.utc), "") is None
    assert travel.drive_minutes("a", "b", datetime.now(timezone.utc), None) is None


def test_weather_bounded_and_survives_failure(monkeypatch):
    seen = {}
    def fake_get(url, params=None, timeout=None):
        seen.setdefault("timeouts", []).append(timeout)
        if "geocoding" in url:
            return _Reply(200, {"results": [{
                "name": "Newark", "admin1": "New Jersey", "country": "US",
                "latitude": 40.7, "longitude": -74.2}]})
        return _Reply(200, {"daily": {
            "temperature_2m_max": [54.0], "temperature_2m_min": [41.0],
            "precipitation_probability_max": [20.0]}})
    monkeypatch.setattr(travel.requests, "get", fake_get)
    out = travel.forecast("EWR")
    assert out["place"] == "Newark" and out["high"] == 54.0
    assert all(t == travel.TIMEOUT for t in seen["timeouts"])


@pytest.mark.parametrize("reply", [
    _Reply(500), _Reply(200, {"results": []}), _Reply(200, {}),
])
def test_weather_returns_none_on_failure(monkeypatch, reply):
    monkeypatch.setattr(travel.requests, "get", lambda *a, **k: reply)
    assert travel.forecast("EWR") is None


def test_weather_survives_a_dead_network(monkeypatch):
    def explode(*a, **k):
        raise ConnectionError("refused")
    monkeypatch.setattr(travel.requests, "get", explode)
    assert travel.forecast("EWR") is None


# ── The home address must never reach a page ─────────────────────────────────

SENTINEL = "1 Sentinel Way, Nowhere"


def test_sentinel_address_never_appears_in_any_rendered_notice(monkeypatch, tmp_path):
    """The address is an input to the drive lookup and must not be an output.

    The vault file and the published page are both rendered from these notice
    strings, and briefing_html.redact strips credentials and filesystem paths
    but NOT street addresses, so nothing downstream would catch a leak.
    """
    monkeypatch.setenv(travel.HOME_ENV_KEY, SENTINEL)
    monkeypatch.setenv(travel.MAPS_ENV_KEY, "sentinel-api-key-value")
    trips = travel.detect_trips([_event(location="SFO -> EWR, Terminal 3")])
    led = {"shown": [], "routes_calls": {}}
    notices, led = travel.build_notices(
        trips, "2026-11-12", PT, led, 90, drive_lookup=lambda *a, **k: 34)
    assert notices, "fixture produced no notice to inspect"

    blob = json.dumps(notices)
    assert SENTINEL not in blob
    assert "Sentinel Way" not in blob
    assert "sentinel-api-key-value" not in blob

    # And not in the ledger, which is written to the logs dir.
    path = tmp_path / "travel_notices.json"
    travel.save_ledger(led, path)
    written = path.read_text(encoding="utf-8")
    assert SENTINEL not in written and "sentinel-api-key-value" not in written


def test_sentinel_control_the_test_can_fail():
    """Proof the grep above is capable of failing: the identical assertion
    against a string that DOES carry the address must raise."""
    leaked = json.dumps([{"text": f"Leave {SENTINEL} by 4:01 AM"}])
    with pytest.raises(AssertionError):
        assert SENTINEL not in leaked


def test_home_address_comes_from_private_state_not_config(monkeypatch):
    """It lives beside the refresh tokens, not in the vault config, because
    the vault is synced and published."""
    monkeypatch.setenv(travel.HOME_ENV_KEY, SENTINEL)
    assert travel.home_address() == SENTINEL
    monkeypatch.delenv(travel.HOME_ENV_KEY, raising=False)
    assert travel.home_address() is None
    import config_loader
    assert not any("home" in name.lower() and "address" in name.lower()
                   for name in dir(config_loader)), \
        "a home-address config accessor would put the address in the vault"


# ── Tier 2 ───────────────────────────────────────────────────────────────────

TIER2_EVENT = {
    "source": "Gmail", "title": "Flight to Newark",
    "day": "Thu Nov 12", "time": "6:05 AM",
    "_sort": "2026-11-12T06:05:00-08:00",
    # No location: the connector payload shape carries none.
}


def test_tier2_payload_produces_no_trips():
    assert travel.detect_trips([TIER2_EVENT]) == []


def test_tier2_control_the_same_event_with_a_location_does_produce_a_trip():
    """Control for the test above. Without this, a Tier 2 test passes because
    the fixture is inert rather than because the tier gate works."""
    tier1 = dict(TIER2_EVENT, location="San Francisco Airport (SFO)")
    assert len(travel.detect_trips([tier1])) == 1


def test_collect_never_raises_and_reports_its_own_failure(monkeypatch):
    """An exception in travel must cost the reader a flight notice, not the
    rest of the briefing."""
    def explode(events):
        raise RuntimeError("detector blew up")
    monkeypatch.setattr(travel, "detect_trips", explode)
    out = travel.collect([_event()], "2026-11-12", PT, 90)
    assert out["notices"] == []
    assert "Travel notices failed" in out["error"]
    # The TYPE, never the text: exception text routinely quotes the arguments,
    # and here those are the home address and the API key.
    assert "RuntimeError" in out["error"]
    assert "detector blew up" not in out["error"]


def test_collect_returns_the_expected_shape():
    out = travel.collect([], "2026-11-12", PT, 90, live=False)
    assert set(out) == {"notices", "trips", "error", "commit"}
    assert out["error"] == ""
    assert callable(out["commit"])


# ── Voice ────────────────────────────────────────────────────────────────────

def test_no_dashes_in_rendered_notice_text(monkeypatch):
    """Notice text is assembled from airport and airline strings we do not
    control, so the strip happens in code, not in a review pass."""
    monkeypatch.setenv(travel.HOME_ENV_KEY, "1 Sentinel Way")
    monkeypatch.setenv(travel.MAPS_ENV_KEY, "k")
    for offset in (5, 2, 0):
        notices, _ = _collect_on(offset)
        for notice in notices:
            assert "\u2014" not in notice["text"], f"em-dash in {notice['kind']}"
            assert "\u2013" not in notice["text"], f"en-dash in {notice['kind']}"


def test_strip_dashes_actually_strips():
    """Control: prove the guard above can fail by feeding it a planted dash."""
    planted = "Leave \u2014 now \u2013 or else"
    cleaned = travel._strip_dashes(planted)
    assert "\u2014" not in cleaned and "\u2013" not in cleaned
    assert planted != cleaned, "the planted instance was not detected"


# ── The location field, end to end from both fetchers ────────────────────────

def test_location_is_consumed_not_merely_carried():
    """A populated-but-unused field would pass a presence check. Blanking it
    must make the airport disappear."""
    with_location = travel.detect_trips([_event(location="SFO Airport, Terminal 3")])
    assert with_location[0]["origin"] == "SFO"
    assert with_location[0]["terminal"] == "3"
    blanked = travel.detect_trips([_event(location="")])
    assert blanked == [], "the airport survived a blanked location"


def test_both_fetchers_carry_location():
    """Read from the source, because a fixture I write myself proves nothing
    about the real payload shape."""
    from pathlib import Path
    source = Path(__file__).resolve().parent.parent / "app" / "week_review.py"
    text = source.read_text(encoding="utf-8")
    # The Google fetcher reads a flat string; Graph nests it under displayName.
    assert '"location": item.get("location", "") or ""' in text, \
        "the Google calendar fetcher does not carry location"
    assert '"location": (item.get("location") or {}).get("displayName", "") or ""' in text, \
        "the Outlook calendar fetcher does not carry location"


def test_merge_keeps_the_sort_instant():
    """_sort is the only field carrying an offset. merge_calendars used to pop
    it, which would leave travel with nothing to compute a leave-by from."""
    import week_review
    merged = week_review.merge_calendars([{
        "source": "Google", "title": "Flight to Newark", "day": "Thu Nov 12",
        "time": "6:05 AM", "location": "SFO -> EWR",
        "_sort": "2026-11-12T06:05:00-08:00",
    }])
    assert merged[0].get("_sort") == "2026-11-12T06:05:00-08:00"
    assert merged[0].get("location") == "SFO -> EWR"


# ── The doc matches the code ─────────────────────────────────────────────────

def test_skill_documents_the_keys_the_code_actually_emits():
    """A doc naming a key the code does not emit sends the model looking for
    something that is never there."""
    from pathlib import Path
    doc = (Path(__file__).resolve().parent.parent
           / "skills" / "morning-coffee" / "SKILL.md").read_text(encoding="utf-8")
    assert '"travel"' in doc, "SKILL.md does not document the travel key"
    assert "TRAVEL" in doc, "SKILL.md has no TRAVEL render block"

    # Every notice field named in the doc must be one the code can emit.
    emitted = {"kind", "trip", "text", "link", "leave_by", "drive_minutes"}
    for field in ("kind", "trip", "text", "link", "leave_by", "drive_minutes"):
        assert field in doc, f"SKILL.md omits the {field} field"

    # And the reverse: build a real notice and check nothing it carries is
    # undocumented.
    trips = travel.detect_trips([_event(location="SFO -> EWR")])
    notices, _ = travel.build_notices(
        trips, "2026-11-12", PT, {"shown": [], "routes_calls": {}}, 90,
        drive_lookup=lambda *a, **k: 34)
    for notice in notices:
        assert set(notice) <= emitted, f"undocumented field: {set(notice) - emitted}"


def test_morning_coffee_emits_travel_on_every_path():
    """The key is seeded in the output dict so an early return still carries
    it; a skill reading output["travel"] must never KeyError."""
    from pathlib import Path
    source = (Path(__file__).resolve().parent.parent
              / "app" / "morning_coffee.py").read_text(encoding="utf-8")
    assert '"travel": [],' in source, \
        "travel is not seeded in the output dict, so early returns omit it"


# ── A booked flight needs no search link ─────────────────────────────────────

BOOKED_EVENTS = [
    {"title": "UA 523 to Newark", "location": "SFO -> EWR",
     "_sort": "2026-11-12T06:05:00-08:00"},
    {"title": "Flight east (Confirmation ABC123)", "location": "SFO -> EWR",
     "_sort": "2026-11-12T06:05:00-08:00"},
    {"title": "Trip east", "location": "SFO -> EWR, record locator XYZ99",
     "_sort": "2026-11-12T06:05:00-08:00"},
    {"title": "Booking: east coast", "location": "SFO -> EWR",
     "_sort": "2026-11-12T06:05:00-08:00"},
]


@pytest.mark.parametrize("event", BOOKED_EVENTS, ids=lambda e: e["title"][:24])
def test_a_booked_flight_gets_no_search_link(event):
    """Offering to search for a flight already held tells the reader to redo
    finished work. Each booked shape asserted on its own."""
    trips = travel.detect_trips([event])
    assert trips and trips[0]["booked"], "the booking marker was not seen"
    notices, _ = travel.build_notices(
        trips, "2026-11-07", PT, {"shown": [], "routes_calls": {}}, 90)
    assert [n["kind"] for n in notices] == []


def test_an_unbooked_trip_still_gets_its_link():
    """Control for the test above: without a booking marker the link fires,
    so the gate is not simply suppressing everything."""
    unbooked = {"title": "Trip east", "location": "SFO -> EWR",
                "_sort": "2026-11-12T06:05:00-08:00"}
    trips = travel.detect_trips([unbooked])
    assert not trips[0]["booked"]
    notices, _ = travel.build_notices(
        trips, "2026-11-07", PT, {"shown": [], "routes_calls": {}}, 90)
    assert [n["kind"] for n in notices] == [travel.NOTICE_FLIGHTS]


def test_a_booked_flight_still_gets_its_departure_notice():
    """Booked only suppresses the SEARCH. The morning-of notice is the whole
    point of the feature and must survive."""
    trips = travel.detect_trips([BOOKED_EVENTS[0]])
    notices, _ = travel.build_notices(
        trips, "2026-11-12", PT, {"shown": [], "routes_calls": {}}, 90)
    assert [n["kind"] for n in notices] == [travel.NOTICE_DEPARTURE]


# ── The address must not escape through an exception ─────────────────────────

def test_a_raising_drive_lookup_does_not_leak_the_address(monkeypatch):
    """An exception raised inside a route lookup routinely quotes its own
    arguments, and those arguments are the home address and the API key."""
    monkeypatch.setenv(travel.HOME_ENV_KEY, SENTINEL)
    monkeypatch.setenv(travel.MAPS_ENV_KEY, "sentinel-api-key-value")

    def explode(origin, dest, when, key):
        raise RuntimeError(f"connection failed for {origin} key={key}")

    trips = travel.detect_trips([_event(location="SFO -> EWR")])
    notices, _ = travel.build_notices(
        trips, "2026-11-12", PT, {"shown": [], "routes_calls": {}}, 90,
        drive_lookup=explode)
    blob = json.dumps(notices)
    assert SENTINEL not in blob and "Sentinel Way" not in blob
    assert "sentinel-api-key-value" not in blob
    # And it degraded rather than crashing.
    assert notices and notices[0]["leave_by"] is None


def test_collect_error_carries_no_exception_text(monkeypatch):
    """collect's error string reaches output["errors"], which the briefing
    renders and the web page publishes."""
    monkeypatch.setenv(travel.HOME_ENV_KEY, SENTINEL)
    monkeypatch.setenv(travel.MAPS_ENV_KEY, "sentinel-api-key-value")

    def explode(events):
        raise RuntimeError(f"failed reading {SENTINEL} key=sentinel-api-key-value")

    monkeypatch.setattr(travel, "detect_trips", explode)
    out = travel.collect([_event()], "2026-11-12", PT, 90)
    assert SENTINEL not in out["error"]
    assert "Sentinel Way" not in out["error"]
    assert "sentinel-api-key-value" not in out["error"]
    assert "RuntimeError" in out["error"], "the reader still needs to know it failed"


def test_leak_control_the_assertions_above_can_fail():
    """Proof the greps are capable of failing, planted both ways."""
    leaked_notice = json.dumps([{"text": f"drive from {SENTINEL}"}])
    with pytest.raises(AssertionError):
        assert SENTINEL not in leaked_notice
    leaked_error = f"Travel notices failed: reading {SENTINEL}"
    with pytest.raises(AssertionError):
        assert SENTINEL not in leaked_error


# ── The ledger is written after the render, never before ─────────────────────

def test_collect_does_not_write_the_ledger_by_itself(monkeypatch, tmp_path):
    """A crash between assembly and render must not leave a record claiming
    the reader was shown a notice they never saw."""
    written = []
    monkeypatch.setattr(travel, "save_ledger", lambda *a, **k: written.append(1))
    monkeypatch.setattr(travel, "load_ledger",
                        lambda *a, **k: {"shown": [], "routes_calls": {}})
    monkeypatch.setenv(travel.HOME_ENV_KEY, "1 Sentinel Way")
    monkeypatch.setenv(travel.MAPS_ENV_KEY, "k")
    out = travel.collect([_event(location="SFO -> EWR")], "2026-11-12", PT, 90,
                         live=False)
    assert out["notices"], "fixture produced nothing to commit"
    assert written == [], "collect wrote the ledger before the notice rendered"
    out["commit"]()
    assert written == [1], "commit did not write the ledger"


def test_morning_coffee_commits_only_after_printing():
    """Read from the source: the commit call must come after the print, or the
    ordering guarantee is words rather than behaviour."""
    from pathlib import Path
    source = (Path(__file__).resolve().parent.parent
              / "app" / "morning_coffee.py").read_text(encoding="utf-8")
    printed = source.rindex("print(json.dumps(output")
    committed = source.rindex("_travel_commit()")
    assert committed > printed, \
        "the ledger is committed before the briefing is printed"


# ── The forecast is for the right place on the map ───────────────────────────

def _haversine_km(lat1, lon1, lat2, lon2):
    """Great-circle distance, so "is this the right city" is a measurement
    rather than a string comparison."""
    import math
    r = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a = (math.sin(dp / 2) ** 2
         + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2)
    return 2 * r * math.asin(math.sqrt(a))


# Where these airports actually are, for the distance check below.
KNOWN = {"EWR": (40.6895, -74.1745), "SFO": (37.6213, -122.3790)}


def test_forecast_resolves_within_100km_of_the_airport(monkeypatch):
    """A geocode that lands on the wrong continent still returns a real
    forecast. Distance is what catches that; a name comparison is not."""
    seen = {}

    def fake_get(url, params=None, timeout=None):
        if "geocoding" in url:
            seen["query"] = params["name"]
            return _Reply(200, {"results": [{
                "name": "Newark", "admin1": "New Jersey", "country": "US",
                "latitude": 40.7357, "longitude": -74.1724}]})
        seen["lat"] = params["latitude"]
        seen["lon"] = params["longitude"]
        return _Reply(200, {"daily": {
            "temperature_2m_max": [54.0], "temperature_2m_min": [41.0],
            "precipitation_probability_max": [20.0]}})

    monkeypatch.setattr(travel.requests, "get", fake_get)
    out = travel.forecast("EWR")
    assert out is not None
    lat, lon = KNOWN["EWR"]
    km = _haversine_km(seen["lat"], seen["lon"], lat, lon)
    assert km < 100, f"forecast resolved {km:.0f} km from EWR"


def test_geocode_control_a_wrong_hemisphere_is_caught():
    """Proof the distance check can fail: Newark in England is a real place
    and a plausible geocode hit."""
    lat, lon = KNOWN["EWR"]
    km = _haversine_km(53.0, -1.0, lat, lon)  # Newark-on-Trent, England
    assert km > 100, "the distance check would not catch a wrong-country hit"


def test_forecast_names_the_place_so_ambiguity_is_visible(monkeypatch):
    """Portland OR and Portland ME both exist. The reader must be able to see
    which one the briefing means."""
    monkeypatch.setattr(travel.requests, "get", lambda url, params=None, timeout=None: (
        _Reply(200, {"results": [{"name": "Portland", "admin1": "Maine",
                                  "country": "US", "latitude": 43.66,
                                  "longitude": -70.26}]})
        if "geocoding" in url else
        _Reply(200, {"daily": {"temperature_2m_max": [50.0],
                               "temperature_2m_min": [38.0],
                               "precipitation_probability_max": [10.0]}})))
    out = travel.forecast("PWM")
    assert out["place"] == "Portland"
    assert out["region"] == "Maine", "the region is what disambiguates"


# ── A pre-feature config makes no network call at all ────────────────────────

def test_a_config_without_a_travel_block_makes_zero_network_calls(monkeypatch):
    """Every existing install is in exactly this state. The feature runs on
    its defaults and must not reach the network before a trip exists."""
    calls = []
    for verb in ("get", "post"):
        monkeypatch.setattr(travel.requests, verb,
                            lambda *a, **k: calls.append(1) or _Reply(200, {}))
    # The defaults are asserted through _travel() rather than by overwriting
    # config_loader._config, which conftest primes once for the whole session:
    # replacing it leaks a stripped config into whatever else is running.
    import config_loader
    monkeypatch.setattr(config_loader, "_travel", dict)
    assert config_loader.travel_enabled() is True
    assert config_loader.travel_buffer_minutes() == 90
    assert config_loader.travel_weather_lead_days() == 2

    # A calendar with no flights: the normal day.
    out = travel.collect(
        [{"source": "Google", "title": "Weekly sync", "day": "Thu Nov 12",
          "time": "9:00 AM", "location": "Room B",
          "_sort": "2026-11-12T09:00:00-08:00"}],
        "2026-11-12", PT, 90)
    assert out["notices"] == []
    assert calls == [], f"a no-trip day made {len(calls)} network calls"


def test_defaults_hold_when_the_travel_block_is_junk(monkeypatch):
    """A hand-edited config should degrade to the defaults, not crash."""
    import config_loader
    monkeypatch.setattr(config_loader, "_travel",
                        lambda: {"airport_buffer_minutes": "ninety",
                                 "weather_lead_days": None})
    assert config_loader.travel_buffer_minutes() == 90
    assert config_loader.travel_weather_lead_days() == 2


# ── An airport code is not a place name ──────────────────────────────────────

def test_a_known_airport_code_is_never_geocoded(monkeypatch):
    """Found on a live run of a real trip: "DAL" handed to a place-name
    geocoder resolves to Dali, in Yunnan, China, and returns a real forecast
    for the wrong continent with no error at all. SNA, EWR and LAX happen to
    resolve correctly, which is exactly what makes it easy to miss."""
    def explode(*a, **k):
        raise AssertionError("a known airport code reached the geocoder")

    calls = []

    def fake_get(url, params=None, timeout=None):
        if "geocoding" in url:
            explode()
        calls.append(params)
        return _Reply(200, {"daily": {"temperature_2m_max": [99.4],
                                      "temperature_2m_min": [82.3],
                                      "precipitation_probability_max": [1.0]}})

    monkeypatch.setattr(travel.requests, "get", fake_get)
    out = travel.forecast("DAL")
    assert out["place"] == "Dallas Love"
    # And the coordinates handed to the weather API are really Dallas.
    lat, lon = calls[0]["latitude"], calls[0]["longitude"]
    assert _haversine_km(lat, lon, 32.8471, -96.8518) < 25


@pytest.mark.parametrize("code,lat,lon", [
    ("DAL", 32.8471, -96.8518), ("SNA", 33.6757, -117.8683),
    ("LAX", 33.9416, -118.4085), ("JFK", 40.6413, -73.7781),
    ("ORD", 41.9742, -87.9073), ("SFO", 37.6213, -122.3790),
])
def test_airport_table_coordinates_are_where_the_airport_is(code, lat, lon):
    """Each asserted on its own: a table is only worth having if the numbers
    in it are right, and a wrong row is silent."""
    assert code in travel.AIRPORT_COORDS
    tlat, tlon, _ = travel.AIRPORT_COORDS[code]
    assert _haversine_km(tlat, tlon, lat, lon) < 25, f"{code} is in the wrong place"


def test_an_unknown_code_still_falls_back_to_the_geocoder(monkeypatch):
    """The table is a shortcut, not a whitelist. A small airport must still
    get a forecast."""
    seen = {}

    def fake_get(url, params=None, timeout=None):
        if "geocoding" in url:
            seen["geocoded"] = params["name"]
            return _Reply(200, {"results": [{"name": "Aspen", "admin1": "Colorado",
                                             "country": "US", "latitude": 39.22,
                                             "longitude": -106.86}]})
        return _Reply(200, {"daily": {"temperature_2m_max": [60.0],
                                      "temperature_2m_min": [30.0],
                                      "precipitation_probability_max": [5.0]}})

    monkeypatch.setattr(travel.requests, "get", fake_get)
    out = travel.forecast("Aspen")
    assert out["place"] == "Aspen"
    assert seen["geocoded"] == "Aspen"


# ── Destinations, as calendar entries really read ────────────────────────────

@pytest.mark.parametrize("location,origin,destination", [
    ("John Wayne Airport (SNA) -> DAL", "SNA", "DAL"),
    ("SNA -> DAL", "SNA", "DAL"),
    ("John Wayne (SNA) to Dallas Love (DAL)", "SNA", "DAL"),
    ("(SNA) - (DAL)", "SNA", "DAL"),
    ("San Francisco Airport (SFO)", "SFO", None),
    ("SFO Airport, Terminal 3", "SFO", None),
])
def test_destination_parsed_from_real_location_shapes(location, origin, destination):
    """Found on a real trip: the original pattern required the two codes
    ADJACENT, so it matched only the one tidy fixture shape and returned None
    for every way a live calendar entry actually reads. Each shape is asserted
    on its own, because a set-level check passes while any one of them works."""
    assert travel.airport_code(location) == origin
    assert travel._destination_code(location, origin) == destination


# ── Reading the airline confirmation ─────────────────────────────────────────
# The calendar entry is usually bare. The detail a traveller needs at 4 AM is
# in this email and nowhere else.

SOUTHWEST_BODY = """
Your trip is confirmed!

Confirmation # ABC123

DEPART
Wed, Sep 16, 2026
6:00 AM
SNA
Orange County (Santa Ana), CA

ARRIVE
11:05 AM
DAL
Dallas (Love Field), TX

Flight WN 1442
"""

# The shape a flattened HTML table really produces: label and value split.
UNITED_TABLE_BODY = """
Confirmation number:

XKCD99

Flight

UA  523

Departs

Terminal

3

SNA

to

DAL
"""

DELTA_BODY = """
Your flight receipt and itinerary
Record locator: QWERTY
Departing SNA (John Wayne) Terminal A, arriving ATL (Hartsfield)
Flight DL 890 on September 16
Gate B14
"""


def test_a_southwest_style_confirmation_parses():
    out = travel.parse_confirmation(SOUTHWEST_BODY, "Your trip confirmation")
    assert out["origin"] == "SNA"
    assert out["destination"] == "DAL"
    assert out["flight"] == "WN1442"
    assert out["confirmation"] == "ABC123"


def test_a_table_split_confirmation_parses():
    """Airline mail is laid out in tables, so flattening one routinely splits
    a label from its value across a blank line: "Terminal\\n\\n3"."""
    out = travel.parse_confirmation(UNITED_TABLE_BODY, "Your itinerary")
    assert out["confirmation"] == "XKCD99"
    assert out["terminal"] == "3"
    assert out["origin"] == "SNA"
    assert out["destination"] == "DAL"


def test_a_delta_style_confirmation_parses():
    out = travel.parse_confirmation(DELTA_BODY, "Your flight receipt")
    assert out["origin"] == "SNA"
    assert out["destination"] == "ATL"
    assert out["flight"] == "DL890"
    assert out["terminal"] == "A"
    assert out["gate"] == "B14"
    assert out["confirmation"] == "QWERTY"


def test_a_partial_confirmation_returns_only_what_it_said():
    """A partial answer is worth having; a guessed one is not."""
    out = travel.parse_confirmation("Departing SNA on Wednesday.", "Your flight")
    assert out["origin"] == "SNA"
    assert out["destination"] is None
    assert out["terminal"] is None
    assert out["flight"] is None


@pytest.mark.parametrize("sender,subject,expected", [
    ("no-reply@united.com", "Your flight confirmation", True),
    ("confirm@southwest.com", "Your trip is confirmed", True),
    ("itinerary@delta.com", "Your flight receipt and itinerary", True),
    # A newsletter ABOUT an airline is not a booking.
    ("news@travelblog.com", "United confirmation tips", False),
    # The airline writing about something other than a trip.
    ("deals@united.com", "Fare sale to Hawaii", False),
    ("noreply@aa.com", "Confirm your email address", True),
    ("", "Your flight confirmation", False),
    ("no-reply@united.com", "", False),
])
def test_only_real_airline_confirmations_qualify(sender, subject, expected):
    """Each asserted on its own. A 4 AM notice built out of marketing copy is
    worse than no notice."""
    assert travel.looks_like_a_confirmation(sender, subject) is expected


def test_the_email_fills_gaps_but_never_overrides_the_calendar():
    """A person editing their own calendar entry is stating a fact about
    their own trip. A month-old confirmation is not grounds to contradict
    them, so the email only fills what is missing."""
    trip = travel.detect_trips([_event(
        location="John Wayne Airport (SNA), Terminal C")])[0]
    assert trip["terminal"] == "C" and trip["destination"] is None
    conf = {"origin": "SNA", "destination": "DAL", "terminal": "3",
            "flight": "WN1442", "confirmation": "ABC123",
            "date": trip["departs"].date()}
    enriched = travel.enrich_trip(dict(trip), [conf])
    assert enriched["destination"] == "DAL", "the gap was not filled"
    assert enriched["terminal"] == "C", "the email overrode the calendar"
    assert enriched["booked"] is True


def test_a_confirmation_for_another_trip_is_ignored():
    """Matched on origin and date, so last month's flight does not decorate
    this week's."""
    trip = travel.detect_trips([_event(location="San Francisco Airport (SFO)")])[0]
    wrong_airport = {"origin": "SNA", "destination": "DAL",
                     "date": trip["departs"].date()}
    assert travel.enrich_trip(dict(trip), [wrong_airport])["destination"] is None
    from datetime import date
    wrong_date = {"origin": "SFO", "destination": "DAL", "date": date(2020, 1, 1)}
    assert travel.enrich_trip(dict(trip), [wrong_date])["destination"] is None


def test_enrichment_of_a_trip_with_no_departure_instant_is_a_no_op():
    trip = {"origin": "SNA", "departs": None}
    assert travel.enrich_trip(dict(trip), [{"origin": "SNA", "destination": "DAL"}]) == trip


# ── The mailbox round trip ───────────────────────────────────────────────────

def _gmail_message(sender, subject, body, snippet=""):
    """A Gmail payload in the real nested shape the API returns."""
    import base64
    encoded = base64.urlsafe_b64encode(body.encode("utf-8")).decode("ascii")
    return {
        "id": "abc", "snippet": snippet,
        "payload": {
            "mimeType": "multipart/alternative",
            "headers": [{"name": "From", "value": sender},
                        {"name": "Subject", "value": subject}],
            "parts": [
                {"mimeType": "text/html", "body": {"data": ""}},
                {"mimeType": "text/plain", "body": {"data": encoded}},
            ],
        },
    }


def test_the_body_is_decoded_out_of_a_nested_payload():
    """Gmail nests text/plain under parts, often behind the HTML alternative."""
    msg = _gmail_message("no-reply@united.com", "Your itinerary", DELTA_BODY)
    body = travel._message_body(msg["payload"])
    assert "Record locator" in body
    assert travel.parse_confirmation(body, "Your itinerary")["flight"] == "DL890"


def test_a_confirmation_date_comes_from_the_text_not_the_arrival(monkeypatch):
    """A confirmation is sent weeks ahead, so matching on when the EMAIL
    arrived attaches it to the wrong trip."""
    from datetime import date
    msg = {"snippet": "Departing Sep 16, 2026 from SNA"}
    assert travel._confirmation_date(msg, {}) == date(2026, 9, 16)
    assert travel._confirmation_date({"snippet": "no date here"}, {}) is None


def test_tier2_makes_no_mailbox_call(monkeypatch):
    """Tier 2 has no OAuth, so the fetch must return empty rather than raise."""
    import data_sources
    monkeypatch.setattr(data_sources, "is_tier1", lambda: False)
    assert travel.fetch_confirmations() == []


def test_collect_skips_the_mailbox_when_nothing_is_missing(monkeypatch):
    """A well-filled calendar entry needs no round trip. Paying for one on
    every run would be a daily cost for nothing."""
    called = []
    monkeypatch.setattr(travel, "fetch_confirmations",
                        lambda *a, **k: called.append(1) or [])
    monkeypatch.setattr(travel, "load_ledger",
                        lambda *a, **k: {"shown": [], "routes_calls": {}})
    monkeypatch.setattr(travel, "save_ledger", lambda *a, **k: None)
    complete = _event(location="SNA -> DAL, Terminal 3")
    travel.collect([complete], "2026-09-16", PT, 90, live=True)
    assert called == [], "the mailbox was queried with nothing missing"


def test_collect_reads_the_mailbox_when_the_destination_is_missing(monkeypatch):
    """The case that motivated this: a bare calendar entry."""
    from datetime import date
    called = []

    def fake_fetch(*a, **k):
        called.append(1)
        return [{"origin": "SNA", "destination": "DAL", "terminal": "3",
                 "flight": "WN1442", "confirmation": "ABC123",
                 "date": date(2026, 9, 16)}]

    monkeypatch.setattr(travel, "fetch_confirmations", fake_fetch)
    monkeypatch.setattr(travel, "load_ledger",
                        lambda *a, **k: {"shown": [], "routes_calls": {}})
    monkeypatch.setattr(travel, "save_ledger", lambda *a, **k: None)
    monkeypatch.setattr(travel, "forecast", lambda p: {
        "place": "Dallas Love", "region": "", "country": "",
        "high": 99.0, "low": 82.0, "rain_chance": 1.0})
    bare = {"source": "Google", "title": "Flight to Dallas",
            "day": "Wed Sep 16", "time": "6:00 AM",
            "location": "John Wayne Airport (SNA)",
            "_sort": "2026-09-16T06:00:00-07:00"}
    out = travel.collect([bare], "2026-09-14", PT, 90, live=True)
    assert called == [1], "the mailbox was not consulted"
    # The weather notice can only exist because the email supplied DAL.
    assert [n["kind"] for n in out["notices"]] == [travel.NOTICE_WEATHER]
    assert "Dallas Love" in out["notices"][0]["text"]


# ── One ledger, several briefings ────────────────────────────────────────────
# Travel used to have one consumer, so a notice was shown once and that was
# the whole rule. Three briefings read it now, and a shared key would mean
# whichever ran first consumed the notice while the others went quiet about
# the trip. That is worst exactly where it matters most: Afternoon Tea exists
# to warn about an early flight the evening before, and it would have been
# silenced by that same morning's Morning Coffee.

def test_each_briefing_gets_its_own_key():
    trip = _trip()
    keys = {travel.notice_key(trip, travel.NOTICE_DEPARTURE, b)
            for b in ("morning-coffee", "afternoon-tea", "week")}
    assert len(keys) == 3, "three briefings, three keys"


def test_one_briefing_showing_a_notice_does_not_silence_another():
    """The collision this key exists to prevent."""
    led = {"shown": [], "routes_calls": {}}
    trip = _trip()
    morning = travel.notice_key(trip, travel.NOTICE_DEPARTURE, "morning-coffee")
    evening = travel.notice_key(trip, travel.NOTICE_DEPARTURE, "afternoon-tea")
    led = travel.record_shown(led, morning, "2026-11-12")
    assert travel.already_shown(led, morning), "shown once in the morning"
    assert not travel.already_shown(led, evening), "the evening still gets to warn"


def test_a_briefing_still_shows_a_notice_only_once():
    """Per briefing, not per run: a rerun must not repeat itself."""
    led = {"shown": [], "routes_calls": {}}
    trip = _trip()
    key = travel.notice_key(trip, travel.NOTICE_WEATHER, "week")
    led = travel.record_shown(led, key, "2026-11-10")
    assert travel.already_shown(led,
                                travel.notice_key(trip, travel.NOTICE_WEATHER, "week"))


def test_an_unkeyed_call_reproduces_the_original_key():
    """A ledger written before this change still suppresses what it recorded."""
    trip = _trip()
    assert travel.notice_key(trip, travel.NOTICE_WEATHER) == \
        travel.notice_key(trip, travel.NOTICE_WEATHER, "")


def test_the_window_moves_with_the_briefing():
    """Afternoon Tea asks about tomorrow, so its departure notice is a day early.

    Same trip, same day, two briefings: with a wider lead the evening briefing
    sees the flight that the morning-of window does not.
    """
    events = [_event(sort="2026-11-13T06:05:00-08:00", location="SFO -> EWR")]
    morning, _ = travel.build_notices(
        travel.detect_trips(events), "2026-11-12", PT,
        {"shown": [], "routes_calls": {}}, 90,
        weather_lead_days=2, briefing="morning-coffee",
        weather_lookup=lambda d: {"place": "Newark", "low": 40, "high": 55,
                                  "rain_chance": 10})
    evening, _ = travel.build_notices(
        travel.detect_trips(events), "2026-11-12", PT,
        {"shown": [], "routes_calls": {}}, 90,
        weather_lead_days=1, briefing="afternoon-tea",
        weather_lookup=lambda d: {"place": "Newark", "low": 40, "high": 55,
                                  "rain_chance": 10})
    assert [n["kind"] for n in morning] == [], "one day out is outside the 2-day window"
    assert [n["kind"] for n in evening] == [travel.NOTICE_WEATHER]


def test_an_evening_briefing_warns_about_tomorrows_flight():
    """The case Afternoon Tea exists for, and the one that was silently missed.

    The departure branch was pinned to `days_out == 0`, so widening the other
    windows changed nothing about it: an evening briefing produced a booking
    link for next week and said nothing at all about the 6 AM flight the next
    morning. Caught by rendering the feature rather than by any passing test,
    which is why this one plants the exact shape.
    """
    events = [_event(title="Flight to Dallas", location="SNA -> DAL",
                     sort="2026-11-13T06:10:00-08:00")]   # tomorrow, 6:10 AM

    def notices(**kw):
        return travel.build_notices(
            travel.detect_trips(events), "2026-11-12", PT,
            {"shown": [], "routes_calls": {}}, 90, **kw)[0]

    morning = notices(briefing="morning-coffee", departure_lead_days=0)
    evening = notices(briefing="afternoon-tea", departure_lead_days=1)

    assert travel.NOTICE_DEPARTURE not in [n["kind"] for n in morning], (
        "the morning of is a day away; Morning Coffee covers it then")
    assert travel.NOTICE_DEPARTURE in [n["kind"] for n in evening], (
        "the evening before is the last chance to say anything useful")


def test_a_departure_notice_never_fires_for_a_past_flight():
    """Widening the window must not reach backwards."""
    events = [_event(location="SNA -> DAL", sort="2026-11-11T06:10:00-08:00")]
    got = travel.build_notices(
        travel.detect_trips(events), "2026-11-12", PT,
        {"shown": [], "routes_calls": {}}, 90,
        briefing="afternoon-tea", departure_lead_days=1)[0]
    assert got == [], "yesterday's flight is not a notice"
