# HTTP API contract

Two listeners, two credential types. Never use an admin token where a
device token is expected or vice versa — they are checked against
different stores entirely.

## Device API (default `:8443`, TLS required off-loopback)

All endpoints below except `/healthz` require `Authorization: Bearer <device-token>`.

### `GET /healthz`
No auth. `200 {"status":"ok"}` if the backend is healthy, else `503`.

### `GET /v1/account`
`200 {"auth_mode": "api_key"|"chatgpt", "plan_type": str|null}`

### `GET /v1/models`
`200 {"object":"list","data":[{"id": "<configured model>", "object":"model"}]}`

### `POST /v1/responses`
Request:
```json
{"input": "text, or omit and send messages instead", "conversation": "optional-id"}
```
`conversation` (or `conversation_id`) is any 1–96 char identifier matching
`^[A-Za-z0-9._:-]{1,96}$`; reuse it to continue the same thread. Omit it
to get a fresh one back in the response.

Response:
```json
{
  "id": "resp_<conversation>",
  "object": "response",
  "created_at": 1234567890,
  "model": "gpt-4o-mini",
  "conversation": "<conversation>",
  "output_text": "...",
  "output": [{"type": "message", "role": "assistant", "content": [{"type": "output_text", "text": "..."}]}]
}
```

### `POST /v1/chat/completions`
Same auth/body rules; OpenAI chat-completions-shaped response instead.

### Error shape (any 4xx/5xx)
```json
{"error": {"code": "unauthorized", "message": "..."}}
```
Codes used: `unauthorized` (401), `rate_limited` (429), `invalid_request` /
`invalid_utf8` / `invalid_content_length` (400), `request_too_large` (413),
`unsupported_media_type` (415), `backend_error` (502/503), `not_found` (404).

## Admin API (default `:8444`, loopback-only by default)

Requires `Authorization: Bearer <EDGE_ADMIN_TOKEN>`. This is the
operator's credential, not a device's — never surface it to a device,
and only surface it to a browser page whose exact origin you've put in
`EDGE_ADMIN_CORS_ORIGIN`.

### `POST /admin/devices`
Body (optional): `{"label": "esp32-desk-01"}`

`201`:
```json
{"id": "a1b2c3d4e5f6a7b8", "token": "<plaintext, shown exactly once>", "label": "esp32-desk-01", "created_at": 1234567890}
```

### `GET /admin/devices`
`200 {"devices": [{"id": "...", "label": "...", "created_at": 1234567890, "revoked": false}, ...]}`
Never returns a token or its hash.

### `DELETE /admin/devices/{id}`
`204` on success. `404 {"error":{"code":"not_found", ...}}` if the id
doesn't exist or was already revoked. Takes effect immediately — the
device's next request gets `401`, no gateway restart needed.
