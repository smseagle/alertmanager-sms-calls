"""
Shared test configuration.

IMPORTANT: the environment variables required by config.Settings must be set
BEFORE any test module imports `app`/`config`. conftest.py is loaded by pytest
before the test modules, so we set them here at module level.
"""

from __future__ import annotations

import os
from typing import Any

import pytest

# --- Baseline env (used e.g. when importing app.py) ---
os.environ.setdefault("SMSEAGLE_URL", "https://192.168.1.101")
os.environ.setdefault("SMSEAGLE_TOKEN", "test-token")
os.environ.setdefault("ADAPTER_WEBHOOK_TOKEN", "secret123")
os.environ.setdefault("SMSEAGLE_DEFAULT_RECIPIENTS", "noc-group")
os.environ.setdefault(
    "SMSEAGLE_ROUTES", "critical=+48600100200,oncall-group;warning=noc-group"
)
os.environ.setdefault("ESCALATE_CALL_SEVERITIES", "critical")
os.environ.setdefault("MAX_INDIVIDUAL_ALERTS", "5")


# --------------------------------------------------------------------------- #
# Helpers building Settings with env overrides (auto-reverted via monkeypatch) #
# --------------------------------------------------------------------------- #
_BASE_ENV = {
    "SMSEAGLE_URL": "https://dev",
    "SMSEAGLE_TOKEN": "t",
    "SMSEAGLE_DEFAULT_RECIPIENTS": "noc-group",
    "SMSEAGLE_ROUTES": "critical=+48600100200,oncall-group;warning=noc-group",
    "ESCALATE_CALL_SEVERITIES": "critical",
    "MAX_INDIVIDUAL_ALERTS": "5",
}

# Optional variables we clear when a test does not provide them, so values
# from the baseline / other tests do not leak in.
_OPTIONAL = [
    "MESSAGE_MODE",
    "INCLUDE_URL",
    "MAX_SMS_LENGTH",
    "SMSEAGLE_RECIPIENT_LABEL",
    "ADAPTER_WEBHOOK_TOKEN",
    "SMSEAGLE_VERIFY_TLS",
    "TTS_CALL_DURATION",
]


@pytest.fixture()
def make_settings(monkeypatch):
    """Settings factory: make_settings(MESSAGE_MODE='summary', ...) -> Settings."""

    def _factory(**overrides: str):
        env = dict(_BASE_ENV)
        env.update(overrides)
        for key, val in env.items():
            monkeypatch.setenv(key, val)
        for opt in _OPTIONAL:
            if opt not in env:
                monkeypatch.delenv(opt, raising=False)
        from smseagle_adapter.config import Settings

        return Settings()

    return _factory


# --------------------------------------------------------------------------- #
# Helpers building Alertmanager payloads (schema v4)                           #
# --------------------------------------------------------------------------- #
def make_alert(
    *,
    status: str = "firing",
    alertname: str = "InstanceDown",
    severity: str = "critical",
    instance: str = "db01:9100",
    summary: str | None = "DB01 not responding",
    description: str | None = None,
    generator_url: str | None = "https://prom/graph?g0",
    extra_labels: dict[str, str] | None = None,
) -> dict[str, Any]:
    labels: dict[str, str] = {"alertname": alertname}
    if severity:
        labels["severity"] = severity
    if instance:
        labels["instance"] = instance
    if extra_labels:
        labels.update(extra_labels)

    annotations: dict[str, str] = {}
    if summary is not None:
        annotations["summary"] = summary
    if description is not None:
        annotations["description"] = description

    alert: dict[str, Any] = {
        "status": status,
        "labels": labels,
        "annotations": annotations,
    }
    if generator_url:
        alert["generatorURL"] = generator_url
    return alert


def make_payload(
    alerts: list[dict[str, Any]],
    *,
    group_labels: dict[str, Any] | None = None,
    common_labels: dict[str, Any] | None = None,
    status: str = "firing",
) -> dict[str, Any]:
    return {
        "version": "4",
        "status": status,
        "groupLabels": group_labels or {"alertname": "InstanceDown"},
        "commonLabels": common_labels or {},
        "externalURL": "https://alertmanager.example/",
        "alerts": alerts,
    }
