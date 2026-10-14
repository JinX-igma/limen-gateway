from __future__ import absolute_import

import io
import json
import unittest
from unittest import mock

from edge_gateway.backend import BackendError
from edge_gateway.backends.openai_api import OpenAIBackend, extract_output_text


class FakeResponse(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


class ExtractOutputTextTests(unittest.TestCase):
    def test_prefers_output_text_field(self):
        document = {"output_text": "hi there"}
        self.assertEqual("hi there", extract_output_text(document))

    def test_falls_back_to_output_items(self):
        document = {
            "output": [
                {
                    "type": "message",
                    "content": [{"type": "output_text", "text": "part one"}],
                }
            ]
        }
        self.assertEqual("part one", extract_output_text(document))

    def test_raises_when_no_text_found(self):
        with self.assertRaises(BackendError):
            extract_output_text({"output": []})


class OpenAIBackendTests(unittest.TestCase):
    def test_requires_api_key(self):
        with self.assertRaises(ValueError):
            OpenAIBackend(api_key="")

    def test_run_turn_replays_history_and_appends_reply(self):
        backend = OpenAIBackend(api_key="sk-test", model="gpt-test")
        backend.start()
        thread_id = backend.create_thread("be concise")

        calls = []

        def fake_urlopen(request, timeout=None):
            body = json.loads(request.data.decode("utf-8"))
            calls.append(body)
            self.assertEqual(
                "Bearer sk-test", request.headers.get("Authorization")
            )
            reply = {"output_text": "reply {}".format(len(calls))}
            return FakeResponse(json.dumps(reply).encode("utf-8"))

        with mock.patch("edge_gateway.backends.openai_api.urllib.request.urlopen", fake_urlopen):
            first = backend.run_turn(thread_id, "hello")
            second = backend.run_turn(thread_id, "how are you")

        self.assertEqual("reply 1", first)
        self.assertEqual("reply 2", second)
        self.assertEqual("be concise", calls[0]["instructions"])
        self.assertEqual([{"role": "user", "content": "hello"}], calls[0]["input"])
        self.assertEqual(
            [
                {"role": "user", "content": "hello"},
                {"role": "assistant", "content": "reply 1"},
                {"role": "user", "content": "how are you"},
            ],
            calls[1]["input"],
        )

    def test_unknown_thread_raises(self):
        backend = OpenAIBackend(api_key="sk-test")
        with self.assertRaises(BackendError):
            backend.run_turn("not-a-real-thread", "hi")


if __name__ == "__main__":
    unittest.main()
