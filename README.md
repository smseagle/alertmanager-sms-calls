# SMSEagle Alertmanager Adapter 

A lightweight webhook receiver that allows sending SMS and Calls from [Prometheus Alertmanager](https://github.com/prometheus/alertmanager). It maps the Prometheus Alertmanager payload (schema v4) to **SMSEagle API v2** calls. 

```
Prometheus -> Alertmanager -> (webhook) -> adapter -> SMSEagle API v2 -> SMS / TTS Calls
```

## Why an adapter instead of a direct webhook
The Alertmanager payload has a fixed format that does not map 1:1 to the SMSEagle
API v2 schema. Alertmanager has no native SMS channel - the canonical pattern is
`webhook_config` -> a thin proxy. This adapter fills that role.


## Why SMSEagle
[SMSEagle](https://www.smseagle.eu/) is an offline hardware SMS gateway. Therefore, no external connection to 3rd party system is required. All notifications are generated on-premise and sent directly to a cellular network. This solution can be used in secure (offline) installations without Internet access. SMSEagle runs on-premises, so alerts aren’t routed through third-party cloud SMS/voice providers.

## Features
Full routing and formatting: per-severity recipients, firing/resolved messages, a single summary SMS during alert storms, and optional voice escalation for critical alerts. Best for any Prometheus/Alertmanager stack, including Kubernetes.

## Quick start

### 1. Generate the API v2 token in the SMSEagle Web-GUI: Users -> new user (User level)
-> Access to API -> APIv2 -> Generate new token, with the `Send SMS` permission
(and `Send TTS` if you use voice escalation).

### 2. Fill in docker-compose.yml (SMSEAGLE_URL, SMSEAGLE_TOKEN, tokens, routing)

### Key environment variables (configured in docker-compose.yml)

| Variable                     | Description                                                 |
|------------------------------|-------------------------------------------------------------|
| `SMSEAGLE_URL`               | device address, e.g. `https://192.168.1.101`                |
| `SMSEAGLE_TOKEN`             | API v2 token                                                |
| `SMSEAGLE_VERIFY_TLS`        | `true`/`false` (self-signed cert on on-prem devices)        |
| `ADAPTER_WEBHOOK_TOKEN`      | bearer protecting the adapter's inbound endpoint (required)  |
| `ALLOW_UNAUTHENTICATED_WEBHOOK` | `true` to explicitly opt out of `ADAPTER_WEBHOOK_TOKEN` (isolated networks only) |
| `SMSEAGLE_DEFAULT_RECIPIENTS`| default recipient (number/group, comma-separated list)      |
| `SMSEAGLE_ROUTES`            | `critical=+48...,oncall-group;warning=noc-group`            |
| `MESSAGE_MODE`               | `one_per_alert` or `summary`                                |
| `MAX_INDIVIDUAL_ALERTS`      | threshold for switching to a single summary SMS             |
| `ESCALATE_CALL_SEVERITIES`   | severities that additionally trigger a TTS call             |


### 3. Run

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
  ADAPTER_WEBHOOK_TOKEN=$(openssl rand -hex 32) \
  uvicorn smseagle_adapter.app:app --app-dir src --host 0.0.0.0 --port 8080
```

`ADAPTER_WEBHOOK_TOKEN` is required - the adapter refuses to start without it
(or with the `docker-compose.yml` placeholder value), since `/alert` would
otherwise accept unauthenticated requests from anyone who can reach the port.
If you are intentionally binding to `127.0.0.1` only, or the port is only
reachable from a fully trusted, isolated network, you can opt out explicitly
with `ALLOW_UNAUTHENTICATED_WEBHOOK=true` instead.

## Recipient routing
A recipient is a phone number (+48…) or a Phonebook group name. You can route in two ways:

By severity — map the alert severity label to recipients in the adapter (SMSEAGLE_ROUTES).
Per alert — add a smseagle_to label in the Prometheus rule to override the recipient; it takes priority over severity routing.

## Configuring Alertmanager

The adapter is a plain `webhook_config` receiver, so pointing Alertmanager at
it only takes a `receivers` entry and (optionally) a `route` to select which
alerts go there. [`examples/alertmanager-example.yml`](examples/alertmanager-example.yml)
is a ready-to-adapt fragment - merge it into your existing `alertmanager.yml`:

1. Copy the `receivers` entry and, if needed, the `route`/`routes` block from
   the example into your `alertmanager.yml`.
2. Set `url` to wherever the adapter is reachable from Alertmanager, e.g.
   `http://smseagle-adapter:8080/alert` when both run in the same Docker
   Compose network, or a full `https://` URL if it's reachable over the
   network.
3. Replace `http_config.authorization.credentials` with the same value as
   the adapter's `ADAPTER_WEBHOOK_TOKEN` - Alertmanager sends it as
   `Authorization: Bearer <token>`, which `/alert` requires.
4. Keep `send_resolved: true` if you also want `[RESOLVED]` notifications,
   and `max_alerts: 0` so Alertmanager doesn't truncate large alert groups
   before they reach the adapter (the adapter's own `MESSAGE_MODE=summary` /
   `MAX_INDIVIDUAL_ALERTS` handle alert storms instead).
5. Use `matchers`/`routes` to send only specific alerts to the `smseagle`
   receiver (e.g. by `severity`), or route by severity entirely inside the
   adapter via `SMSEAGLE_ROUTES` instead - both are shown in the example.
6. Validate the config and reload Alertmanager:
   ```bash
   amtool check-config alertmanager.yml
   # then reload/restart Alertmanager, or send SIGHUP / POST /-/reload
   ```
7. Confirm delivery: check the adapter logs for `SMS -> ...` / `TTS call -> ...`
   lines, and Alertmanager's `Status` page for the receiver's last notify
   attempt.

See [Recipient routing](#recipient-routing) above for how `SMSEAGLE_ROUTES`
and the `smseagle_to` label interact.

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

MIT - see [LICENSE](LICENSE)

