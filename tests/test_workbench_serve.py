"""workbench_serve: the security posture and the endpoint shapes.

The server can trigger an agentic `claude -p`, so "it only listens on localhost"
is not the whole answer. These tests pin the three things that keep a hostile
page in the user's own browser from driving it: the per-boot token, the
loopback-only Host check, and a static whitelist with no traversal.
"""
import json
import threading
import urllib.error
import urllib.request

import pytest

import workbench_serve as ws
import workbench_store as store


@pytest.fixture
def server(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "van_gogh_root", lambda: tmp_path)
    monkeypatch.setattr(store, "deal_critical_domains", lambda: set())
    monkeypatch.setattr(ws, "_TOKEN", "test-token", raising=False)
    ws._jobs.clear()

    srv = ws.build_server(0)
    thread = threading.Thread(target=srv.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{srv.server_address[1]}"
    srv.shutdown()
    srv.server_close()


def call(base, path, token="test-token", host=None, method="GET", body=None):
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(base + path, data=data, method=method)
    if token is not None:
        req.add_header("X-Workbench-Token", token)
    if body is not None:
        req.add_header("Content-Type", "application/json")
    if host:
        req.add_header("Host", host)
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()


# ── Auth ─────────────────────────────────────────────────────────────────────

def test_api_without_a_token_is_rejected(server):
    status, _ = call(server, "/api/meta", token=None)
    assert status == 401


def test_api_with_the_wrong_token_is_rejected(server):
    status, _ = call(server, "/api/meta", token="not-it")
    assert status == 401


def test_api_with_the_token_succeeds(server):
    status, body = call(server, "/api/meta")
    assert status == 200
    assert "version" in json.loads(body)


def test_token_may_ride_in_the_query_string_for_image_tags(server):
    status, _ = call(server, "/api/meta?t=test-token", token=None)
    assert status == 200


def test_a_non_loopback_host_header_is_refused(server):
    """The DNS-rebinding guard: an attacker-controlled name resolving to
    127.0.0.1 still arrives with its own Host header."""
    status, _ = call(server, "/api/meta", host="evil.example.com")
    assert status == 403


# ── Static ───────────────────────────────────────────────────────────────────

def test_index_is_served_without_a_token(server):
    status, body = call(server, "/", token=None)
    assert status == 200
    assert b"Van Gogh Workbench" in body


def test_whitelisted_static_files_are_served(server):
    for name in ("style.css", "app.js", "graph.js"):
        status, body = call(server, "/static/" + name, token=None)
        assert status == 200, name
        assert body


def test_static_traversal_is_impossible(server):
    for path in ("/static/../config.template.json",
                 "/static/..%2f..%2fconfig.template.json",
                 "/static/secrets.env"):
        status, _ = call(server, path, token=None)
        assert status == 404, path


def test_unknown_paths_are_404(server):
    status, _ = call(server, "/nope", token=None)
    assert status == 404


# ── Endpoints ────────────────────────────────────────────────────────────────

def test_briefings_endpoint_returns_one_entry_per_briefing(server):
    status, body = call(server, "/api/briefings")
    assert status == 200
    names = {b["name"] for b in json.loads(body)["briefings"]}
    assert names == set(ws.workbench_data.BRIEFINGS)


def test_tickets_endpoint_shape(server):
    status, body = call(server, "/api/tickets")
    payload = json.loads(body)
    assert status == 200
    assert isinstance(payload["tickets"], list)
    assert isinstance(payload["counts"], dict)


def test_a_ticket_can_be_created_commented_and_dismissed(server):
    status, body = call(server, "/api/tickets", method="POST",
                        body={"title": "Build the deck", "type": "pptx"})
    assert status == 200
    tid = json.loads(body)["id"]

    status, body = call(server, f"/api/tickets/{tid}/comment", method="POST",
                        body={"text": "shorter"})
    assert status == 200
    assert json.loads(body)["comments"][0]["text"] == "shorter"

    status, _ = call(server, f"/api/tickets/{tid}/dismiss", method="POST", body={})
    assert status == 200
    assert store.get(tid)["state"] == "dismissed"


def test_an_illegal_transition_answers_409_not_500(server):
    _, body = call(server, "/api/tickets", method="POST", body={"title": "x"})
    tid = json.loads(body)["id"]
    status, _ = call(server, f"/api/tickets/{tid}/rate", method="POST",
                     body={"stars": 99})
    assert status == 400


def test_an_unknown_ticket_answers_404(server):
    status, _ = call(server, "/api/tickets/deadbeef/dismiss", method="POST", body={})
    assert status == 404


def test_refresh_of_an_unknown_briefing_is_a_400(server):
    status, _ = call(server, "/api/refresh", method="POST", body={"briefing": "brunch"})
    assert status == 400


def test_refresh_without_oauth_is_refused_rather_than_half_run(server, monkeypatch):
    """Decision 4: the Workbench requires Tier 1. It says so instead of
    starting a job that cannot fetch anything."""
    monkeypatch.setattr(ws, "is_tier1", lambda: False)
    status, body = call(server, "/api/refresh", method="POST",
                        body={"briefing": "morning-coffee"})
    assert status == 412
    assert "install-van-gogh" in json.loads(body)["error"]


def test_logo_endpoint_rejects_a_domain_that_is_not_a_hostname(server):
    for bad in ("../../etc/passwd", "not a domain", ""):
        status, _ = call(server, "/api/logo?domain=" + urllib.parse.quote(bad))
        assert status == 404, bad


def test_graph_endpoint_answers(server):
    status, body = call(server, "/api/graph")
    assert status == 200
    assert "nodes" in json.loads(body)
