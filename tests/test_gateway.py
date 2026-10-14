from __future__ import absolute_import

import os
import tempfile
import unittest

from edge_gateway.auth import DeviceAuthenticator, RateLimiter
from edge_gateway.devices_store import DevicesStore
from edge_gateway.service import GatewayService, normalize_input
from edge_gateway.store import ConversationStore


class FakeBackend(object):
    def __init__(self):
        self.created = 0
        self.turns = []
        self.account = {"type": "fake", "planType": None}

    def create_thread(self, instructions):
        self.created += 1
        self.instructions = instructions
        return "thread-{}".format(self.created)

    def run_turn(self, thread_id, text):
        self.turns.append((thread_id, text))
        return "answer: " + text

    def healthy(self):
        return True


class GatewayTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()

    def tearDown(self):
        self.temp.cleanup()

    def test_authenticator_uses_live_store(self):
        store = DevicesStore(os.path.join(self.temp.name, "db.sqlite3"))
        record = store.mint(label="watch")
        auth = DeviceAuthenticator(store)
        self.assertEqual(record["id"], auth.authenticate("Bearer " + record["token"]))
        self.assertIsNone(auth.authenticate("Bearer wrong"))
        self.assertIsNone(auth.authenticate(None))

    def test_revoked_device_cannot_authenticate(self):
        store = DevicesStore(os.path.join(self.temp.name, "db.sqlite3"))
        record = store.mint(label="watch")
        auth = DeviceAuthenticator(store)
        self.assertIsNotNone(auth.authenticate("Bearer " + record["token"]))
        store.revoke(record["id"])
        self.assertIsNone(auth.authenticate("Bearer " + record["token"]))

    def test_rate_limit(self):
        limiter = RateLimiter(2)
        self.assertTrue(limiter.allow("watch", now=100))
        self.assertTrue(limiter.allow("watch", now=101))
        self.assertFalse(limiter.allow("watch", now=102))
        self.assertTrue(limiter.allow("watch", now=161))

    def test_conversation_reuses_thread(self):
        backend = FakeBackend()
        store = ConversationStore(os.path.join(self.temp.name, "db.sqlite3"))
        service = GatewayService(backend, store)
        first = service.respond("watch", "one", "session")
        second = service.respond("watch", "two", "session")
        self.assertEqual("session", first[0])
        self.assertEqual("answer: two", second[1])
        self.assertEqual(1, backend.created)
        self.assertEqual([("thread-1", "one"), ("thread-1", "two")], backend.turns)

    def test_normalize_chat_messages(self):
        text = normalize_input({"messages": [{"role": "user", "content": "hello"}]})
        self.assertEqual("user: hello", text)


if __name__ == "__main__":
    unittest.main()
