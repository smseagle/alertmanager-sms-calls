"""
Adapter core: maps the Alertmanager payload (schema v4) to SMSEagle messages.

Alertmanager payload structure (webhook_config, version 4) - abbreviated:
{
  "version": "4",
  "status": "firing" | "resolved",
  "groupLabels":      {...},
  "commonLabels":     {...},
  "commonAnnotations":{...},
  "externalURL": "https://alertmanager...",
  "alerts": [
    {
      "status": "firing" | "resolved",
      "labels":      {"alertname": "...", "severity": "...", "instance": "...", ...},
      "annotations": {"summary": "...", "description": "..."},
      "startsAt": "RFC3339", "endsAt": "RFC3339",
      "generatorURL": "...", "fingerprint": "..."
    }, ...
  ]
}

Field mapping -> SMS:
  status        -> [FIRING] / [RESOLVED] prefix
  severity      -> recipient routing + optional voice escalation
  alertname     -> body header
  instance/job  -> context (where the alert comes from)
  summary       -> main body (fallback: description, then alertname)
  generatorURL  -> optional link (INCLUDE_URL=true)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .config import Settings


@dataclass
class AdapterMessage:
    recipients: list[str]
    text: str
    escalate_call: bool = False
    labels: dict[str, str] = field(default_factory=dict)


# --------------------------------------------------------------------------- #
def _status_prefix(status: str) -> str:
    return {"firing": "[FIRING]", "resolved": "[RESOLVED]"}.get(
        status.lower(), f"[{status.upper()}]"
    )


def _alert_severity(alert: dict[str, Any]) -> str:
    return str(alert.get("labels", {}).get("severity", "")).lower()


def _format_alert_text(alert: dict[str, Any], settings: Settings) -> str:
    """Builds concise text for a single alert, SMS-length aware."""
    labels = alert.get("labels", {})
    annotations = alert.get("annotations", {})

    status = _status_prefix(str(alert.get("status", "")))
    alertname = labels.get("alertname", "alert")
    severity = labels.get("severity", "")
    instance = labels.get("instance") or labels.get("job") or ""
    summary = (
        annotations.get("summary")
        or annotations.get("description")
        or alertname
    )

    parts = [status, alertname]
    if severity:
        parts.append(severity)
    if instance:
        parts.append(instance)
    head = " | ".join(parts)

    text = f"{head} | {summary}" if summary and summary != alertname else head

    if settings.include_url:
        url = alert.get("generatorURL") or ""
        if url:
            text = f"{text} | {url}"

    return _truncate(text, settings.max_sms_length)


def _format_summary_text(
    alerts: list[dict[str, Any]], group_labels: dict[str, Any], settings: Settings
) -> str:
    """A single combined SMS for the whole alert group (when there are many)."""
    firing = sum(1 for a in alerts if str(a.get("status")).lower() == "firing")
    resolved = sum(1 for a in alerts if str(a.get("status")).lower() == "resolved")
    group = group_labels.get("alertname") or group_labels.get("job") or "group"

    names = []
    for a in alerts[:5]:
        names.append(a.get("labels", {}).get("alertname", "alert"))
    sample = ", ".join(dict.fromkeys(names))  # unique, order preserved

    text = (
        f"[ALERTS] {group}: {firing} firing, {resolved} resolved "
        f"({len(alerts)} total). {sample}"
    )
    return _truncate(text, settings.max_sms_length)


def _truncate(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "\u2026"  # ...


def _recipients_for_alert(alert: dict[str, Any], settings: Settings) -> list[str]:
    """Resolves recipients: override label first, then routing by severity."""
    labels = alert.get("labels", {})
    override = labels.get(settings.recipient_label)
    if override:
        return [r.strip() for r in str(override).split(",") if r.strip()]
    return settings.recipients_for(_alert_severity(alert))


# --------------------------------------------------------------------------- #
def build_messages(payload: dict[str, Any], settings: Settings) -> list[AdapterMessage]:
    """Main function: Alertmanager payload -> list of messages to send."""
    alerts: list[dict[str, Any]] = payload.get("alerts", []) or []
    if not alerts:
        return []

    group_labels: dict[str, Any] = payload.get("groupLabels", {}) or {}

    # Summary mode: a single SMS when there are more alerts than the threshold,
    # or when MESSAGE_MODE=summary.
    use_summary = settings.message_mode == "summary" or (
        len(alerts) > settings.max_individual_alerts
    )

    if use_summary:
        # All go to default (routing by commonLabels could be added here).
        common_sev = str(
            payload.get("commonLabels", {}).get("severity", "")
        ).lower()
        recipients = settings.recipients_for(common_sev) or settings.default_recipients
        if not recipients:
            return []
        text = _format_summary_text(alerts, group_labels, settings)
        escalate = common_sev in settings.escalate_call_severities
        return [
            AdapterMessage(
                recipients=recipients,
                text=text,
                escalate_call=escalate,
                labels=dict(payload.get("commonLabels", {})),
            )
        ]

    # Individual mode: one SMS per alert, with per-alert routing.
    messages: list[AdapterMessage] = []
    for alert in alerts:
        recipients = _recipients_for_alert(alert, settings)
        if not recipients:
            continue  # no configured recipient -> skip
        severity = _alert_severity(alert)
        messages.append(
            AdapterMessage(
                recipients=recipients,
                text=_format_alert_text(alert, settings),
                escalate_call=(
                    str(alert.get("status")).lower() == "firing"
                    and severity in settings.escalate_call_severities
                ),
                labels=dict(alert.get("labels", {})),
            )
        )
    return messages
