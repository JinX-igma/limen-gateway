from __future__ import absolute_import

import json
import logging
import ssl
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from socketserver import ThreadingMixIn

from .backend import BackendError
from .service import normalize_input

LOG = logging.getLogger("edge_gateway.http")


class ThreadingHTTPServer(ThreadingMixIn, HTTPServer):
    daemon_threads = True
    allow_reuse_address = True


class GatewayHTTPServer(ThreadingHTTPServer):
    def __init__(self, address, handler, service, authenticator, limiter, config):
        ThreadingHTTPServer.__init__(self, address, handler)
        self.gateway_service = service
        self.authenticator = authenticator
        self.limiter = limiter
        self.config = config


class Handler(BaseHTTPRequestHandler):
    server_version = "LimenGateway/0.1"
    sys_version = ""

    def log_message(self, fmt, *args):
        LOG.info("client=%s %s", self.client_address[0], fmt % args)

    def _json(self, status, document):
        payload = json.dumps(document, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(payload)

    def _error(self, status, code, message):
        self._json(status, {"error": {"code": code, "message": message}})

    def _authenticate(self):
        device_id = self.server.authenticator.authenticate(self.headers.get("Authorization"))
        if device_id is None:
            self._error(401, "unauthorized", "valid device bearer token required")
            return None
        if not self.server.limiter.allow(device_id):
            self._error(429, "rate_limited", "device request limit reached; retry later")
            return None
        return device_id

    def do_GET(self):
        if self.path == "/healthz":
            status = 200 if self.server.gateway_service.backend.healthy() else 503
            self._json(status, {"status": "ok" if status == 200 else "unavailable"})
            return
        device_id = self._authenticate()
        if device_id is None:
            return
        if self.path == "/v1/models":
            model = getattr(self.server.config, "codex_model", "") or "gateway-default"
            self._json(200, {"object": "list", "data": [{"id": model, "object": "model"}]})
            return
        if self.path == "/v1/account":
            account = self.server.gateway_service.backend.account or {}
            self._json(200, {"auth_mode": account.get("type"), "plan_type": account.get("planType")})
            return
        self._error(404, "not_found", "endpoint not found")

    def do_POST(self):
        device_id = self._authenticate()
        if device_id is None:
            return
        if self.path not in ("/v1/responses", "/v1/chat/completions"):
            self._error(404, "not_found", "endpoint not found")
            return
        content_type = self.headers.get("Content-Type", "").split(";", 1)[0].strip().lower()
        if content_type != "application/json":
            self._error(415, "unsupported_media_type", "Content-Type must be application/json")
            return
        try:
            size = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            self._error(400, "invalid_content_length", "invalid Content-Length")
            return
        if size <= 0 or size > self.server.config.max_request_bytes:
            self._error(413, "request_too_large", "request body size is invalid")
            return
        try:
            document = json.loads(self.rfile.read(size).decode("utf-8"))
            if not isinstance(document, dict):
                raise ValueError("JSON body must be an object")
            text = normalize_input(document)
            conversation = document.get("conversation") or document.get("conversation_id")
            conversation, output = self.server.gateway_service.respond(
                device_id, text, conversation
            )
        except UnicodeDecodeError:
            self._error(400, "invalid_utf8", "request body must be UTF-8")
            return
        except (ValueError, KeyError) as exc:
            self._error(400, "invalid_request", str(exc))
            return
        except BackendError as exc:
            status = 503 if "busy" in str(exc) or "stopped" in str(exc) else 502
            self._error(status, "backend_error", str(exc))
            return
        now = int(time.time())
        model = getattr(self.server.config, "codex_model", "") or "gateway-default"
        if self.path == "/v1/chat/completions":
            self._json(
                200,
                {
                    "id": "chatcmpl_{}".format(conversation),
                    "object": "chat.completion",
                    "created": now,
                    "model": model,
                    "conversation": conversation,
                    "choices": [
                        {
                            "index": 0,
                            "message": {"role": "assistant", "content": output},
                            "finish_reason": "stop",
                        }
                    ],
                },
            )
        else:
            self._json(
                200,
                {
                    "id": "resp_{}".format(conversation),
                    "object": "response",
                    "created_at": now,
                    "model": model,
                    "conversation": conversation,
                    "output_text": output,
                    "output": [
                        {
                            "type": "message",
                            "role": "assistant",
                            "content": [{"type": "output_text", "text": output}],
                        }
                    ],
                },
            )


def build_server(config, service, authenticator, limiter):
    server = GatewayHTTPServer(
        (config.bind, config.port), Handler, service, authenticator, limiter, config
    )
    if config.tls_cert:
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.options |= ssl.OP_NO_TLSv1 | ssl.OP_NO_TLSv1_1
        context.load_cert_chain(config.tls_cert, config.tls_key)
        server.socket = context.wrap_socket(server.socket, server_side=True)
    return server
