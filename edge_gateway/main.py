from __future__ import absolute_import

import json
import logging
import signal
import sys
import threading

from .admin_api import build_admin_server
from .auth import DeviceAuthenticator, RateLimiter
from .backends.codex_backend import CodexBackend
from .backends.openai_api import OpenAIBackend
from .config import Config
from .devices_store import DevicesStore
from .http_api import build_server
from .service import GatewayService
from .store import ConversationStore


def configure_logging():
    logging.basicConfig(
        level=logging.INFO,
        format='{"time":"%(asctime)s","level":"%(levelname)s","logger":"%(name)s","message":%(message_json)s}',
    )


class JsonMessageFilter(logging.Filter):
    def filter(self, record):
        record.message_json = json.dumps(record.getMessage())
        return True


def build_backend(config):
    if config.backend_name == "openai_api":
        return OpenAIBackend(
            config.openai_api_key,
            config.openai_model,
            config.request_timeout,
            config.openai_base_url,
        )
    return CodexBackend(config.codex_bin, config.work_dir, config.request_timeout, config.codex_model)


def main():
    configure_logging()
    for handler in logging.getLogger().handlers:
        handler.addFilter(JsonMessageFilter())
    config = Config()
    try:
        config.validate()
        devices_store = DevicesStore(config.database)
        authenticator = DeviceAuthenticator(devices_store)
        limiter = RateLimiter(config.requests_per_minute)
        store = ConversationStore(config.database)
        backend = build_backend(config)
        backend.start()
        service = GatewayService(backend, store, config.max_concurrent, config.max_output_chars)
        device_server = build_server(config, service, authenticator, limiter)
        admin_server = build_admin_server(config, devices_store)
    except Exception as exc:
        logging.getLogger("edge_gateway").error("startup failed: %s", exc)
        return 1

    def stop(signum, frame):
        del signum, frame
        for server in (device_server, admin_server):
            thread = threading.Thread(target=server.shutdown, name="gateway-shutdown")
            thread.daemon = True
            thread.start()

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)

    scheme = "https" if config.tls_cert else "http"
    admin_scheme = "https" if config.admin_tls_cert else "http"
    logging.getLogger("edge_gateway").info(
        "device API on %s://%s:%s, admin API on %s://%s:%s, backend=%s",
        scheme,
        config.bind,
        config.port,
        admin_scheme,
        config.admin_bind,
        config.admin_port,
        config.backend_name,
    )

    admin_thread = threading.Thread(
        target=admin_server.serve_forever,
        kwargs={"poll_interval": 0.5},
        name="admin-server",
    )
    admin_thread.daemon = True
    admin_thread.start()

    try:
        device_server.serve_forever(poll_interval=0.5)
    finally:
        device_server.server_close()
        admin_server.shutdown()
        admin_server.server_close()
        backend.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
