from __future__ import absolute_import

import os
import sqlite3
import threading
import time


class ConversationStore(object):
    def __init__(self, path):
        directory = os.path.dirname(os.path.abspath(path))
        if not os.path.isdir(directory):
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
                "CREATE TABLE IF NOT EXISTS conversations ("
                "device_id TEXT NOT NULL, conversation_id TEXT NOT NULL, "
                "thread_id TEXT NOT NULL, updated_at INTEGER NOT NULL, "
                "PRIMARY KEY (device_id, conversation_id))"
            )

    def get(self, device_id, conversation_id):
        with self._lock:
            with self._connect() as connection:
                row = connection.execute(
                    "SELECT thread_id FROM conversations "
                    "WHERE device_id = ? AND conversation_id = ?",
                    (device_id, conversation_id),
                ).fetchone()
        return row[0] if row else None

    def put(self, device_id, conversation_id, thread_id):
        now = int(time.time())
        with self._lock:
            with self._connect() as connection:
                connection.execute(
                    "INSERT OR REPLACE INTO conversations "
                    "(device_id, conversation_id, thread_id, updated_at) "
                    "VALUES (?, ?, ?, ?)",
                    (device_id, conversation_id, thread_id, now),
                )
