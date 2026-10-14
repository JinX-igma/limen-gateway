from __future__ import absolute_import

import os

_LOOPBACK = ("127.0.0.1", "localhost", "::1")


def _integer(name, default, minimum=1):
    raw = os.environ.get(name, str(default))
    try:
        value = int(raw)
    except ValueError:
        raise ValueError("{} must be an integer".format(name))
    if value < minimum:
        raise ValueError("{} must be >= {}".format(name, minimum))
    return value


class Config(object):
    def __init__(self):
        root = os.path.abspath(os.environ.get("EDGE_ROOT", os.getcwd()))
        self.root = root

        # Device-facing API (what embedded devices call)
        self.bind = os.environ.get("EDGE_BIND", "127.0.0.1")
        self.port = _integer("EDGE_PORT", 8443, minimum=0)
        self.tls_cert = os.environ.get("EDGE_TLS_CERT", "")
        self.tls_key = os.environ.get("EDGE_TLS_KEY", "")

        # Admin API (device registration) — separate listener, loopback by default
        self.admin_bind = os.environ.get("EDGE_ADMIN_BIND", "127.0.0.1")
        self.admin_port = _integer("EDGE_ADMIN_PORT", 8444, minimum=0)
        self.admin_tls_cert = os.environ.get("EDGE_ADMIN_TLS_CERT", self.tls_cert)
        self.admin_tls_key = os.environ.get("EDGE_ADMIN_TLS_KEY", self.tls_key)
        self.admin_token = os.environ.get("EDGE_ADMIN_TOKEN", "")
        self.admin_cors_origin = os.environ.get("EDGE_ADMIN_CORS_ORIGIN", "")

        self.database = os.environ.get(
            "EDGE_DATABASE", os.path.join(root, "data", "gateway.sqlite3")
        )

        # Backend selection
        self.backend_name = os.environ.get("EDGE_BACKEND", "openai_api")
        self.codex_bin = os.environ.get("EDGE_CODEX_BIN", "codex")
        self.codex_model = os.environ.get("EDGE_CODEX_MODEL", "")
        self.work_dir = os.environ.get(
            "EDGE_AGENT_WORK_DIR", os.path.join(root, "runtime", "agent-empty")
        )
        self.openai_api_key = os.environ.get("OPENAI_API_KEY", "")
        self.openai_model = os.environ.get("OPENAI_MODEL", "gpt-4o-mini")
        self.openai_base_url = os.environ.get(
            "OPENAI_BASE_URL", "https://api.openai.com/v1/responses"
        )

        self.max_request_bytes = _integer("EDGE_MAX_REQUEST_BYTES", 32768)
        self.max_output_chars = _integer("EDGE_MAX_OUTPUT_CHARS", 12000)
        self.request_timeout = _integer("EDGE_REQUEST_TIMEOUT_SECONDS", 180)
        self.requests_per_minute = _integer("EDGE_REQUESTS_PER_MINUTE", 10)
        self.max_concurrent = _integer("EDGE_MAX_CONCURRENT", 2)

    def validate(self):
        self._validate_listener("device", self.bind, self.tls_cert, self.tls_key)
        self._validate_listener("admin", self.admin_bind, self.admin_tls_cert, self.admin_tls_key)
        if self.admin_bind not in _LOOPBACK and not self.admin_token:
            raise ValueError(
                "EDGE_ADMIN_TOKEN is required when the admin listener is not loopback-only"
            )
        if self.backend_name not in ("codex", "openai_api"):
            raise ValueError("EDGE_BACKEND must be 'codex' or 'openai_api'")
        if self.backend_name == "openai_api" and not self.openai_api_key:
            raise ValueError("OPENAI_API_KEY is required when EDGE_BACKEND=openai_api")

    @staticmethod
    def _validate_listener(name, bind, cert, key):
        if bind not in _LOOPBACK and not (cert and key):
            raise ValueError(
                "TLS certificate and key are required for the {} listener on a "
                "non-loopback address".format(name)
            )
        if bool(cert) != bool(key):
            raise ValueError("{} TLS cert and key must be set together".format(name))
        if cert and not os.path.isfile(cert):
            raise ValueError("{} TLS certificate not found: {}".format(name, cert))
        if key and not os.path.isfile(key):
            raise ValueError("{} TLS key not found: {}".format(name, key))
