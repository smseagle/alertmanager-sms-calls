"""Endpoint tests for the FastAPI app with a mocked SMSEagle client.

The SMSEagle client (app.client) is a module global created at import time.
We replace its methods with AsyncMock so no real HTTP calls are made.
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import httpx
import pytest
from fastapi.testclient import TestClient

from smseagle_adapter import app as app_module
from conftest import make_alert, make_payload

# Token matching ADAPTER_WEBHOOK_TOKEN set in conftest
AUTH = {"Authorization": "Bearer secret123"}


@pytest.fixture()
def client(monkeypatch):
    """TestClient + mocked SMSEagle client methods."""
    send_sms = AsyncMock(return_value={"status": "queued", "id": 297})
    send_tts = AsyncMock(return_value={"status": "queued"})
    monkeypatch.setattr(app_module.client, "send_sms", send_sms)
    monkeypatch.setattr(app_module.client, "send_tts_call", send_tts)
    tc = TestClient(app_module.app)
    # expose the mocks to the tests
    tc.send_sms = send_sms  # type: ignore[attr-defined]
    tc.send_tts = send_tts  # type: ignore[attr-defined]
    return tc


# --------------------------------------------------------------------------- #
# Health                                                                       #
# --------------------------------------------------------------------------- #
def test_healthz(client):
    resp = client.get("/healthz")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


# --------------------------------------------------------------------------- #
# Webhook authentication                                                       #
# --------------------------------------------------------------------------- #
def test_alert_rejected_without_token(client):
    payload = make_payload([make_alert()])
    resp = client.post("/alert", json=payload)  # no Authorization
    assert resp.status_code == 401
    client.send_sms.assert_not_awaited()


def test_alert_rejected_with_wrong_token(client):
    payload = make_payload([make_alert()])
    resp = client.post(
        "/alert", json=payload, headers={"Authorization": "Bearer wrong"}
    )
    assert resp.status_code == 401


# --------------------------------------------------------------------------- #
# Successful delivery                                                          #
# --------------------------------------------------------------------------- #
def test_alert_sends_sms_and_returns_counts(client):
    payload = make_payload([make_alert(severity="critical")])
    resp = client.post("/alert", json=payload, headers=AUTH)
    assert resp.status_code == 200
    body = resp.json()
    assert body["sent_sms"] == 1
    # critical -> voice escalation enabled in conftest
    assert body["sent_calls"] == 1
    client.send_sms.assert_awaited_once()
    client.send_tts.assert_awaited_once()

    # check the arguments passed to SMSEagle
    args, kwargs = client.send_sms.await_args
    recipients, text = args
    assert recipients == ["+48600100200", "oncall-group"]
    assert text.startswith("[FIRING]")


def test_warning_alert_does_not_call(client):
    payload = make_payload(
        [make_alert(severity="warning", instance="web02:9100")]
    )
    resp = client.post("/alert", json=payload, headers=AUTH)
    assert resp.status_code == 200
    assert resp.json() == {"sent_sms": 1, "sent_calls": 0, "errors": []}
    client.send_tts.assert_not_awaited()


def test_no_recipients_yields_zero_sent(client, monkeypatch):
    # a payload with a severity that has no route and no override; default is
    # 'noc-group', so to get no recipients we override the settings to be empty.
    monkeypatch.setattr(app_module.settings, "default_recipients", [])
    monkeypatch.setattr(app_module.settings, "routes", {})
    payload = make_payload([make_alert(severity="info", extra_labels=None)])
    resp = client.post("/alert", json=payload, headers=AUTH)
    assert resp.status_code == 200
    assert resp.json()["sent_sms"] == 0
    client.send_sms.assert_not_awaited()


# --------------------------------------------------------------------------- #
# Invalid data / SMSEagle errors                                              #
# --------------------------------------------------------------------------- #
def test_invalid_json_returns_400(client):
    resp = client.post(
        "/alert",
        content=b"this is not json",
        headers={**AUTH, "Content-Type": "application/json"},
    )
    assert resp.status_code == 400


def test_smseagle_failure_returns_502(client, monkeypatch):
    # send_sms raises an HTTP error -> since nothing went through, expect 502
    request = httpx.Request("POST", "https://dev/api/v2/messages/sms")
    response = httpx.Response(500, text="modem error", request=request)
    failing = AsyncMock(
        side_effect=httpx.HTTPStatusError("500", request=request, response=response)
    )
    monkeypatch.setattr(app_module.client, "send_sms", failing)

    payload = make_payload([make_alert(severity="warning")])
    resp = client.post("/alert", json=payload, headers=AUTH)
    assert resp.status_code == 502


def test_partial_failure_still_200(client, monkeypatch):
    # first SMS OK, second fails -> sent_sms>=1, so 200 with errors
    request = httpx.Request("POST", "https://dev/api/v2/messages/sms")
    response = httpx.Response(500, text="err", request=request)
    calls = {"n": 0}

    async def flaky(recipients, text):
        calls["n"] += 1
        if calls["n"] == 2:
            raise httpx.HTTPStatusError("500", request=request, response=response)
        return {"status": "queued"}

    monkeypatch.setattr(app_module.client, "send_sms", flaky)
    payload = make_payload(
        [
            make_alert(alertname="A", severity="warning"),
            make_alert(alertname="B", severity="warning"),
        ]
    )
    resp = client.post("/alert", json=payload, headers=AUTH)
    assert resp.status_code == 200
    body = resp.json()
    assert body["sent_sms"] == 1
    assert body["errors"]
