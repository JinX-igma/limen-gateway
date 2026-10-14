# Security model

Do not report real API keys, device tokens, or TLS private keys in a
public issue.

## Threat model

- **Devices are untrusted clients.** A device holds only its own
  per-device bearer token, never the upstream OpenAI API key or the
  operator's ChatGPT login. A compromised or physically-extracted
  device can be revoked (`DELETE /admin/devices/{id}`) without touching
  the real credential or any other device's token.
- **The admin API is operator-only.** It is a separate TLS listener
  from the device API, bound to loopback by default. Widening
  `EDGE_ADMIN_BIND` beyond loopback requires both TLS and
  `EDGE_ADMIN_TOKEN` to be set — enforced in `Config.validate()`, not
  just documented. `EDGE_ADMIN_CORS_ORIGIN` defaults to empty (deny all
  browser origins); set it only to the exact origin of a flasher page
  you trust to mint devices on your behalf.
- **Token comparisons are constant-time** (`hmac.compare_digest`) for
  both device tokens and the admin token. A timing leak on the admin
  token is the worse of the two — it gates minting against your real
  OpenAI spend.
- **Tokens are hashed at rest** (SHA-256) in the devices table; the
  plaintext token is returned exactly once, at mint time, and is never
  logged — not even masked — by the gateway. (A device's own firmware
  may mask its own token for local serial debugging; that's a decision
  made in `limen-flasher`, not this repo.)
- **Rate limiting is per-device and in-memory**, resetting on restart.
  This bounds a single compromised device's blast radius on your
  upstream spend, but is not a substitute for prompt revocation.

## Known limitations

- The device and admin rate limiters/caches are single-process and
  in-memory; they do not survive a restart and do not coordinate across
  multiple gateway processes. Fine for the fleet sizes this project
  targets; not a multi-region/HA design.
- The `openai_api` backend keeps conversation history in process memory
  per thread id — a restart loses in-flight conversations (the device
  will simply start a new thread on its next turn).
- There is no built-in per-device spend quota beyond the request-rate
  limiter. If you need a hard dollar cap, enforce it on the OpenAI
  project/key itself (a dedicated, spend-limited project key is
  recommended regardless).

## Reporting a vulnerability

Open a private security advisory on this repository, or contact the
maintainer directly — do not open a public issue with exploit details.
