"""Notification sink management + test push (admin-gated)."""
import pytest

pytestmark = pytest.mark.api

ADMIN = {"Authorization": "Bearer secret-admin"}


def test_sinks_crud_masks_url_and_refreshes_notifier(auth_client):
    app = auth_client.app
    # Create a DB sink.
    r = auth_client.post(
        "/api/alerts/sinks",
        json={"type": "slack", "url": "https://hooks.slack.com/services/T/B/SECRETxyz",
              "min_severity": "warning"},
        headers=ADMIN,
    )
    assert r.status_code == 201
    body = r.json()
    sink_id = body["id"]
    assert body["source"] == "db" and "SECRET" not in body["url_preview"]
    assert body["url_preview"].startswith("https://hooks.slack.com/")

    # It shows up in the list (masked) and the live notifier picked it up.
    listed = auth_client.get("/api/alerts/sinks", headers=ADMIN).json()
    assert any(s["id"] == sink_id and s["type"] == "slack" for s in listed)
    assert any(sk.type == "slack" for sk in app.state.notifier.sinks)

    # Delete -> gone from list and from the notifier.
    assert auth_client.delete(f"/api/alerts/sinks/{sink_id}", headers=ADMIN).status_code == 204
    assert auth_client.delete(f"/api/alerts/sinks/{sink_id}", headers=ADMIN).status_code == 404
    assert not any(sk.type == "slack" for sk in app.state.notifier.sinks)


def test_test_push_sends_to_sink(auth_client):
    app = auth_client.app
    r = auth_client.post(
        "/api/alerts/sinks",
        json={"type": "webhook", "url": "http://hook/x"}, headers=ADMIN,
    )
    sink_id = r.json()["id"]
    before = len(app.state.http_client.posts)
    res = auth_client.post("/api/alerts/test", json={"id": sink_id}, headers=ADMIN)
    assert res.status_code == 200
    results = res.json()["results"]
    assert results and results[0]["ok"] is True
    assert len(app.state.http_client.posts) == before + 1  # actually dispatched


def test_test_push_with_no_sinks_is_400(auth_client):
    # Fresh notifier with no sinks (env empty in tests).
    assert not auth_client.app.state.notifier.sinks
    assert auth_client.post("/api/alerts/test", json={}, headers=ADMIN).status_code == 400


def test_alerts_require_admin(auth_client):
    assert auth_client.get("/api/alerts/sinks").status_code == 401
    assert auth_client.post("/api/alerts/sinks",
                            json={"type": "slack", "url": "x"}).status_code == 401
