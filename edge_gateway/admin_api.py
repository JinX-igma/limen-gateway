from __future__ import absolute_import

import hmac
import json
import logging
import ssl
from http.server import BaseHTTPRequestHandler, HTTPServer
from socketserver import ThreadingMixIn

from .devices_store import DeviceError

LOG = logging.getLogger("edge_gateway.admin")


class ThreadingHTTPServer(ThreadingMixIn, HTTPServer):
    daemon_threads = True
    allow_reuse_address = True


class AdminHTTPServer(ThreadingHTTPServer):
    def __init__(self, address, handler, devices_store, admin_token, cors_origin):
        ThreadingHTTPServer.__init__(self, address, handler)
        self.devices_store = devices_store
        self.admin_token = admin_token
        self.cors_origin = cors_origin


class AdminHandler(BaseHTTPRequestHandler):
    """Separate listener from the device-facing API, loopback-only by
    default (see Config._validate_listener). This is the operator's
    "mint/revoke a device" surface — never reachable by a device, and
    never allowed cross-origin unless EDGE_ADMIN_CORS_ORIGIN explicitly
    allowlists the flasher page's origin.
    """

    server_version = "LimenGatewayAdmin/0.1"
    sys_version = ""

    def log_message(self, fmt, *args):
        LOG.info("client=%s %s", self.client_address[0], fmt % args)

    def _cors_headers(self):
        origin = self.headers.get("Origin")
        allowed = self.server.cors_origin
        if origin and allowed and origin == allowed:
            self.send_header("Access-Control-Allow-Origin", allowed)
            self.send_header("Vary", "Origin")

    def _json(self, status, document):
        payload = json.dumps(document, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self._cors_headers()
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(payload)

    def _error(self, status, code, message):
        self._json(status, {"error": {"code": code, "message": message}})

    def _authorized(self):
        token = self.server.admin_token
        if not token:
            self._error(503, "admin_disabled", "admin API has no EDGE_ADMIN_TOKEN configured")
            return False
        header = self.headers.get("Authorization", "")
        prefix = "Bearer "
        if not header.startswith(prefix) or not hmac.compare_digest(
            header[len(prefix):].strip(), token
        ):
            self._error(401, "unauthorized", "valid admin bearer token required")
            return False
        return True

    def do_OPTIONS(self):
        self.send_response(204)
        self._cors_headers()
        self.send_header("Access-Control-Allow-Methods", "GET, POST, DELETE, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Authorization, Content-Type")
        self.end_headers()

    def do_GET(self):
        if self.path == "/admin/devices":
            if not self._authorized():
                return
            self._json(200, {"devices": self.server.devices_store.list()})
            return
        self._error(404, "not_found", "endpoint not found")

    def do_POST(self):
        if self.path == "/admin/devices":
            if not self._authorized():
                return
            label = None
            length = int(self.headers.get("Content-Length", "0") or "0")
            if length:
                try:
                    document = json.loads(self.rfile.read(length).decode("utf-8"))
                    label = document.get("label") if isinstance(document, dict) else None
                except ValueError:
                    self._error(400, "invalid_request", "body must be JSON")
                    return
            record = self.server.devices_store.mint(label=label)
            self._json(201, record)
            return
        self._error(404, "not_found", "endpoint not found")

    def do_DELETE(self):
        prefix = "/admin/devices/"
        if self.path.startswith(prefix) and len(self.path) > len(prefix):
            if not self._authorized():
                return
            device_id = self.path[len(prefix):]
            try:
                self.server.devices_store.revoke(device_id)
            except DeviceError as exc:
                self._error(404, "not_found", str(exc))
                return
            self.send_response(204)
            self._cors_headers()
            self.end_headers()
            return
        self._error(404, "not_found", "endpoint not found")


def build_admin_server(config, devices_store):
    server = AdminHTTPServer(
        (config.admin_bind, config.admin_port),
        AdminHandler,
        devices_store,
        config.admin_token,
        config.admin_cors_origin,
    )
    if config.admin_tls_cert:
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.options |= ssl.OP_NO_TLSv1 | ssl.OP_NO_TLSv1_1
        context.load_cert_chain(config.admin_tls_cert, config.admin_tls_key)
        server.socket = context.wrap_socket(server.socket, server_side=True)
    return server
