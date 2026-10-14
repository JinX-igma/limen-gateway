from __future__ import absolute_import

import os
import tempfile
import unittest

from edge_gateway.devices_store import DeviceError, DevicesStore


class DevicesStoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = DevicesStore(os.path.join(self.temp.name, "db.sqlite3"))

    def tearDown(self):
        self.temp.cleanup()

    def test_mint_then_verify(self):
        record = self.store.mint(label="esp32-01")
        self.assertEqual(record["id"], self.store.verify_token(record["token"]))

    def test_duplicate_id_rejected(self):
        self.store.mint(device_id="fixed-id")
        with self.assertRaises(DeviceError):
            self.store.mint(device_id="fixed-id")

    def test_revoke_then_verify_fails(self):
        record = self.store.mint()
        self.store.revoke(record["id"])
        self.assertIsNone(self.store.verify_token(record["token"]))

    def test_revoke_unknown_device_raises(self):
        with self.assertRaises(DeviceError):
            self.store.revoke("does-not-exist")

    def test_revoke_twice_raises(self):
        record = self.store.mint()
        self.store.revoke(record["id"])
        with self.assertRaises(DeviceError):
            self.store.revoke(record["id"])

    def test_list_reports_status_without_leaking_token(self):
        record = self.store.mint(label="watch")
        listing = self.store.list()
        self.assertEqual(1, len(listing))
        self.assertEqual("watch", listing[0]["label"])
        self.assertFalse(listing[0]["revoked"])
        self.assertNotIn("token", listing[0])
        self.assertNotIn("token_sha256", listing[0])
        self.store.revoke(record["id"])
        listing = self.store.list()
        self.assertTrue(listing[0]["revoked"])

    def test_wrong_token_does_not_authenticate(self):
        self.store.mint()
        self.assertIsNone(self.store.verify_token("not-a-real-token"))


if __name__ == "__main__":
    unittest.main()
