from __future__ import absolute_import

import abc


class BackendError(RuntimeError):
    """Raised when an LLM backend cannot complete a turn."""


class Backend(abc.ABC):
    """Common interface GatewayService drives regardless of upstream LLM.

    Implementations: backends/codex_backend.py (local ChatGPT-subscription
    Codex CLI, stdio-only) and backends/openai_api.py (server-held OpenAI
    API key, forwarded to the real Responses API). Both keep the device
    side identical: a device only ever sees a per-device bearer token and
    the gateway's own /v1/responses contract, never an upstream credential.

    `account` is a plain instance attribute (not abstract) each backend
    sets after start(): a dict such as {"type": "api_key", "planType": None}
    or {"type": "chatgpt", "planType": "plus"}, surfaced at GET /v1/account.
    """

    account = None

    @abc.abstractmethod
    def start(self):
        raise NotImplementedError

    @abc.abstractmethod
    def close(self):
        raise NotImplementedError

    @abc.abstractmethod
    def healthy(self):
        raise NotImplementedError

    @abc.abstractmethod
    def create_thread(self, developer_instructions):
        """Start a new conversation, returning an opaque thread id."""
        raise NotImplementedError

    @abc.abstractmethod
    def run_turn(self, thread_id, text):
        """Send one user turn on an existing thread, return assistant text."""
        raise NotImplementedError
