"""
SMSEagle Alertmanager Adapter
=============================

A lightweight webhook receiver that maps the Prometheus Alertmanager payload
(webhook_config, schema v4) to REST API v2 calls of an SMSEagle device.

Flow:
    Prometheus  ->  Alertmanager  ->  (webhook)  ->  THIS ADAPTER  ->  SMSEagle API v2  ->  SMS / TTS call

SMSEagle API v2 (confirmed):
    POST https://<smseagle-ip>/api/v2/messages/sms
    headers:  access-token: <token>,  Content-Type: application/json
    body:     {"to": ["+48123456789"], "text": "..."}
    The "to" field may be a phone number OR a Phonebook group name.

The adapter is stateless. All configuration is via environment variables (see config.py).
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from typing import Any

import httpx
from fastapi import FastAPI, Header, HTTPException, Request, status

from .config import Settings
from .mapping import build_messages, AdapterMessage

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
log = logging.getLogger("smseagle-adapter")

settings = Settings()


@asynccontextmanager
async def lifespan(_: FastAPI):
    # startup: nothing beyond the already-created client
    yield
    # shutdown: close HTTP connections to SMSEagle
    await client.aclose()


app = FastAPI(
    title="SMSEagle Alertmanager Adapter",
    version="0.1.0",
    lifespan=lifespan,
)


# --------------------------------------------------------------------------- #
# SMSEagle API v2 client                                                       #
# --------------------------------------------------------------------------- #
class SMSEagleClient:
    """Thin client over the SMSEagle API v2 (SMS + optional TTS call)."""

    def __init__(self, base_url: str, token: str, verify_tls: bool, timeout: float):
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout
        # access-token is passed as a header, per API v2
        self._client = httpx.AsyncClient(
            headers={
                "access-token": token,
                "Content-Type": "application/json",
            },
            verify=verify_tls,  # on-prem devices often have a self-signed cert
            timeout=timeout,
        )

    async def send_sms(self, recipients: list[str], text: str) -> dict[str, Any]:
        url = f"{self._base_url}/api/v2/messages/sms"
        payload = {"to": recipients, "text": text}
        resp = await self._client.post(url, json=payload)
        resp.raise_for_status()
        return _safe_json(resp)

    async def send_tts_call(
        self, recipients: list[str], text: str, duration: int
    ) -> dict[str, Any]:
        """Text-to-speech voice call - escalation for critical alerts.

        Requires a device with a voice modem. Endpoint: /api/v2/calls/tts
        """
        url = f"{self._base_url}/api/v2/calls/tts"
        payload = {"to": recipients, "text": text, "duration": duration}
        resp = await self._client.post(url, json=payload)
        resp.raise_for_status()
        return _safe_json(resp)

    async def aclose(self) -> None:
        await self._client.aclose()


def _safe_json(resp: httpx.Response) -> dict[str, Any]:
    try:
        return resp.json()
    except ValueError:
        return {"raw": resp.text}


client = SMSEagleClient(
    base_url=settings.smseagle_url,
    token=settings.smseagle_token,
    verify_tls=settings.smseagle_verify_tls,
    timeout=settings.request_timeout,
)


# --------------------------------------------------------------------------- #
# Endpoints                                                                    #
# --------------------------------------------------------------------------- #
@app.get("/healthz")
async def healthz() -> dict[str, str]:
    """Liveness probe - does not touch SMSEagle, to avoid generating traffic."""
    return {"status": "ok"}


@app.post("/alert", status_code=status.HTTP_200_OK)
async def receive_alert(
    request: Request,
    authorization: str | None = Header(default=None),
) -> dict[str, Any]:
    """Main endpoint receiving the webhook from Alertmanager.

    Configure it in alertmanager.yml as webhook_config -> url.
    """
    _authenticate(authorization)

    try:
        payload = await request.json()
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid JSON: {exc}",
        ) from exc

    version = str(payload.get("version", ""))
    if version and version != "4":
        log.warning("Unexpected Alertmanager payload version: %s", version)

    messages: list[AdapterMessage] = build_messages(payload, settings)
    if not messages:
        log.info("Webhook received, but no message was generated.")
        return {"sent_sms": 0, "sent_calls": 0, "detail": "no recipients/messages"}

    sent_sms = 0
    sent_calls = 0
    errors: list[str] = []

    for msg in messages:
        # SMS
        try:
            result = await client.send_sms(msg.recipients, msg.text)
            sent_sms += 1
            log.info(
                "SMS -> %s (%d chars) status=%s",
                msg.recipients,
                len(msg.text),
                result,
            )
        except httpx.HTTPStatusError as exc:
            body = exc.response.text if exc.response is not None else ""
            errors.append(f"SMS HTTP {exc.response.status_code}: {body}")
            log.error("SMSEagle error (SMS): %s %s", exc, body)
        except httpx.HTTPError as exc:
            errors.append(f"SMS transport: {exc}")
            log.error("Transport error (SMS): %s", exc)

        # Voice escalation for critical alerts (optional)
        if msg.escalate_call:
            try:
                await client.send_tts_call(
                    msg.recipients, msg.text, settings.tts_call_duration
                )
                sent_calls += 1
                log.info("TTS call -> %s", msg.recipients)
            except httpx.HTTPError as exc:
                errors.append(f"TTS call: {exc}")
                log.error("SMSEagle error (TTS call): %s", exc)

    if errors and sent_sms == 0:
        # nothing went through -> return 502 so Alertmanager retries
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail={"errors": errors},
        )

    return {"sent_sms": sent_sms, "sent_calls": sent_calls, "errors": errors}


def _authenticate(authorization: str | None) -> None:
    """Simple inbound bearer so the webhook is not open to the world.

    In alertmanager.yml: http_config.authorization.credentials = <ADAPTER_WEBHOOK_TOKEN>
    If ADAPTER_WEBHOOK_TOKEN is not set, authentication is disabled
    (use only in an isolated network).
    """
    if not settings.webhook_token:
        return
    expected = f"Bearer {settings.webhook_token}"
    if authorization != expected:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing or invalid webhook token.",
        )
