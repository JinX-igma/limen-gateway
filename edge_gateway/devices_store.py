from __future__ import absolute_import

import hmac
import os
import secrets
import sqlite3
import threading
import time

from .auth import token_digest


class DeviceError(RuntimeError):
    pass


class DevicesStore(object):
    """SQLite-backed device identity store: mint, revoke, list, verify.

    Replaces the old one-shot devices.json (loaded once at process start,
    hardcoded to exactly two device ids, refused to be regenerated) with a
    live table that the admin API and a headless CLI both write through,
    and that DeviceAuthenticator reads on every request — no server
    restart needed to add or revoke a device.

    Shares the same SQLite file as ConversationStore (different table,
    same WAL-mode database); this is a deliberate choice over a second
    file-locking scheme, see docs/architecture.md.
    """

    def __init__(self, path):
        directory = os.path.dirname(os.path.abspath(path))
        if directory and not os.path.isdir(directory):
            os.makedirs(directory)
        self._path = path
        self._lock = threading.Lock()
        self._initialize()

    def _connect(self):
        connection = sqlite3.connect(self._path, timeout=10)
        connection.execute("PRAGMA journal_mode=WAL")
        return connection

    def _initialize(self):
        with self._connect() as connection:
            connection.execute(
                "CREATE TABLE IF NOT EXISTS devices ("
                "id TEXT PRIMARY KEY, token_sha256 TEXT NOT NULL, "
                "label TEXT, created_at INTEGER NOT NULL, revoked_at INTEGER)"
            )

    def mint(self, label=None, device_id=None):
        device_id = device_id or secrets.token_hex(8)
        token = secrets.token_urlsafe(32)
        now = int(time.time())
        with self._lock:
            with self._connect() as connection:
                try:
                    connection.execute(
                        "INSERT INTO devices (id, token_sha256, label, created_at, revoked_at) "
                        "VALUES (?, ?, ?, ?, NULL)",
                        (device_id, token_digest(token), label, now),
                    )
                except sqlite3.IntegrityError:
                    raise DeviceError("device id already exists: {}".format(device_id))
        return {"id": device_id, "token": token, "label": label, "created_at": now}

    def revoke(self, device_id):
        now = int(time.time())
        with self._lock:
            with self._connect() as connection:
                cursor = connection.execute(
                    "UPDATE devices SET revoked_at = ? WHERE id = ? AND revoked_at IS NULL",
                    (now, device_id),
                )
                if cursor.rowcount == 0:
                    raise DeviceError(
                        "device not found or already revoked: {}".format(device_id)
                    )

    def list(self):
        with self._lock:
            with self._connect() as connection:
                rows = connection.execute(
                    "SELECT id, label, created_at, revoked_at FROM devices ORDER BY created_at"
                ).fetchall()
        return [
            {"id": row[0], "label": row[1], "created_at": row[2], "revoked": row[3] is not None}
            for row in rows
        ]

    def verify_token(self, token):
        """Constant-time lookup: hash once, compare against every active
        digest without short-circuiting on match, mirroring the original
        DeviceAuthenticator's full-loop comparison over devices.json."""
        presented = token_digest(token)
        with self._lock:
            with self._connect() as connection:
                rows = connection.execute(
                    "SELECT id, token_sha256 FROM devices WHERE revoked_at IS NULL"
                ).fetchall()
        matched = None
        for device_id, digest in rows:
            if hmac.compare_digest(presented, digest):
                matched = device_id
        return matched
