# Architecture

## Two listeners, one process

```
                 ┌─────────────────────────────┐
  Device  ─────► │ Device API   :8443 (TLS)     │
 (bearer token)  │ /v1/responses, /healthz, ... │──┐
                 └─────────────────────────────┘  │
                                                    ▼
                 ┌─────────────────────────────┐  GatewayService
 Operator ─────► │ Admin API    :8444 (loopback)│  (per-conversation
(admin token)    │ /admin/devices               │   locking, semaphore)
                 └─────────────────────────────┘  │
                              │                     ▼
                              ▼                  Backend (pluggable)
                        DevicesStore          ┌───────────┬────────────┐
                        (SQLite, same         │ openai_api│   codex    │
                         file as the          │  (API key)│ (ChatGPT   │
                         conversation store)  │           │ subscr.)   │
                                               └───────────┴────────────┘
```

Both listeners run in the same process (`edge_gateway/main.py`), on
separate sockets, with separate credentials. This is deliberate: an
admin endpoint reachable from wherever devices sit is a design bug, not
a convenience, so it is never routed under the same port or TLS context
as the device API.

## Why one SQLite file for two stores

`ConversationStore` (`store.py`) and `DevicesStore` (`devices_store.py`)
open the same `EDGE_DATABASE` file, each in its own table
(`conversations`, `devices`), both in WAL mode. This avoids inventing a
second persistence/locking story next to one that was already proven at
this project's scale; at higher write concurrency than a handful of
devices you'd want to split them, but that's not this project's target
fleet size.

## Why devices are live, not a file loaded at startup

The previous design (one `devices.json` loaded once at process start,
written by a one-shot provisioning script that refused to rerun) meant
adding or revoking a device required editing a file and restarting the
service. `DeviceAuthenticator` now holds no cache at all — every
request calls `DevicesStore.verify_token()`, which reads straight from
SQLite — so a mint or revoke via the admin API or
`scripts/manage-devices.py` takes effect on the very next device
request, no restart.

## Why two backends behind one interface

`edge_gateway/backend.py` defines the minimal surface
`GatewayService` actually drives: `create_thread(instructions)` and
`run_turn(thread_id, text)`, plus `healthy()` and `account`. Two
implementations satisfy it:

- `backends/openai_api.py` — forwards to the real OpenAI Responses API
  with a server-held `OPENAI_API_KEY`. This is the generally useful
  default: anyone with an API key can stand up a gateway.
- `backends/codex_backend.py` — drives a local Codex CLI subprocess
  over stdio, authenticated via the operator's own ChatGPT subscription
  login, never an API key. Scoped to a single-operator, personal-fleet
  use case, but useful if you'd rather spend a flat subscription than
  metered API credits. It refuses to start unless the locally logged-in
  account reports `type == "chatgpt"` — do not relax this guard; it is
  the entire reason this backend is safe to run at all (an API-key
  login here would silently reintroduce the problem this project
  exists to avoid).

`GatewayService`, `ConversationStore`, `DeviceAuthenticator`, and the
device-facing HTTP layer are identical regardless of which backend is
selected (`EDGE_BACKEND=openai_api|codex`).

## What's intentionally out of scope for v1

- Only ESP32-family devices are supported on the flasher side (browser
  flashing protocols differ fundamentally between ESP32/esptool,
  RP2040/UF2, and STM32/DFU — see `limen-flasher`'s
  `docs/adding-a-chip-family.md` for the extension seam).
- No multi-gateway coordination, no HA/failover.
- No built-in spend dashboard — pair with your LLM provider's own
  usage/spend limits.
