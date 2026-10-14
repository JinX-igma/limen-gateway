from __future__ import absolute_import

import json
import logging
import os
import queue
import subprocess
import threading
import time

from ..backend import Backend, BackendError

LOG = logging.getLogger("edge_gateway.codex_backend")


class CodexBackend(Backend):
    """Thin JSONL client for a local, ChatGPT-authenticated Codex app-server.

    Scope: the operator's own ChatGPT Plus/Pro subscription, never an API
    key. start() refuses to proceed unless the locally logged-in Codex CLI
    reports account.type == "chatgpt" — this guard must not be relaxed,
    it is the whole reason this backend exists alongside openai_api.py.
    """

    def __init__(self, codex_bin, work_dir, timeout=180, model=""):
        self.codex_bin = codex_bin
        self.work_dir = os.path.abspath(work_dir)
        self.timeout = timeout
        self.model = model
        self.account = None
        self._proc = None
        self._reader = None
        self._stderr_reader = None
        self._write_lock = threading.Lock()
        self._state_lock = threading.Lock()
        self._next_id = 1
        self._pending = {}
        self._event_queues = {}
        self._active_threads = set()

    def start(self):
        if self._proc is not None:
            return
        if not os.path.isdir(self.work_dir):
            os.makedirs(self.work_dir)
        command = [
            self.codex_bin,
            "app-server",
            "--listen",
            "stdio://",
            "--disable",
            "shell_tool",
            "--disable",
            "unified_exec",
            "--disable",
            "apps",
            "--disable",
            "browser_use",
            "--disable",
            "computer_use",
            "--disable",
            "image_generation",
            "--disable",
            "multi_agent",
            "--disable",
            "goals",
            "--disable",
            "plugins",
        ]
        self._proc = subprocess.Popen(
            command,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            cwd=self.work_dir,
            universal_newlines=True,
            bufsize=1,
        )
        self._reader = threading.Thread(target=self._read_stdout, name="codex-jsonl")
        self._reader.daemon = True
        self._reader.start()
        self._stderr_reader = threading.Thread(target=self._read_stderr, name="codex-stderr")
        self._stderr_reader.daemon = True
        self._stderr_reader.start()
        self.request(
            "initialize",
            {
                "capabilities": {"experimentalApi": True},
                "clientInfo": {
                    "name": "limen_gateway",
                    "title": "Limen Gateway",
                    "version": "0.1.0",
                },
            },
            timeout=30,
        )
        self.notify("initialized", {})
        result = self.request("account/read", {"refreshToken": True}, timeout=30)
        account = result.get("account") or {}
        if account.get("type") != "chatgpt":
            self.close()
            raise BackendError(
                "Codex must be logged in with ChatGPT; current account type is {!r}".format(
                    account.get("type")
                )
            )
        self.account = {"type": "chatgpt", "planType": account.get("planType", "unknown")}
        LOG.info("Codex backend authenticated with ChatGPT plan=%s", self.account["planType"])

    def close(self):
        proc = self._proc
        self._proc = None
        if proc is not None and proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except TypeError:
                proc.wait()
            except subprocess.TimeoutExpired:
                proc.kill()

    def healthy(self):
        return self._proc is not None and self._proc.poll() is None and self.account is not None

    def _allocate_id(self):
        with self._state_lock:
            request_id = self._next_id
            self._next_id += 1
            return request_id

    def _send(self, message):
        proc = self._proc
        if proc is None or proc.poll() is not None:
            raise BackendError("Codex app-server is not running")
        wire = json.dumps(message, separators=(",", ":"), ensure_ascii=False)
        with self._write_lock:
            proc.stdin.write(wire + "\n")
            proc.stdin.flush()

    def notify(self, method, params):
        self._send({"method": method, "params": params})

    def request(self, method, params, timeout=None):
        request_id = self._allocate_id()
        responses = queue.Queue(maxsize=1)
        with self._state_lock:
            self._pending[request_id] = responses
        try:
            self._send({"method": method, "id": request_id, "params": params})
            try:
                message = responses.get(timeout=timeout or self.timeout)
            except queue.Empty:
                raise BackendError("Codex request timed out: {}".format(method))
            if "error" in message:
                error = message["error"]
                raise BackendError("{}: {}".format(method, error.get("message", error)))
            return message.get("result") or {}
        finally:
            with self._state_lock:
                self._pending.pop(request_id, None)

    def _read_stdout(self):
        try:
            for raw_line in self._proc.stdout:
                line = raw_line.strip()
                if not line:
                    continue
                try:
                    message = json.loads(line)
                except ValueError:
                    LOG.warning("Discarded non-JSON Codex output")
                    continue
                request_id = message.get("id")
                method = message.get("method")
                if request_id is not None and not method:
                    with self._state_lock:
                        waiter = self._pending.get(request_id)
                    if waiter is not None:
                        waiter.put(message)
                    continue
                if request_id is not None and method:
                    self._deny_server_request(message)
                    continue
                params = message.get("params") or {}
                thread_id = params.get("threadId")
                with self._state_lock:
                    event_queue = self._event_queues.get(thread_id)
                if event_queue is not None:
                    event_queue.put(message)
        except Exception:
            LOG.exception("Codex stdout reader stopped")
        finally:
            self._fail_pending("Codex app-server stopped")

    def _read_stderr(self):
        for line in self._proc.stderr:
            text = line.strip()
            if text:
                LOG.warning("codex: %s", text[:1000])

    def _fail_pending(self, reason):
        with self._state_lock:
            waiters = list(self._pending.values())
        for waiter in waiters:
            try:
                waiter.put_nowait({"error": {"message": reason}})
            except queue.Full:
                pass

    def _deny_server_request(self, message):
        method = message.get("method", "")
        LOG.warning("Denied unexpected Codex server request method=%s", method)
        self._send({"id": message["id"], "result": {"decision": "decline"}})

    def create_thread(self, developer_instructions):
        params = {
            "approvalPolicy": "never",
            "approvalsReviewer": "user",
            "cwd": self.work_dir,
            "developerInstructions": developer_instructions,
            "dynamicTools": [],
            "environments": [],
            "ephemeral": False,
            "runtimeWorkspaceRoots": [self.work_dir],
            "sandbox": "read-only",
        }
        if self.model:
            params["model"] = self.model
        result = self.request("thread/start", params)
        thread = result.get("thread") or {}
        thread_id = thread.get("id")
        if not thread_id:
            raise BackendError("thread/start returned no thread id")
        with self._state_lock:
            self._active_threads.add(thread_id)
        return thread_id

    def _ensure_thread(self, thread_id):
        with self._state_lock:
            active = thread_id in self._active_threads
        if active:
            return
        self.request(
            "thread/resume",
            {
                "threadId": thread_id,
                "approvalPolicy": "never",
                "cwd": self.work_dir,
                "sandbox": "read-only",
            },
        )
        with self._state_lock:
            self._active_threads.add(thread_id)

    def run_turn(self, thread_id, text):
        self._ensure_thread(thread_id)
        events = queue.Queue()
        with self._state_lock:
            if thread_id in self._event_queues:
                raise BackendError("a turn is already active for this conversation")
            self._event_queues[thread_id] = events
        turn_id = None
        chunks = []
        try:
            result = self.request(
                "turn/start",
                {
                    "threadId": thread_id,
                    "input": [{"type": "text", "text": text, "text_elements": []}],
                    "approvalPolicy": "never",
                    "cwd": self.work_dir,
                    "environments": [],
                    "sandboxPolicy": {"type": "readOnly", "networkAccess": False},
                },
            )
            turn_id = (result.get("turn") or {}).get("id")
            deadline = time.time() + self.timeout
            while True:
                remaining = deadline - time.time()
                if remaining <= 0:
                    raise BackendError("Codex turn timed out")
                try:
                    event = events.get(timeout=remaining)
                except queue.Empty:
                    raise BackendError("Codex turn timed out")
                method = event.get("method")
                params = event.get("params") or {}
                if turn_id and params.get("turnId") not in (None, turn_id):
                    continue
                if method == "item/agentMessage/delta":
                    chunks.append(params.get("delta", ""))
                elif method == "error":
                    raise BackendError(params.get("message", "Codex turn failed"))
                elif method == "turn/completed":
                    turn = params.get("turn") or {}
                    status = turn.get("status", "completed")
                    if status != "completed":
                        error = turn.get("error") or {}
                        raise BackendError(error.get("message", "turn status: {}".format(status)))
                    return "".join(chunks).strip()
        finally:
            with self._state_lock:
                self._event_queues.pop(thread_id, None)
