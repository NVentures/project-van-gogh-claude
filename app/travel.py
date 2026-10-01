#!/usr/bin/env python3
"""Travel logistics for the morning briefing.

Three notices, each on its own day, so a trip never produces the same line
twice:

  no flight yet   ->  a Google Flights search link, once, as soon as a trip
                      is far enough out to book
  two days out    ->  the destination forecast
  departure day   ->  origin airport, terminal when the calendar knows it,
                      live drive time, and the time to leave home

WHY THE TIMEZONE HANDLING LOOKS PARANOID
Calendar events reach this module carrying `time` as text that was already
formatted for display ("6:05 AM"), and the timezone that text was rendered in
is not written down anywhere in the event. A flight out of Newark at 6:05 AM
Eastern is 3:05 AM where a Pacific user is standing when they need to leave
the house. Subtracting the drive and the buffer from a naive clock reading is
three hours wrong, in the direction that misses the flight, and it is wrong
silently: every field still renders. So a trip is built from `_sort`, which
carries the original ISO instant with its offset, and every arithmetic step
below runs on aware datetimes. `time` is display text and is never parsed for
arithmetic. If `_sort` cannot be parsed into an aware instant, the trip
produces no leave-by notice at all rather than a plausible wrong one.

The airport is read from the event's location, never guessed from the title.
"Flight simulator demo" is a meeting, and a briefing that invents a 4 AM
alarm for it is worse than one that says nothing.
"""

import json
import os
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlencode
from zoneinfo import ZoneInfo

import requests

# Every outbound call is bounded. A briefing that hangs is a briefing that
# does not arrive, and this runs unattended before dawn.
TIMEOUT = 10

# Live traffic is a paid lookup. The cap is per day, counted in the ledger,
# and exists so a malformed calendar cannot bill in a loop. Three is one
# real departure plus two retries.
ROUTES_DAILY_CAP = 3

ROUTES_URL = "https://routes.googleapis.com/directions/v2:computeRoutes"
WEATHER_URL = "https://api.open-meteo.com/v1/forecast"
GEOCODE_URL = "https://geocoding-api.open-meteo.com/v1/search"
FLIGHTS_URL = "https://www.google.com/travel/flights"

MAPS_ENV_KEY = "GOOGLE_MAPS_API_KEY"
HOME_ENV_KEY = "VAN_GOGH_HOME_ADDRESS"

# A drive this long is a geocode that landed in the wrong state; one this
# short is an origin that resolved onto the terminal itself. Neither is a
# number to hang an alarm on.
MIN_PLAUSIBLE_DRIVE_MIN = 2
MAX_PLAUSIBLE_DRIVE_MIN = 240

# Notices older than this are dropped from the ledger. Without a prune the
# file grows for the life of the install.
LEDGER_RETENTION_DAYS = 30

NOTICE_FLIGHTS = "flights"
NOTICE_WEATHER = "weather"
NOTICE_DEPARTURE = "departure"

# A location field only names an airport when the code stands alone. Bare
# uppercase triples are common in meeting locations ("HQ CEO SYNC"), so the
# code must be attached to an airport word or wrapped in the punctuation
# airlines use.
_AIRPORT_PATTERNS = [
    re.compile(r"\(([A-Z]{3})\)"),
    re.compile(r"\b([A-Z]{3})\s+(?:International\s+)?Airport\b"),
    re.compile(r"\bAirport\s+\(?([A-Z]{3})\)?\b"),
    re.compile(r"\b([A-Z]{3})\s*(?:->|→|-)\s*[A-Z]{3}\b"),
]

# A booked flight names itself: a confirmation code, a record locator, or a
# flight number. Offering to search for a flight someone already holds is the
# briefing telling them to redo finished work.
_BOOKED = re.compile(
    r"\b(?:conf(?:irmation)?|record\s+locator|booking|PNR|ticket)\b"
    r"|\b[A-Z]{2}\s?\d{2,4}\b",
    re.IGNORECASE,
)

# Words that make an event about travel without being a flight.
_NOT_A_FLIGHT = re.compile(
    r"\b(simulator|sim|training|webinar|demo|podcast|interview|debrief|"
    r"retro|standup|stand-up|1:1|one-on-one|review|sync)\b",
    re.IGNORECASE,
)


def _strip_dashes(text: str) -> str:
    """Remove em and en dashes from anything rendered to a person.

    The interface voice rules ban them, and a prompt or a review pass is not
    a guarantee: notice text is assembled from airline and airport strings we
    do not control, so the strip happens in code at the point of render.
    """
    # Escapes, not literals: a dash sweep over this file must not be able to
    # rewrite the very characters the strip removes.
    return text.replace("\u2014", ", ").replace("\u2013", "-")


def parse_instant(raw: str):
    """An aware datetime from a calendar `_sort` value, or None.

    Returns None for an all-day date ("2026-11-12"), which carries no clock
    time and therefore cannot support leave-by arithmetic.
    """
    if not raw or "T" not in raw:
        return None
    try:
        text = raw.replace("Z", "+00:00")
        dt = datetime.fromisoformat(text)
    except ValueError:
        return None
    if dt.tzinfo is None:
        return None
    return dt


def airport_code(location: str):
    """The IATA code named by an event location, or None.

    Reads the location only. A title is never consulted: titles are written
    by people and routinely name airports they are not departing from.
    """
    if not location:
        return None
    for pattern in _AIRPORT_PATTERNS:
        found = pattern.search(location)
        if found:
            return found.group(1).upper()
    return None


def detect_trips(events: list) -> list:
    """Trips from calendar events, in departure order.

    A trip needs an airport code in its location and an instant it departs.
    Everything else is a meeting.
    """
    trips = []
    for event in events or []:
        location = event.get("location") or ""
        code = airport_code(location)
        if not code:
            continue
        title = event.get("title") or ""
        if _NOT_A_FLIGHT.search(title):
            continue
        departs = parse_instant(event.get("_sort") or "")
        trips.append({
            "title": title,
            "origin": code,
            "destination": _destination_code(location, code),
            "departs": departs,
            "day": event.get("day") or "",
            "time": event.get("time") or "",
            "location": location,
            "terminal": _terminal(location),
            "booked": bool(_BOOKED.search(f"{title} {location}")),
        })
    trips.sort(key=lambda t: (t["departs"] is None, t["departs"] or datetime.max.replace(tzinfo=timezone.utc)))
    return trips


def _destination_code(location: str, origin: str):
    """The airport being flown TO, when the location names two of them.

    Written against how these strings really read, not one tidy shape. A live
    entry is "John Wayne Airport (SNA) -> DAL" or "John Wayne (SNA) to Dallas
    Love (DAL)": airline and city names sit between the codes, so a pattern
    requiring them adjacent matches only a fixture. Instead: find every code
    the location names, in order, and take the first one that is not the
    origin. Direction comes from position, which is how a person reads it.
    """
    if not location:
        return None
    codes = []
    for found in re.finditer(r"\b([A-Z]{3})\b", location):
        code = found.group(1)
        # Skip words that merely look like a code. Without this, "Terminal 3,
        # SNA to LAX" can pick up a stray uppercase word as an airport.
        if code in _NOT_AIRPORT_WORDS:
            continue
        if code not in codes:
            codes.append(code)
    for code in codes:
        if code != origin:
            return code
    return None


# Three-letter uppercase runs that are never airports. A stoplist alone
# cannot be trusted here: an airline confirmation is full of them (WED, MON,
# JAN, USD, PDT, CST, ETA), and every one I fail to list silently becomes an
# invented airport. Found in the wild: a Southwest confirmation parsed "Wed,
# Sep 16" and reported the origin as "WED". So the stoplist is the second
# line of defence, and _plausible_airport below is the first.
_NOT_AIRPORT_WORDS = {
    "THE", "AND", "FOR", "VIA", "OFF", "ONE", "TWO", "NEW", "NON",
    "MON", "TUE", "WED", "THU", "FRI", "SAT", "SUN",
    "JAN", "FEB", "MAR", "APR", "MAY", "JUN",
    "JUL", "AUG", "SEP", "OCT", "NOV", "DEC",
    "USD", "EUR", "GBP", "CAD", "AMT", "TAX", "FEE",
    "EST", "EDT", "CST", "CDT", "MST", "MDT", "PST", "PDT", "UTC", "GMT",
    "ETA", "ETD", "SEAT", "ROW", "PDF", "FAA", "TSA", "ADT", "CHD",
}


def _plausible_airport(code: str) -> bool:
    """Whether a three-letter run is really an airport code.

    A known airport is accepted outright. Anything else must at least not be
    a word this file knows is not one. The table is not exhaustive, so an
    unknown code still passes: rejecting a real small airport is a worse
    failure than the occasional odd string, and the caller only ever uses the
    result to fill a gap.
    """
    if not code:
        return False
    code = code.upper()
    if code in AIRPORT_COORDS:
        return True
    return code not in _NOT_AIRPORT_WORDS


def _terminal(location: str):
    """The terminal named in the location, or None.

    Only ever read from the calendar entry. A lookup table of airline
    terminals goes stale without telling anyone, and a confidently wrong
    terminal at 5 AM is the worst thing this feature could print.
    """
    found = re.search(r"\bTerminal\s+([A-Z0-9]{1,2})\b", location or "", re.IGNORECASE)
    return found.group(1).upper() if found else None


def leave_by(departs, drive_minutes: int, buffer_minutes: int, home_tz):
    """The instant to leave home, expressed in the traveller's own timezone.

    `departs` is an aware datetime, so this is instant arithmetic and the
    departure airport's timezone never enters into it. The result is
    converted to `home_tz` because that is the clock the traveller will read
    when deciding whether to get up.

    Returns None when the departure carries no usable instant. A missing
    notice is recoverable; a wrong one is not.
    """
    if departs is None or departs.tzinfo is None:
        return None
    if drive_minutes is None:
        return None
    leave = departs - timedelta(minutes=buffer_minutes) - timedelta(minutes=drive_minutes)
    return leave.astimezone(home_tz)


def plausible_drive(minutes) -> bool:
    """Whether a drive time is worth showing.

    A geocode that lands in the wrong state returns a real number from a real
    API, and it is useless. Bounds catch that without needing to know where
    the user lives.
    """
    if minutes is None:
        return False
    try:
        value = float(minutes)
    except (TypeError, ValueError):
        return False
    return MIN_PLAUSIBLE_DRIVE_MIN <= value <= MAX_PLAUSIBLE_DRIVE_MIN


def home_address():
    """The traveller's home address, from private per-user state.

    Deliberately not in the vault config: the vault is synced, rendered, and
    published, and a home address in it would reach a web page. This reads
    the same private .env that holds refresh tokens.
    """
    return (os.environ.get(HOME_ENV_KEY) or "").strip() or None


def maps_key():
    """The Routes API key, or None when travel runs without live traffic."""
    return (os.environ.get(MAPS_ENV_KEY) or "").strip() or None


def drive_minutes(origin: str, destination: str, depart_at, key: str):
    """Live drive minutes from Routes, or None on any failure.

    `depart_at` is the instant the drive begins, not "now": a 5 AM briefing
    asking about a 6 AM drive wants the traffic the traveller will meet, and
    Routes prices the request on the departure time it is given.

    Every failure returns None. The caller degrades to a notice that says so.
    """
    if not key or not origin or not destination or depart_at is None:
        return None
    body = {
        "origin": {"address": origin},
        "destination": {"address": destination},
        "travelMode": "DRIVE",
        "routingPreference": "TRAFFIC_AWARE",
        "departureTime": depart_at.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    headers = {
        "Content-Type": "application/json",
        "X-Goog-Api-Key": key,
        "X-Goog-FieldMask": "routes.duration",
    }
    try:
        reply = requests.post(ROUTES_URL, json=body, headers=headers, timeout=TIMEOUT)
        if reply.status_code != 200:
            return None
        data = reply.json()
    except Exception:
        return None
    try:
        seconds = data["routes"][0]["duration"]
        return int(round(int(str(seconds).rstrip("s")) / 60))
    except (KeyError, IndexError, TypeError, ValueError):
        return None


# Where the busiest US airports actually are. An IATA code is NOT a place
# name, and handing one to a place-name geocoder is unsound: "DAL" resolves to
# Dali, in Yunnan, China, and returns a real forecast for the wrong continent
# with no error. SNA, EWR and LAX happen to resolve correctly, which is what
# makes the failure so easy to miss. Codes here are answered from the table
# and never geocoded at all.
AIRPORT_COORDS = {
    "ATL": (33.6407, -84.4277, "Atlanta"), "AUS": (30.1975, -97.6664, "Austin"),
    "BNA": (36.1263, -86.6774, "Nashville"), "BOS": (42.3656, -71.0096, "Boston"),
    "BUR": (34.2007, -118.3590, "Burbank"), "BWI": (39.1774, -76.6684, "Baltimore"),
    "CLT": (35.2144, -80.9473, "Charlotte"), "DAL": (32.8471, -96.8518, "Dallas Love"),
    "DCA": (38.8512, -77.0402, "Washington National"),
    "DEN": (39.8561, -104.6737, "Denver"), "DFW": (32.8998, -97.0403, "Dallas Fort Worth"),
    "DTW": (42.2162, -83.3554, "Detroit"), "EWR": (40.6895, -74.1745, "Newark"),
    "FLL": (26.0742, -80.1506, "Fort Lauderdale"), "HOU": (29.6454, -95.2789, "Houston Hobby"),
    "IAD": (38.9531, -77.4565, "Washington Dulles"), "IAH": (29.9902, -95.3368, "Houston"),
    "JFK": (40.6413, -73.7781, "New York JFK"), "LAS": (36.0840, -115.1537, "Las Vegas"),
    "LAX": (33.9416, -118.4085, "Los Angeles"), "LGA": (40.7769, -73.8740, "New York LaGuardia"),
    "LGB": (33.8177, -118.1516, "Long Beach"), "MCO": (28.4312, -81.3081, "Orlando"),
    "MDW": (41.7868, -87.7522, "Chicago Midway"), "MIA": (25.7959, -80.2870, "Miami"),
    "MSP": (44.8848, -93.2223, "Minneapolis"), "OAK": (37.7126, -122.2197, "Oakland"),
    "ONT": (34.0560, -117.6012, "Ontario"), "ORD": (41.9742, -87.9073, "Chicago O'Hare"),
    "PDX": (45.5898, -122.5951, "Portland"), "PHL": (39.8744, -75.2424, "Philadelphia"),
    "PHX": (33.4342, -112.0116, "Phoenix"), "RDU": (35.8801, -78.7880, "Raleigh Durham"),
    "SAN": (32.7338, -117.1933, "San Diego"), "SEA": (47.4502, -122.3088, "Seattle"),
    "SFO": (37.6213, -122.3790, "San Francisco"), "SJC": (37.3639, -121.9289, "San Jose"),
    "SLC": (40.7899, -111.9791, "Salt Lake City"), "SNA": (33.6757, -117.8683, "Orange County"),
    "STL": (38.7487, -90.3700, "St Louis"), "TPA": (27.9755, -82.5332, "Tampa"),
}


def forecast(place: str):
    """A one-line forecast for a place, or None.

    Open-Meteo needs no key, so this runs for every user whether or not they
    have set up live traffic. A known airport code is answered from the
    coordinate table above; anything else is resolved by name, and the
    resolved name is returned with the forecast so an ambiguous destination
    (Portland, Birmingham) is visible to the reader rather than silently
    wrong.
    """
    if not place:
        return None
    known = AIRPORT_COORDS.get(place.strip().upper())
    try:
        if known:
            lat, lon, label = known
            spot = {"name": label, "admin1": "", "country": "",
                    "latitude": lat, "longitude": lon}
        else:
            found = requests.get(
                GEOCODE_URL, params={"name": place, "count": 1}, timeout=TIMEOUT
            )
            if found.status_code != 200:
                return None
            results = (found.json() or {}).get("results") or []
            if not results:
                return None
            spot = results[0]
        weather = requests.get(
            WEATHER_URL,
            params={
                "latitude": spot["latitude"],
                "longitude": spot["longitude"],
                "daily": "temperature_2m_max,temperature_2m_min,precipitation_probability_max",
                "temperature_unit": "fahrenheit",
                "forecast_days": 3,
                "timezone": "auto",
            },
            timeout=TIMEOUT,
        )
        if weather.status_code != 200:
            return None
        daily = (weather.json() or {}).get("daily") or {}
    except Exception:
        return None
    try:
        return {
            "place": spot.get("name") or place,
            "region": spot.get("admin1") or "",
            "country": spot.get("country") or "",
            "high": daily["temperature_2m_max"][0],
            "low": daily["temperature_2m_min"][0],
            "rain_chance": daily["precipitation_probability_max"][0],
        }
    except (KeyError, IndexError, TypeError):
        return None


def flights_link(origin: str, destination: str, depart_date: str) -> str:
    """A Google Flights search for a trip that has no booking yet."""
    query = f"flights from {origin} to {destination} on {depart_date}"
    return f"{FLIGHTS_URL}?" + urlencode({"q": query})


# ── The notice ledger ─────────────────────────────────────────────────────────
# One file recording which notices have been shown and how many paid lookups
# ran today. It is written AFTER a notice is rendered, never before, so a
# crash mid-render cannot leave a record claiming the traveller was told
# something they never saw.

def ledger_path() -> Path:
    import config_loader
    return config_loader.logs_dir() / "travel_notices.json"


def load_ledger(path: Path | None = None) -> dict:
    """The ledger, or an empty one.

    A missing, empty, or corrupt file is not an error worth failing a
    briefing over. The cost of rebuilding it is one repeated notice.
    """
    path = path or ledger_path()
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return {"shown": [], "routes_calls": {}}
    if not isinstance(data, dict):
        return {"shown": [], "routes_calls": {}}
    data.setdefault("shown", [])
    data.setdefault("routes_calls", {})
    if not isinstance(data["shown"], list):
        data["shown"] = []
    if not isinstance(data["routes_calls"], dict):
        data["routes_calls"] = {}
    return data


def prune_ledger(ledger: dict, today: str) -> dict:
    """Drop notices and call counts older than the retention window."""
    try:
        cutoff = (datetime.strptime(today, "%Y-%m-%d")
                  - timedelta(days=LEDGER_RETENTION_DAYS)).strftime("%Y-%m-%d")
    except ValueError:
        return ledger
    ledger["shown"] = [
        entry for entry in ledger["shown"]
        if isinstance(entry, dict) and (entry.get("date") or "") >= cutoff
    ]
    ledger["routes_calls"] = {
        day: count for day, count in ledger["routes_calls"].items()
        if day >= cutoff
    }
    return ledger


def notice_key(trip: dict, kind: str, briefing: str = "") -> str:
    """A stable id for one notice about one trip, in one briefing.

    Keyed on the trip's own departure instant rather than the day it is
    shown, so the same notice cannot fire twice, while a different kind of
    notice about the same trip still can.

    Keyed on the BRIEFING too, because more than one of them now reads these
    notices. A shared key would mean whichever briefing ran first consumed
    the notice and the others went silent about the trip, which is worst
    exactly where it matters most: Afternoon Tea exists to warn about an
    early flight the evening before, and it would have been suppressed by
    that morning's Morning Coffee. The default is empty, which reproduces
    the original key, so a ledger written before this still suppresses the
    notices it recorded.
    """
    stamp = trip["departs"].isoformat() if trip.get("departs") else trip.get("day", "")
    prefix = f"{briefing}:" if briefing else ""
    return f"{prefix}{kind}:{trip.get('origin', '')}:{stamp}"


def already_shown(ledger: dict, key: str) -> bool:
    return any(
        isinstance(entry, dict) and entry.get("key") == key
        for entry in ledger.get("shown", [])
    )


def record_shown(ledger: dict, key: str, today: str) -> dict:
    if not already_shown(ledger, key):
        ledger["shown"].append({"key": key, "date": today})
    return ledger


def routes_calls_today(ledger: dict, today: str) -> int:
    try:
        return int(ledger.get("routes_calls", {}).get(today, 0))
    except (TypeError, ValueError):
        return 0


def routes_budget_left(ledger: dict, today: str) -> bool:
    return routes_calls_today(ledger, today) < ROUTES_DAILY_CAP


def record_routes_call(ledger: dict, today: str) -> dict:
    ledger.setdefault("routes_calls", {})
    ledger["routes_calls"][today] = routes_calls_today(ledger, today) + 1
    return ledger


def save_ledger(ledger: dict, path: Path | None = None) -> None:
    path = path or ledger_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(ledger, f, indent=2)
    except OSError:
        pass


# ── Building the notices ──────────────────────────────────────────────────────

def _fmt_leave(dt) -> str:
    """A leave-by time a person reads at 5 AM, with the zone named.

    The zone is named because the whole point of the arithmetic is that the
    flight's clock and the traveller's clock differ. Hour formatting goes
    through platform_compat: %-I is POSIX-only and this ships to Windows.
    """
    import platform_compat
    return f"{platform_compat.fmt_hour_minute(dt)} {dt.strftime('%Z')}"


def _fmt_date(d) -> str:
    """"Nov 12" with no leading zero, without POSIX-only flags."""
    return f"{d.strftime('%b')} {d.day}"


def build_notices(trips, today_str, home_tz, ledger, buffer_minutes,
                  weather_lead_days=2, flights_min_days=3,
                  drive_lookup=None, weather_lookup=None, briefing="",
                  departure_lead_days=0):
    """The travel notices due today, and the ledger updated to record them.

    Pure with respect to the network: the two lookups are injected so the
    caller decides whether live calls happen, and the tests can drive every
    failure shape without touching a wire.

    Returns (notices, ledger). The caller saves the ledger only after the
    notices have actually been rendered.
    """
    drive_lookup = drive_lookup or (lambda *a, **k: None)
    weather_lookup = weather_lookup or (lambda *a, **k: None)
    notices = []
    try:
        today = datetime.strptime(today_str, "%Y-%m-%d").date()
    except ValueError:
        return notices, ledger

    for trip in trips:
        departs = trip.get("departs")
        if departs is None:
            continue
        trip_date = departs.astimezone(home_tz).date()
        days_out = (trip_date - today).days
        if days_out < 0:
            continue

        # Departure day: the notice that has to be right.
        #
        # `departure_lead_days` is how far ahead a briefing counts as
        # "departure". Morning Coffee keeps 0, the morning of. An evening
        # briefing uses 1, because a 6 AM flight told about at 5:55 AM is
        # already too late to pack for, and the evening before is not.
        if days_out <= departure_lead_days:
            key = notice_key(trip, NOTICE_DEPARTURE, briefing)
            if already_shown(ledger, key):
                continue
            notice = _departure_notice(trip, departs, home_tz, ledger,
                                       buffer_minutes, today_str, drive_lookup)
            notices.append(notice)
            ledger = record_shown(ledger, key, today_str)
            continue

        # Two days out: what to pack.
        if days_out == weather_lead_days and trip.get("destination"):
            key = notice_key(trip, NOTICE_WEATHER, briefing)
            if not already_shown(ledger, key):
                weather = weather_lookup(trip["destination"])
                if weather:
                    notices.append({
                        "kind": NOTICE_WEATHER,
                        "trip": trip["title"],
                        "text": _strip_dashes(
                            f"{trip['destination']} in two days: "
                            f"{weather['place']} is {round(weather['low'])} to "
                            f"{round(weather['high'])} degrees, "
                            f"{round(weather['rain_chance'])}% chance of rain."
                        ),
                    })
                    ledger = record_shown(ledger, key, today_str)
            continue

        # Far enough out to still be booking.
        if (days_out >= flights_min_days and trip.get("destination")
                and not trip.get("booked")):
            key = notice_key(trip, NOTICE_FLIGHTS, briefing)
            if not already_shown(ledger, key):
                link = flights_link(trip["origin"], trip["destination"],
                                    trip_date.strftime("%Y-%m-%d"))
                notices.append({
                    "kind": NOTICE_FLIGHTS,
                    "trip": trip["title"],
                    "text": _strip_dashes(
                        f"No flight booked yet for {trip['origin']} to "
                        f"{trip['destination']} on "
                        f"{_fmt_date(trip_date)}."
                    ),
                    "link": link,
                })
                ledger = record_shown(ledger, key, today_str)

    return notices, ledger


def _departure_notice(trip, departs, home_tz, ledger, buffer_minutes,
                      today_str, drive_lookup):
    """The morning-of notice: where to go and when to leave.

    Built so that every part that cannot be established is simply absent.
    A notice that says the drive time is unavailable is useful; one that
    invents a leave-by time is not.
    """
    parts = [f"Flight from {trip['origin']}"]
    if trip.get("terminal"):
        parts.append(f"Terminal {trip['terminal']}")
    where = ", ".join(parts) + "."

    minutes = None
    address = home_address()
    key = maps_key()
    if address and key and routes_budget_left(ledger, today_str):
        rough_leave = departs - timedelta(minutes=buffer_minutes)
        try:
            minutes = drive_lookup(address, f"{trip['origin']} Airport",
                                   rough_leave, key)
        except Exception:
            # Deliberately swallowed without its text. An exception raised
            # while looking up a route routinely quotes the arguments, and
            # those arguments are the home address and the API key. That
            # string would land in output["errors"], which the briefing
            # renders and the web page publishes.
            minutes = None
        ledger = record_routes_call(ledger, today_str)

    if not plausible_drive(minutes):
        # Name the thing that is actually missing. One message covering three
        # different causes sends the reader to fix something that is already
        # set: with an address present and no key, "set a home address" is a
        # false instruction, and they will go looking at the wrong file.
        if address and key:
            tail = "Live drive time is unavailable, so allow your usual time."
        elif not address and not key:
            tail = ("Add your home address and a Maps key to get drive time "
                    "and a leave by time.")
        elif not address:
            tail = "Add your home address to get drive time and a leave by time."
        else:
            tail = ("No Maps key is set, so there is no live drive time. "
                    "Allow your usual time.")
        return {
            "kind": NOTICE_DEPARTURE,
            "trip": trip["title"],
            "text": _strip_dashes(f"{where} {tail}"),
            "leave_by": None,
            "drive_minutes": None,
        }

    minutes = int(round(float(minutes)))
    leave = leave_by(departs, minutes, buffer_minutes, home_tz)
    if leave is None:
        return {
            "kind": NOTICE_DEPARTURE,
            "trip": trip["title"],
            "text": _strip_dashes(f"{where} Drive is about {minutes} minutes."),
            "leave_by": None,
            "drive_minutes": minutes,
        }

    return {
        "kind": NOTICE_DEPARTURE,
        "trip": trip["title"],
        "text": _strip_dashes(
            f"{where} Drive is about {minutes} minutes with current traffic. "
            f"Leave home by {_fmt_leave(leave)} to be there "
            f"{buffer_minutes} minutes early."
        ),
        "leave_by": _fmt_leave(leave),
        "drive_minutes": minutes,
    }


TRAVEL_HEADING = "## Travel"

# Departure first: it is the notice with a clock on it. Then weather, which is
# what to pack, then an unbooked flight, which is the only one that can wait.
_KIND_ORDER = {NOTICE_DEPARTURE: 0, NOTICE_WEATHER: 1, NOTICE_FLIGHTS: 2}


def render_travel_md(notices, mode: str = "md") -> str:
    """The Travel block, or "" when nothing is due.

    Rendered by code, like the front page and the drafts block, because every
    word of a notice was already written by `build_notices` against the
    flight's own timezone. A model asked to present these could reorder them,
    round a time, or drop the caveat that says a drive time is unavailable,
    and the reader has no way to tell that happened. The one number here that
    can be wrong is a leave-by time, and being wrong about it means a missed
    flight.

    `mode` is "md" for the written file, where a booking link is a hyperlink,
    and "terminal" for the chat render, where a bare URL is noise.
    """
    if mode not in ("md", "terminal"):
        raise ValueError(f"unknown mode {mode!r}")
    rows = [n for n in (notices or []) if isinstance(n, dict) and n.get("text")]
    if not rows:
        return ""

    rows = sorted(rows, key=lambda n: (_KIND_ORDER.get(n.get("kind"), 9),
                                       str(n.get("trip") or "")))
    out = [TRAVEL_HEADING, ""]
    for n in rows:
        text = str(n["text"]).strip()
        link = str(n.get("link") or "").strip()
        if link and mode == "md":
            # The trip's own words carry the link, not a "Find flights" label
            # after them. On the Workbench that link becomes a button whose
            # face already says Find flights, and a label linked to a button
            # of the same name reads as the same control printed twice.
            out.append(f"- [{text}]({link})")
        elif link:
            out.append(f"- {text}")
            out.append(f"  {link}")
        else:
            out.append(f"- {text}")
    out.append("")
    return "\n".join(out)


def collect(events, today_str, home_tz, buffer_minutes, live=True,
            briefing="", weather_lead_days=2, flights_min_days=3,
            departure_lead_days=0):
    """Every travel notice due today, for one briefing.

    `briefing` keys the ledger, so each briefing shows a given notice once.
    `weather_lead_days` and `flights_min_days` move the windows: an evening
    briefing wants tomorrow's departure, and a Monday briefing wants the
    whole week's unbooked flights, which are different questions about the
    same trips.

    Never raises: a failure here returns an empty list and a note, because a
    broken travel section must not cost the reader the rest of the briefing.
    """
    try:
        ledger = prune_ledger(load_ledger(), today_str)
        trips = detect_trips(events)
        # Fill each trip's gaps from the airline confirmation. Only worth the
        # mailbox round trip when something is actually missing, which on a
        # well-filled calendar entry is nothing.
        if live and trips and any(
                not t.get("destination") or not t.get("terminal") for t in trips):
            confirmations = fetch_confirmations()
            if confirmations:
                trips = [enrich_trip(t, confirmations) for t in trips]
        notices, ledger = build_notices(
            trips, today_str, home_tz, ledger, buffer_minutes,
            weather_lead_days=weather_lead_days,
            flights_min_days=flights_min_days,
            departure_lead_days=departure_lead_days,
            drive_lookup=drive_minutes if live else None,
            weather_lookup=forecast if live else None,
            briefing=briefing,
        )
        # NOT saved here. The ledger records "the reader was told this", and
        # at this point they have not been: morning_coffee still has to print
        # the JSON and the skill still has to render it. Writing now means a
        # crash between here and the render silently costs the traveller a
        # notice they never saw. The caller commits once the output is out.
        return {"notices": notices, "trips": len(trips), "error": "",
                "commit": (lambda: save_ledger(ledger)) if notices else (lambda: None)}
    except Exception as e:
        # The exception TYPE only, never its text. An exception raised
        # anywhere in this path can quote the home address or the API key,
        # and this string is appended to output["errors"], which the briefing
        # renders and the published page carries. briefing_html.redact strips
        # credential shapes and file paths, not street addresses, so nothing
        # downstream would catch it. The type is enough to tell the reader
        # the section failed, which is all they can act on anyway.
        return {"notices": [], "trips": 0,
                "error": f"Travel notices failed ({type(e).__name__})."}


# ── Reading the confirmation email ───────────────────────────────────────────
# A calendar entry is usually just "Flight to Dallas". The detail a traveller
# needs at 4 AM, which airport, which terminal, which flight, lives in the
# airline's confirmation email and nowhere else. These parse that email.
#
# Everything here is a pure function over text so it can be tested against
# real message shapes with no network and no mailbox.

# Airlines whose confirmations are worth reading. Matched against the sender
# domain, so a newsletter ABOUT United does not qualify as a booking.
AIRLINE_DOMAINS = (
    "united.com", "delta.com", "aa.com", "southwest.com", "alaskaair.com",
    "jetblue.com", "flyfrontier.com", "spirit.com", "hawaiianairlines.com",
    "allegiantair.com", "aircanada.ca", "british-airways.com", "lufthansa.com",
    "airfrance.com", "klm.com", "emirates.com", "qatarairways.com",
    "singaporeair.com", "cathaypacific.com", "virginatlantic.com",
)

_CONFIRMATION_SUBJECT = re.compile(
    r"\b(?:confirm|confirmation|itinerary|e-?ticket|boarding|"
    r"your (?:flight|trip)|reservation|record locator)\b",
    re.IGNORECASE,
)

# "SNA to DAL", "SNA - DAL", "SNA > DAL", "Depart SNA", "Arrive DAL".
_LEG = re.compile(
    r"\b([A-Z]{3})\b\s*(?:to|->|-|>|→)\s*\b([A-Z]{3})\b")
# Allow the value to sit BELOW its label, not just beside it. A flattened
# table puts "DEPART" at the head of a column and the airport code two lines
# down, past a date and a time, which is the commonest real layout.
_DEPART_FROM = re.compile(
    r"\b(?:depart(?:ing|ure|s)?|from|origin|leaving)\b"
    r"(?:[^A-Z]|[A-Z][a-z]+|\d)*?\(?\b([A-Z]{3})\b\)?",
    re.IGNORECASE)
_ARRIVE_AT = re.compile(
    r"\b(?:arriv(?:e|ing|al|es)?|to|destination)\b"
    r"(?:[^A-Z]|[A-Z][a-z]+|\d)*?\(?\b([A-Z]{3})\b\)?",
    re.IGNORECASE)

_FLIGHT_NO = re.compile(r"\b([A-Z]{2})\s?(\d{1,4})\b")
_TERMINAL = re.compile(r"\bterminal\s*:?\s*([A-Z0-9]{1,2})\b", re.IGNORECASE)
_GATE = re.compile(r"\bgate\s*:?\s*([A-Z0-9]{1,4})\b", re.IGNORECASE)
_CONFIRMATION_CODE = re.compile(
    r"\b(?:confirmation|record\s+locator|booking|PNR)\b"
    r"(?:\s*(?:code|number|reference|#|:))*\s*\b([A-Z0-9]{5,7})\b",
    re.IGNORECASE)


def looks_like_a_confirmation(sender: str, subject: str) -> bool:
    """Whether an email is an airline booking confirmation.

    Both halves are required. A subject alone matches trip newsletters and
    "confirm your email address"; a sender alone matches fare sales and
    loyalty statements. Requiring both is what keeps a 4 AM notice from being
    built out of marketing copy.
    """
    if not sender or not subject:
        return False
    domain = sender.lower().rsplit("@", 1)[-1].strip(" <>")
    if not any(domain == d or domain.endswith("." + d) for d in AIRLINE_DOMAINS):
        return False
    return bool(_CONFIRMATION_SUBJECT.search(subject))


def _rejoin_split_values(text: str) -> str:
    """Repair values an HTML table split across cells.

    Airline confirmations are laid out in tables, and flattening one to text
    routinely separates a label from its value by a run of whitespace and
    newlines: "Terminal\\n\\n3", "SNA\\n\\nto\\n\\nDAL". Collapsing runs of
    blank space to a single space puts them back together without touching
    anything else.
    """
    return re.sub(r"[ \t]*\n[ \t\n]*", "\n", text or "").replace("\n", " \n ")


_DEPART_LABEL = re.compile(
    r"\b(?:depart(?:ing|ure|s)?|from|origin|leaving)\b", re.IGNORECASE)
_ARRIVE_LABEL = re.compile(
    r"\b(?:arriv(?:e|ing|al|es)?|to|destination)\b", re.IGNORECASE)
_CODE_TOKEN = re.compile(r"\b([A-Z]{3})\b")

# How far past a label to keep looking. A flattened table puts a date and a
# time between "DEPART" and the airport code, so the answer is several tokens
# away, but not paragraphs away: past this the next section has started.
_CODE_SEARCH_WINDOW = 120


def _code_after(text: str, label: re.Pattern, exclude: str | None = None):
    """The first real airport code following a label.

    Written as a forward scan rather than one regex because the distance is
    unpredictable: a tidy prose confirmation puts the code on the same line,
    and a flattened table puts it three lines down past a date. A single
    pattern either stops at the first three-letter run, which is usually a
    weekday, or runs away across the whole message.
    """
    for hit in label.finditer(text):
        window = text[hit.end():hit.end() + _CODE_SEARCH_WINDOW]
        for found in _CODE_TOKEN.finditer(window):
            code = found.group(1).upper()
            if not _plausible_airport(code):
                continue
            if exclude and code == exclude:
                continue
            return code
    return None


def parse_confirmation(body: str, subject: str = "") -> dict:
    """Airport codes, flight number, terminal and booking code from an email.

    Returns only what the text actually said. Every field is independently
    optional, because a partial answer (the destination but no terminal) is
    still worth having and a guessed one is not.
    """
    text = _rejoin_split_values(f"{subject}\n{body or ''}")
    out = {"origin": None, "destination": None, "flight": None,
           "terminal": None, "gate": None, "confirmation": None}

    leg = _LEG.search(text)
    if leg:
        first, second = leg.group(1).upper(), leg.group(2).upper()
        if _plausible_airport(first) and _plausible_airport(second):
            out["origin"], out["destination"] = first, second
    if not out["origin"]:
        out["origin"] = _code_after(text, _DEPART_LABEL)
    if not out["destination"]:
        found = _code_after(text, _ARRIVE_LABEL, exclude=out["origin"])
        if found:
            out["destination"] = found

    flight = _FLIGHT_NO.search(text)
    if flight:
        out["flight"] = f"{flight.group(1).upper()}{flight.group(2)}"

    for key, pattern in (("terminal", _TERMINAL), ("gate", _GATE),
                         ("confirmation", _CONFIRMATION_CODE)):
        found = pattern.search(text)
        if found:
            out[key] = found.group(1).upper()

    return out


def enrich_trip(trip: dict, confirmations: list) -> dict:
    """Fill a trip's gaps from a matching confirmation email.

    Matched on the origin airport, which the calendar almost always has, plus
    the departure date. The email NEVER overrides something the calendar
    already said: a person editing their own calendar entry is stating a fact
    about their own trip, and a month-old confirmation is not grounds to
    contradict them. It only fills what is missing.
    """
    if not trip.get("departs"):
        return trip
    trip_date = trip["departs"].date()
    for conf in confirmations or []:
        if conf.get("origin") and conf["origin"] != trip.get("origin"):
            continue
        conf_date = conf.get("date")
        if conf_date and conf_date != trip_date:
            continue
        for field in ("destination", "terminal", "gate", "flight", "confirmation"):
            if not trip.get(field) and conf.get(field):
                trip[field] = conf[field]
        if conf.get("confirmation") or conf.get("flight"):
            trip["booked"] = True
        break
    return trip


def fetch_confirmations(days_back: int = 60, days_ahead: int = 30) -> list:
    """Airline confirmations from the mailbox, parsed.

    Tier 1 only: this needs live mail access. Returns an empty list on any
    failure, including no OAuth, because a travel notice missing its terminal
    is worth having and a briefing that dies fetching mail is not.

    Read-only, and narrow on purpose. The query names the airline senders
    rather than scanning the inbox, so the volume is a handful of messages
    even for a frequent traveller.
    """
    try:
        import data_sources
        if not data_sources.is_tier1():
            return []
        import config_loader
        from google_client import google_client
    except ImportError:
        return []

    senders = " OR ".join(f"from:{d}" for d in AIRLINE_DOMAINS)
    since = (datetime.now(timezone.utc) - timedelta(days=days_back)).strftime("%Y/%m/%d")
    out = []
    for account in config_loader.google_accounts():
        try:
            client = google_client(account["label"])
            listed = client.gmail.users().messages().list(
                userId="me", q=f"({senders}) after:{since}", maxResults=25,
            ).execute()
        except Exception:
            continue
        for stub in listed.get("messages", []) or []:
            try:
                msg = client.gmail.users().messages().get(
                    userId="me", id=stub["id"], format="full",
                ).execute(http=client.new_http())
            except Exception:
                continue
            headers = {h["name"]: h["value"]
                       for h in (msg.get("payload") or {}).get("headers", [])}
            sender = headers.get("From", "")
            subject = headers.get("Subject", "")
            if not looks_like_a_confirmation(sender, subject):
                continue
            parsed = parse_confirmation(_message_body(msg.get("payload") or {}), subject)
            parsed["date"] = _confirmation_date(msg, parsed)
            out.append(parsed)
    return out


def _message_body(payload: dict) -> str:
    """The text/plain body of a Gmail message, walking nested parts."""
    import base64
    if payload.get("mimeType") == "text/plain":
        data = (payload.get("body") or {}).get("data", "")
        if data:
            return base64.urlsafe_b64decode(data + "==").decode("utf-8", errors="replace")
    for part in payload.get("parts", []) or []:
        body = _message_body(part)
        if body:
            return body
    return ""


def _confirmation_date(msg: dict, parsed: dict):
    """The date the flight departs, as named in the email.

    Deliberately NOT the date the email arrived: a confirmation is sent weeks
    before the trip, so matching on arrival would attach it to the wrong one.
    Returns None when the text names no date, and enrich_trip then matches on
    the airport alone.
    """
    text = msg.get("snippet", "") or ""
    found = re.search(
        r"\b(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\s+(\d{1,2})"
        r"(?:,?\s+(\d{4}))?", text)
    if not found:
        return None
    months = {"Jan": 1, "Feb": 2, "Mar": 3, "Apr": 4, "May": 5, "Jun": 6,
              "Jul": 7, "Aug": 8, "Sep": 9, "Oct": 10, "Nov": 11, "Dec": 12}
    try:
        year = int(found.group(3)) if found.group(3) else datetime.now().year
        return datetime(year, months[found.group(1)], int(found.group(2))).date()
    except (ValueError, KeyError):
        return None
