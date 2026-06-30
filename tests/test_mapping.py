"""Tests for the adapter core: mapping the Alertmanager payload -> messages."""

from __future__ import annotations

from smseagle_adapter import mapping
from conftest import make_alert, make_payload


# --------------------------------------------------------------------------- #
# Formatting helpers                                                           #
# --------------------------------------------------------------------------- #
def test_status_prefix_known():
    assert mapping._status_prefix("firing") == "[FIRING]"
    assert mapping._status_prefix("resolved") == "[RESOLVED]"


def test_status_prefix_unknown_falls_back_to_uppercase():
    assert mapping._status_prefix("paused") == "[PAUSED]"


def test_truncate_keeps_short_text():
    assert mapping._truncate("short", 100) == "short"


def test_truncate_long_text_adds_ellipsis():
    out = mapping._truncate("x" * 50, 10)
    assert len(out) == 10
    assert out.endswith("\u2026")


# --------------------------------------------------------------------------- #
# one_per_alert mode: routing, prefixes, body fallback                         #
# --------------------------------------------------------------------------- #
def test_one_per_alert_routes_by_severity(make_settings):
    s = make_settings()
    payload = make_payload(
        [
            make_alert(status="firing", alertname="InstanceDown", severity="critical"),
            make_alert(
                status="resolved",
                alertname="HighCPU",
                severity="warning",
                instance="web02:9100",
                summary=None,
                description="CPU back to normal",
            ),
        ]
    )
    msgs = mapping.build_messages(payload, s)
    assert len(msgs) == 2

    crit, warn = msgs
    assert crit.recipients == ["+48600100200", "oncall-group"]
    assert crit.text.startswith("[FIRING] | InstanceDown | critical")
    assert crit.escalate_call is True  # critical + firing

    assert warn.recipients == ["noc-group"]
    assert warn.text.startswith("[RESOLVED] | HighCPU | warning")
    # no summary -> fallback to description
    assert "CPU back to normal" in warn.text
    assert warn.escalate_call is False


def test_summary_fallback_chain_to_alertname(make_settings):
    s = make_settings()
    payload = make_payload(
        [make_alert(alertname="OnlyName", summary=None, description=None)]
    )
    msg = mapping.build_messages(payload, s)[0]
    # no summary and no description -> body based on alertname, nothing appended
    assert "OnlyName" in msg.text


def test_escalate_only_on_firing_not_resolved(make_settings):
    s = make_settings()
    payload = make_payload(
        [make_alert(status="resolved", severity="critical")]
    )
    msg = mapping.build_messages(payload, s)[0]
    # critical, but resolved -> do not place a call
    assert msg.escalate_call is False


def test_recipient_override_label_takes_priority(make_settings):
    s = make_settings()
    payload = make_payload(
        [
            make_alert(
                severity="info",  # would normally go to default
                extra_labels={"smseagle_to": "+48700800900, vip-group"},
            )
        ]
    )
    msg = mapping.build_messages(payload, s)[0]
    assert msg.recipients == ["+48700800900", "vip-group"]


def test_alert_with_no_recipient_is_skipped(make_settings):
    # no default and no route for 'info' -> alert is skipped
    s = make_settings(SMSEAGLE_DEFAULT_RECIPIENTS="", SMSEAGLE_ROUTES="critical=+48100")
    payload = make_payload([make_alert(severity="info")])
    assert mapping.build_messages(payload, s) == []


def test_empty_alerts_returns_empty(make_settings):
    s = make_settings()
    assert mapping.build_messages(make_payload([]), s) == []


def test_include_url_toggle(make_settings):
    s_on = make_settings(INCLUDE_URL="true")
    s_off = make_settings(INCLUDE_URL="false")
    payload = make_payload([make_alert(generator_url="https://prom/g?x")])
    assert "https://prom/g?x" in mapping.build_messages(payload, s_on)[0].text
    assert "https://prom/g?x" not in mapping.build_messages(payload, s_off)[0].text


def test_text_is_truncated_to_max_length(make_settings):
    s = make_settings(MAX_SMS_LENGTH="40")
    payload = make_payload([make_alert(summary="x" * 200)])
    msg = mapping.build_messages(payload, s)[0]
    assert len(msg.text) <= 40


# --------------------------------------------------------------------------- #
# Summary mode                                                                 #
# --------------------------------------------------------------------------- #
def test_summary_mode_triggers_over_threshold(make_settings):
    s = make_settings(MAX_INDIVIDUAL_ALERTS="3")
    alerts = [make_alert(alertname=f"Alert{i}", severity="critical") for i in range(5)]
    payload = make_payload(alerts, common_labels={"severity": "critical"})
    msgs = mapping.build_messages(payload, s)
    assert len(msgs) == 1
    summary = msgs[0]
    assert summary.text.startswith("[ALERTS]")
    assert "5 firing" in summary.text
    assert summary.recipients == ["+48600100200", "oncall-group"]
    assert summary.escalate_call is True


def test_summary_mode_forced_by_message_mode(make_settings):
    s = make_settings(MESSAGE_MODE="summary")
    payload = make_payload(
        [make_alert(severity="warning")], common_labels={"severity": "warning"}
    )
    msgs = mapping.build_messages(payload, s)
    assert len(msgs) == 1
    assert msgs[0].text.startswith("[ALERTS]")
    assert msgs[0].recipients == ["noc-group"]


def test_summary_counts_firing_and_resolved(make_settings):
    s = make_settings(MESSAGE_MODE="summary")
    alerts = [
        make_alert(status="firing", alertname="A", severity="critical"),
        make_alert(status="resolved", alertname="B", severity="critical"),
        make_alert(status="resolved", alertname="C", severity="critical"),
    ]
    payload = make_payload(alerts, common_labels={"severity": "critical"})
    text = mapping.build_messages(payload, s)[0].text
    assert "1 firing" in text
    assert "2 resolved" in text
