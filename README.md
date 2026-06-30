# SMSEagle Alertmanager Adapter 

A lightweight webhook receiver that allows sending SMS and Calls from Prometheus Alertmanager. It maps the Prometheus Alertmanager payload (schema v4) to **SMSEagle API v2** calls. 

```
Prometheus -> Alertmanager -> (webhook) -> adapter -> SMSEagle API v2 -> SMS / TTS Calls
```

## Why an adapter instead of a direct webhook
The Alertmanager payload has a fixed format that does not map 1:1 to the SMSEagle
API v2 schema. Alertmanager has no native SMS channel - the canonical pattern is
`webhook_config` -> a thin proxy. This adapter fills that role.


## Why SMSEagle
SMSEagle is an offline hardware SMS gateway. Therefore, no external connection to 3rd party system is required. All notifications are generated on-premise and sent directly to a cellular network. This solution can be used in secure (offline) installations without Internet access. SMSEagle runs on-premises, so alerts aren’t routed through third-party cloud SMS/voice providers.

## Features
Full routing and formatting: per-severity recipients, firing/resolved messages, a single summary SMS during alert storms, and optional voice escalation for critical alerts. Best for any Prometheus/Alertmanager stack, including Kubernetes.

## Running

# 1. Generate the API v2 token in the SMSEagle Web-GUI: Users -> new user (User level)
-> Access to API -> APIv2 -> Generate new token, with the `Send SMS` permission
(and `Send TTS` if you use voice escalation).

# 2. Fill in docker-compose.yml (SMSEAGLE_URL, SMSEAGLE_TOKEN, tokens, routing)

## Key environment variables (configured in docker-compose.yml)

| Variable                     | Description                                                 |
|------------------------------|-------------------------------------------------------------|
| `SMSEAGLE_URL`               | device address, e.g. `https://192.168.1.101`                |
| `SMSEAGLE_TOKEN`             | API v2 token                                                |
| `SMSEAGLE_VERIFY_TLS`        | `true`/`false` (self-signed cert on on-prem devices)        |
| `ADAPTER_WEBHOOK_TOKEN`      | bearer protecting the adapter's inbound endpoint            |
| `SMSEAGLE_DEFAULT_RECIPIENTS`| default recipient (number/group, comma-separated list)      |
| `SMSEAGLE_ROUTES`            | `critical=+48...,oncall-group;warning=noc-group`            |
| `MESSAGE_MODE`               | `one_per_alert` or `summary`                                |
| `MAX_INDIVIDUAL_ALERTS`      | threshold for switching to a single summary SMS             |
| `ESCALATE_CALL_SEVERITIES`   | severities that additionally trigger a TTS call             |


# 3. Run

```bash
docker compose up -d --build

#  In alertmanager.yml add a receiver pointing at the adapter
#    (see examples/alertmanager-example.yml)
#    url: http://smseagle-adapter:8080/alert
```

To run locally without Docker:

```bash
pip install -r requirements.txt
SMSEAGLE_URL=https://192.168.1.101 SMSEAGLE_TOKEN=... \
  uvicorn smseagle_adapter.app:app --app-dir src --host 0.0.0.0 --port 8080
```

## Recipient routing
A recipient is a phone number (+48…) or a Phonebook group name. You can route in two ways:

By severity — map the alert severity label to recipients in the adapter (SMSEAGLE_ROUTES).
Per alert — add a smseagle_to label in the Prometheus rule to override the recipient; it takes priority over severity routing.


## Endpoints
- `POST /alert` - webhook from Alertmanager
- `GET /healthz` - liveness probe


## Field mapping

| Alertmanager            | SMSEagle / effect                                   |
|-------------------------|-----------------------------------------------------|
| `status`                | `[FIRING]` / `[RESOLVED]` prefix                    |
| `labels.severity`       | recipient routing + optional voice escalation (TTS) |
| `labels.alertname`      | SMS body header                                     |
| `labels.instance`/`job` | context (where the alert comes from)                |
| `annotations.summary`   | main body (fallback: `description`, `alertname`)    |
| `generatorURL`          | optional link (`INCLUDE_URL=true`)                  |
| `labels.smseagle_to`    | recipient override (number/group), takes priority   |

Body sent to SMSEagle: `{"to": [...], "text": "..."}` with an `access-token` header.
A recipient can be a phone number (`+48...`) or a Phonebook group name.

## Project layout

```
.
├── README.md
├── LICENSE
├── .gitignore
├── Dockerfile
├── docker-compose.yml
├── pytest.ini
├── requirements.txt
├── requirements-dev.txt
├── src/
│   └── smseagle_adapter/        # application package
│       ├── __init__.py
│       ├── app.py               # FastAPI app, endpoints, SMSEagle client
│       ├── config.py            # env-based configuration + routing
│       └── mapping.py           # Alertmanager payload -> messages
├── tests/                       # pytest suite
│   ├── conftest.py
│   ├── test_app.py
│   ├── test_config.py
│   └── test_mapping.py
├── examples/
│   └── alertmanager-example.yml # sample alertmanager.yml fragment
└── .github/workflows/ci.yml     # tests + docker build
```


## Tests

```bash
pip install -r requirements-dev.txt
pytest
```

Coverage (35 tests):
- `test_config.py` - route parsing (`SMSEAGLE_ROUTES`), recipients, boolean flags, severity routing with fallback.
- `test_mapping.py` - firing/resolved prefixes, body fallback chain (summary -> description -> alertname), per-severity routing, `smseagle_to` override label, TTS escalation only for firing+critical, skipping alerts with no recipient, truncation, summary mode (threshold + forced), and firing/resolved counting.
- `test_app.py` - webhook authentication (401), successful delivery and arguments passed to SMSEagle, no recipients -> 0 sent, invalid JSON -> 400, total SMSEagle failure -> 502 (Alertmanager will retry), partial failure -> 200 with an error list. The SMSEagle client is mocked (no real HTTP calls).


## CI

GitHub Actions (`.github/workflows/ci.yml`) runs `pytest` on every push/PR to
`main` and then builds the Docker image (build only - add a registry login +
`push: true` and secrets when ready to publish).

## License

MIT - see [LICENSE](LICENSE). Copyright (c) 2026 PROXIMUS sp. z o.o.

> Status: conceptual draft for further development / possible release as an
> official SMSEagle integration component.
