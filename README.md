# Limen Gateway

A self-hosted gateway that lets a fleet of resource-constrained embedded
devices use an LLM without a single one of them ever holding a real
upstream API key. Devices hold a revocable, per-device bearer token;
the gateway holds the real credential (an OpenAI API key, or a local
ChatGPT subscription login) and never sends it anywhere a device can see.

Pairs with [`limen-flasher`](https://github.com/JinX-igma/limen-flasher) —
a browser-based ESP32 firmware flasher — for a close-to-one-click
"flash a device, mint it a token, start chatting" workflow. See that
repo's README for the device side; this one is the backend.

## Why this exists

Embedding a real LLM API key in client-side or on-device code is a
well-documented, expensive mistake: leaked embedded keys have turned
$400/month bills into $67,000 overnight, because a stolen key converts
directly to billable compute with no further exploitation needed. A
device you've shipped — physically, to users, outside your network — is
exactly the kind of client you cannot safely embed a real key in. Limen
Gateway is the backend half of "don't do that": devices authenticate
with a token you can revoke in one request; only the gateway process
ever holds the credential that costs you real money.

## What's here

- **Per-device bearer tokens**, SHA-256 hashed at rest, constant-time
  compared (`edge_gateway/auth.py`, `edge_gateway/devices_store.py`).
- **A live admin API** to mint and revoke device identities
  (`POST /admin/devices`, `DELETE /admin/devices/{id}`) — no server
  restart required, and it's on a separate, loopback-by-default listener
  from the device-facing API (`edge_gateway/admin_api.py`).
- **Two pluggable backends** behind one interface
  (`edge_gateway/backend.py`): `openai_api` forwards to the real OpenAI
  Responses API with a server-held key (the generally useful default),
  and `codex` drives a local, ChatGPT-subscription-authenticated Codex
  CLI over stdio — no API key anywhere in that path at all.
- **Per-device rate limiting** and **per-(device, conversation) session
  isolation**, backed by SQLite in WAL mode.
- **TLS on both listeners**, with the device listener requiring it
  whenever it's bound off loopback.

## Quick start

```bash
git clone https://github.com/JinX-igma/limen-gateway.git
cd limen-gateway
python3 -m unittest discover -s tests   # no network calls, no real keys needed

# One-time: generate TLS + an admin token for this host.
python3 scripts/provision-local.py --lan-ip 192.168.1.50

# Fill in OPENAI_API_KEY (or set EDGE_BACKEND=codex) in .secrets/gateway.env, then:
export $(cat .secrets/gateway.env | xargs)
python3 -m edge_gateway.main
```

Mint your first device (headless, no running server required):

```bash
python3 scripts/manage-devices.py mint --label "esp32-desk-01"
# Device id:    a1b2c3d4e5f6a7b8
# Device token: <copy this into the device's captive-portal setup page>
```

Or, once the gateway is running and you've allowlisted an origin via
`EDGE_ADMIN_CORS_ORIGIN`, the flasher's web UI can call
`POST /admin/devices` directly — see `docs/api.md`.

## Request contract

```http
POST /v1/responses HTTP/1.1
Host: 192.168.1.50:8443
Authorization: Bearer <device-token>
Content-Type: application/json

{"input":"report status","conversation":"wearable-main"}
```

Read `output_text` from the response; repeat the same `conversation` to
keep context. Full contract in [`docs/api.md`](docs/api.md).

## Documentation

- [Architecture](docs/architecture.md)
- [HTTP API contract](docs/api.md)
- [Security model](SECURITY.md)

## Development

Standard library only — no third-party dependencies, no build step.

```bash
python3 -m unittest discover -s tests -v
```

Tests never make a real network call or consume real API credits; the
`openai_api` backend's tests patch `urllib.request.urlopen`.

Runtime-generated paths (`.secrets/`, `data/`, `logs/`, `runtime/`) are
gitignored — never commit device tokens, TLS private keys, or the
Codex login state.
