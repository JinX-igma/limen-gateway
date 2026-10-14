from __future__ import absolute_import

import hashlib
import threading
import time
from collections import defaultdict, deque


def token_digest(token):
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


class DeviceAuthenticator(object):
    """Validates device bearer tokens against a live DevicesStore.

    Unlike the original one-shot devices.json loaded at process start,
    this holds no cache of its own — every call hits the store, so a
    device minted or revoked via the admin API takes effect on its very
    next request, no restart required.
    """

    def __init__(self, devices_store):
        self._store = devices_store

    def authenticate(self, authorization):
        prefix = "Bearer "
        if not authorization or not authorization.startswith(prefix):
            return None
        token = authorization[len(prefix):].strip()
        if not token:
            return None
        return self._store.verify_token(token)


class RateLimiter(object):
    def __init__(self, requests_per_minute):
        self._limit = requests_per_minute
        self._events = defaultdict(deque)
        self._lock = threading.Lock()

    def allow(self, device_id, now=None):
        now = time.time() if now is None else now
        cutoff = now - 60.0
        with self._lock:
            events = self._events[device_id]
            while events and events[0] <= cutoff:
                events.popleft()
            if len(events) >= self._limit:
                return False
            events.append(now)
            return True
