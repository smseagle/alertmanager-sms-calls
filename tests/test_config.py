"""Tests for the config module: parsing routes, recipients, boolean flags."""

from __future__ import annotations

from smseagle_adapter import config


def test_split_recipients_trims_and_drops_empty():
    assert config._split_recipients(" +48100, oncall-group ,, ") == [
        "+48100",
        "oncall-group",
    ]


def test_parse_routes_basic():
    routes = config._parse_routes(
        "critical=+48600100200,oncall-group;warning=noc-group"
    )
    assert routes == {
        "critical": ["+48600100200", "oncall-group"],
        "warning": ["noc-group"],
    }


def test_parse_routes_ignores_malformed_segments():
    routes = config._parse_routes("critical=+48100; garbage ;;warning=")
    assert routes["critical"] == ["+48100"]
    # warning has an empty recipient list (nothing on the right-hand side)
    assert routes["warning"] == []
    assert "garbage" not in routes


def test_parse_routes_is_case_insensitive_on_severity():
    routes = config._parse_routes("CRITICAL=+48100")
    assert "critical" in routes


def test_env_bool_truthy_values(monkeypatch):
    for val in ["1", "true", "TRUE", "yes", "On"]:
        monkeypatch.setenv("FLAG_X", val)
        assert config._env_bool("FLAG_X", default=False) is True


def test_env_bool_falsy_and_default(monkeypatch):
    monkeypatch.setenv("FLAG_X", "nope")
    assert config._env_bool("FLAG_X", default=True) is False
    monkeypatch.delenv("FLAG_X", raising=False)
    assert config._env_bool("FLAG_X", default=True) is True


def test_recipients_for_known_and_fallback(make_settings):
    s = make_settings()
    assert s.recipients_for("critical") == ["+48600100200", "oncall-group"]
    assert s.recipients_for("warning") == ["noc-group"]
    # unknown severity -> default
    assert s.recipients_for("info") == ["noc-group"]


def test_recipients_for_is_case_insensitive(make_settings):
    s = make_settings()
    assert s.recipients_for("CRITICAL") == ["+48600100200", "oncall-group"]


def test_escalate_call_severities_parsed(make_settings):
    s = make_settings(ESCALATE_CALL_SEVERITIES="critical, page ")
    assert s.escalate_call_severities == {"critical", "page"}


def test_verify_tls_default_true(make_settings):
    s = make_settings()
    assert s.smseagle_verify_tls is True


def test_message_mode_default(make_settings):
    s = make_settings()
    assert s.message_mode == "one_per_alert"
