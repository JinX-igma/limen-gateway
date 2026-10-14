from __future__ import absolute_import

import json
import threading
import urllib.error
import urllib.request
import uuid

from ..backend import Backend, BackendError


class OpenAIBackend(Backend):
    """Generic backend: forwards turns to the real OpenAI Responses API
    using a server-side API key. The API key never leaves the gateway
    process — devices only ever hold a revocable per-device bearer token
    and talk to this gateway's own /v1/responses contract.

    Conversation history is kept in process memory, keyed by thread id,
    and replayed on every turn (OpenAI's Responses API is stateless per
    call). This is lost on a gateway restart, same documented limitation
    as RateLimiter's in-memory window.
    """

    DEFAULT_BASE_URL = "https://api.openai.com/v1/responses"

    def __init__(self, api_key, model="gpt-4o-mini", timeout=60, base_url=None):
        if not api_key:
            raise ValueError("OPENAI_API_KEY is required for the openai_api backend")
        self.api_key = api_key
        self.model = model
        self.timeout = timeout
        self.base_url = base_url or self.DEFAULT_BASE_URL
        self.account = {"type": "api_key", "planType": None}
        self._lock = threading.Lock()
        self._threads = {}
        self._healthy = False

    def start(self):
        self._healthy = True

    def close(self):
        self._healthy = False

    def healthy(self):
        return self._healthy

    def create_thread(self, developer_instructions):
        thread_id = "oa-{}".format(uuid.uuid4().hex)
        with self._lock:
            self._threads[thread_id] = {
                "instructions": developer_instructions,
                "messages": [],
            }
        return thread_id

    def run_turn(self, thread_id, text):
        with self._lock:
            thread = self._threads.get(thread_id)
            if thread is None:
                raise BackendError("unknown thread: {}".format(thread_id))
            thread["messages"].append({"role": "user", "content": text})
            messages = list(thread["messages"])
            instructions = thread["instructions"]

        body = {
            "model": self.model,
            "instructions": instructions,
            "input": [{"role": m["role"], "content": m["content"]} for m in messages],
        }
        output_text = self._call(body)

        with self._lock:
            thread = self._threads.get(thread_id)
            if thread is not None:
                thread["messages"].append({"role": "assistant", "content": output_text})
        return output_text

    def _call(self, body):
        data = json.dumps(body).encode("utf-8")
        request = urllib.request.Request(
            self.base_url,
            data=data,
            method="POST",
            headers={
                "Authorization": "Bearer {}".format(self.api_key),
                "Content-Type": "application/json",
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                document = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", "replace")[:500]
            raise BackendError("OpenAI API error {}: {}".format(exc.code, detail))
        except urllib.error.URLError as exc:
            raise BackendError("OpenAI API unreachable: {}".format(exc.reason))
        return extract_output_text(document)


def extract_output_text(document):
    text = document.get("output_text")
    if isinstance(text, str) and text:
        return text
    chunks = []
    for item in document.get("output", []) or []:
        if item.get("type") != "message":
            continue
        for part in item.get("content", []) or []:
            if part.get("type") == "output_text" and isinstance(part.get("text"), str):
                chunks.append(part["text"])
    if not chunks:
        raise BackendError("OpenAI response contained no output text")
    return "".join(chunks)
