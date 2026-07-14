"""
Adapter configuration - everything via environment variables.

Recipient routing by severity:
    Maps the `severity` label (from a Prometheus alert rule) to a recipient
    in SMSEagle. A recipient is a number (+48...) OR a Phonebook group name.
    Format of the SMSEAGLE_ROUTES variable:
        severity1=recipient1;severity2=recipientA,recipientB
    e.g.:
        critical=+48600100200,oncall-group;warning=noc-group
"""

from __future__ import annotations

import os

# Placeholder secrets shipped in docker-compose.yml / examples. If one of these
# is still in place at startup, the operator forgot to replace it - treat it as
# "no secret configured" rather than a valid token.
_PLACEHOLDER_WEBHOOK_TOKENS = {"REPLACE_WITH_LONG_RANDOM_SECRET"}


def _split_recipients(raw: str) -> list[str]:
    return [r.strip() for r in raw.split(",") if r.strip()]


def _parse_routes(raw: str) -> dict[str, list[str]]:
    routes: dict[str, list[str]] = {}
    for pair in raw.split(";"):
        pair = pair.strip()
        if not pair or "=" not in pair:
            continue
        sev, recips = pair.split("=", 1)
        routes[sev.strip().lower()] = _split_recipients(recips)
    return routes


def _env_bool(name: str, default: bool) -> bool:
    val = os.getenv(name)
    if val is None:
        return default
    return val.strip().lower() in {"1", "true", "yes", "on"}


class Settings:
    def __init__(self) -> None:
        # --- Connection to the SMSEagle device ---
        self.smseagle_url: str = os.environ["SMSEAGLE_URL"].rstrip("/")
        self.smseagle_token: str = os.environ["SMSEAGLE_TOKEN"]
        self.smseagle_verify_tls: bool = _env_bool("SMSEAGLE_VERIFY_TLS", True)
        self.request_timeout: float = float(os.getenv("REQUEST_TIMEOUT", "10"))

        # --- Inbound security (webhook from Alertmanager) ---
        self.webhook_token: str = os.getenv("ADAPTER_WEBHOOK_TOKEN", "")
        allow_unauthenticated = _env_bool("ALLOW_UNAUTHENTICATED_WEBHOOK", False)

        if self.webhook_token in _PLACEHOLDER_WEBHOOK_TOKENS:
            raise RuntimeError(
                "ADAPTER_WEBHOOK_TOKEN is still set to the placeholder value "
                "from docker-compose.yml. Generate a real secret (e.g. "
                "`openssl rand -hex 32`) and set it before starting the adapter."
            )
        if not self.webhook_token and not allow_unauthenticated:
            raise RuntimeError(
                "ADAPTER_WEBHOOK_TOKEN is not set, so /alert would accept "
                "unauthenticated requests from anyone who can reach it. Set "
                "ADAPTER_WEBHOOK_TOKEN to a long random secret, or set "
                "ALLOW_UNAUTHENTICATED_WEBHOOK=true if you have verified the "
                "adapter is reachable only from a fully trusted, isolated "
                "network."
            )

        # --- Recipient routing ---
        # Default recipient when severity matches no route.
        self.default_recipients: list[str] = _split_recipients(
            os.getenv("SMSEAGLE_DEFAULT_RECIPIENTS", "")
        )
        # severity -> [recipients]
        self.routes: dict[str, list[str]] = _parse_routes(
            os.getenv("SMSEAGLE_ROUTES", "")
        )
        # Alert label that can override the recipient directly
        # (flexible routing on the Alertmanager side).
        self.recipient_label: str = os.getenv("SMSEAGLE_RECIPIENT_LABEL", "smseagle_to")

        # --- Message formatting ---
        # one_per_alert: one SMS per alert. summary: a single combined SMS.
        self.message_mode: str = os.getenv("MESSAGE_MODE", "one_per_alert").lower()
        # Max number of alerts sent individually before switching to a summary.
        self.max_individual_alerts: int = int(os.getenv("MAX_INDIVIDUAL_ALERTS", "5"))
        # Hard limit on SMS text length (multipart is handled by the device).
        self.max_sms_length: int = int(os.getenv("MAX_SMS_LENGTH", "320"))
        # Whether to append generatorURL / externalURL to the body.
        self.include_url: bool = _env_bool("INCLUDE_URL", False)

        # --- Voice escalation (TTS) ---
        # For which severities to additionally place a TTS call (besides SMS).
        self.escalate_call_severities: set[str] = {
            s.strip().lower()
            for s in os.getenv("ESCALATE_CALL_SEVERITIES", "").split(",")
            if s.strip()
        }
        self.tts_call_duration: int = int(os.getenv("TTS_CALL_DURATION", "20"))

    def recipients_for(self, severity: str) -> list[str]:
        """Returns the recipient list for the given severity (with default fallback)."""
        return self.routes.get(severity.lower(), self.default_recipients)
