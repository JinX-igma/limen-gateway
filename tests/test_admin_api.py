from __future__ import absolute_import

import json
import os
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from types import SimpleNamespace

from edge_gateway.admin_api import build_admin_server
from edge_gateway.devices_store import DevicesStore


class AdminAPITests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = DevicesStore(os.path.join(self.temp.name, "db.sqlite3"))
        self.admin_token = "test-admin-token"
        config = SimpleNamespace(
            admin_bind="127.0.0.1",
            admin_port=0,
            admin_tls_cert="",
            admin_tls_key="",
            admin_token=self.admin_token,
            admin_cors_origin="https://flasher.example",
        )
        self.server = build_admin_server(config, self.store)
        self.port = self.server.server_address[1]
        self.thread = threading.Thread(target=self.server.serve_forever, kwargs={"poll_interval": 0.1})
        self.thread.daemon = True
        self.thread.start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.temp.cleanup()

    def _request(self, method, path, body=None, token=None, origin=None):
        url = "http://127.0.0.1:{}{}".format(self.port, path)
        data = json.dumps(body).encode("utf-8") if body is not None else None
        request = urllib.request.Request(url, data=data, method=method)
        if token is not None:
            request.add_header("Authorization", "Bearer {}".format(token))
        if origin is not None:
            request.add_header("Origin", origin)
        if data is not None:
            request.add_header("Content-Type", "application/json")
        try:
            with urllib.request.urlopen(request, timeout=5) as response:
                payload = response.read()
                return response.status, (json.loads(payload) if payload else None), response.headers
        except urllib.error.HTTPError as exc:
            payload = exc.read()
            return exc.code, (json.loads(payload) if payload else None), exc.headers

    def test_mint_requires_admin_token(self):
        status, document, _ = self._request("POST", "/admin/devices", {"label": "x"})
        self.assertEqual(401, status)
        self.assertEqual("unauthorized", document["error"]["code"])

    def test_mint_list_revoke_roundtrip(self):
        status, minted, _ = self._request(
            "POST", "/admin/devices", {"label": "esp32-01"}, token=self.admin_token
        )
        self.assertEqual(201, status)
        self.assertIn("token", minted)
        device_id = minted["id"]

        status, listing, _ = self._request("GET", "/admin/devices", token=self.admin_token)
        self.assertEqual(200, status)
        self.assertEqual(1, len(listing["devices"]))
        self.assertNotIn("token", listing["devices"][0])

        self.assertIsNotNone(self.store.verify_token(minted["token"]))

        status, _, _ = self._request(
            "DELETE", "/admin/devices/" + device_id, token=self.admin_token
        )
        self.assertEqual(204, status)
        self.assertIsNone(self.store.verify_token(minted["token"]))

    def test_cors_origin_allowlist(self):
        status, _, headers = self._request(
            "GET", "/admin/devices", token=self.admin_token, origin="https://flasher.example"
        )
        self.assertEqual(200, status)
        self.assertEqual("https://flasher.example", headers.get("Access-Control-Allow-Origin"))

        status, _, headers = self._request(
            "GET", "/admin/devices", token=self.admin_token, origin="https://evil.example"
        )
        self.assertEqual(200, status)
        self.assertIsNone(headers.get("Access-Control-Allow-Origin"))

    def test_revoke_unknown_device_is_404(self):
        status, document, _ = self._request(
            "DELETE", "/admin/devices/does-not-exist", token=self.admin_token
        )
        self.assertEqual(404, status)
        self.assertEqual("not_found", document["error"]["code"])


if __name__ == "__main__":
    unittest.main()
