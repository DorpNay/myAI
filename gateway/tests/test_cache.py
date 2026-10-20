import json
import os
import tempfile
import unittest

from fastapi.testclient import TestClient

import main
from main import canonical_key, is_cacheable, should_bypass_cache


class CachePolicyTests(unittest.TestCase):
    def setUp(self):
        self.payload = {
            "model": "qwen3:1.7b",
            "messages": [{"role": "user", "content": "What is cache?"}],
            "temperature": 0,
        }

    def test_identical_payload_has_stable_key(self):
        payload = {"model": "qwen3:4b", "messages": [{"role": "user", "content": "What is cache?"}]}
        self.assertEqual(
            canonical_key(payload),
            canonical_key(dict(reversed(list(payload.items())))),
        )

    def test_model_and_messages_are_part_of_key(self):
        first = {"model": "qwen3:4b", "messages": [{"role": "user", "content": "hello"}]}
        second = {"model": "qwen3:1.7b", "messages": [{"role": "user", "content": "hello"}]}
        self.assertNotEqual(canonical_key(first), canonical_key(second))

    def test_route_method_and_query_are_part_of_key(self):
        original = canonical_key(self.payload, path="chat/completions")
        self.assertNotEqual(original, canonical_key(self.payload, path="responses"))
        self.assertNotEqual(
            original, canonical_key(self.payload, method="GET", path="chat/completions")
        )
        self.assertNotEqual(
            original,
            canonical_key(self.payload, path="chat/completions", query="a=1"),
        )

    def test_basic_non_streaming_chat_is_cacheable(self):
        self.assertTrue(is_cacheable(self.payload))
        self.assertFalse(should_bypass_cache(
            self.payload, method="POST", path="chat/completions", headers={}
        ))
        # No explicit temperature means we do not assume upstream defaults are deterministic.
        without_temperature = dict(self.payload)
        without_temperature.pop("temperature")
        self.assertFalse(is_cacheable(without_temperature))
        self.assertTrue(should_bypass_cache(
            without_temperature, method="POST", path="chat/completions", headers={}
        ))

    def test_only_chat_completions_post_is_cacheable(self):
        self.assertTrue(should_bypass_cache(
            self.payload, method="GET", path="chat/completions", headers={}
        ))
        self.assertTrue(should_bypass_cache(
            self.payload, method="POST", path="models", headers={}
        ))

    def test_explicit_bypass_headers_are_honored(self):
        for headers in (
            {"x-ai-cache-bypass": "1"},
            {"x-ai-cache-bypass": "true"},
            {"cache-control": "no-cache"},
            {"cache-control": "private, no-store"},
        ):
            with self.subTest(headers=headers):
                self.assertTrue(should_bypass_cache(
                    self.payload,
                    method="POST",
                    path="chat/completions",
                    headers=headers,
                ))

    def test_dynamic_requests_bypass_cache(self):
        cases = [
            {**self.payload, "stream": True},
            {**self.payload, "temperature": 0.8},
            {**self.payload, "temperature": "high"},
            {**self.payload, "tools": [{"type": "function"}]},
            {**self.payload, "function_call": {"name": "search"}},
            {"messages": [{"role": "tool", "content": "result"}]},
            {"messages": []},
            {"messages": [{"role": "user", "content": [
                {"type": "image_url", "image_url": {"url": "https://example.invalid/image.png"}}
            ]}]},
        ]
        for payload in cases:
            with self.subTest(payload=payload):
                self.assertFalse(is_cacheable(payload))
                self.assertTrue(should_bypass_cache(
                    payload, method="POST", path="chat/completions", headers={}
                ))

    def test_missing_or_invalid_payload_bypasses_cache(self):
        for payload in (None, {}, {"messages": "not-a-list"}):
            self.assertTrue(should_bypass_cache(
                payload, method="POST", path="chat/completions", headers={}
            ))


class FakeJsonResponse:
    status_code = 200
    headers = {"content-type": "application/json"}

    def __init__(self):
        self.data = {
            "id": "cmpl-test",
            "object": "chat.completion",
            "choices": [{
                "index": 0,
                "message": {"role": "assistant", "content": "cached answer"},
                "finish_reason": "stop",
            }],
        }
        self.content = json.dumps(self.data).encode("utf-8")

    def json(self):
        return self.data


class FakeStreamResponse:
    status_code = 200
    headers = {"content-type": "text/event-stream"}

    def __init__(self):
        self.closed = False
        self.iterated = False
        self.chunks = [
            b'data: {"choices":[{"delta":{"content":"hello"}}]}\n\n',
            b"data: [DONE]\n\n",
        ]

    async def aiter_bytes(self):
        self.iterated = True
        for chunk in self.chunks:
            yield chunk

    async def aclose(self):
        self.closed = True

    async def aread(self):
        return b"upstream error"


class FakeAsyncClient:
    def __init__(self):
        self.request_calls = 0
        self.send_calls = 0
        self.send_stream = False
        self.stream_response = FakeStreamResponse()

    async def request(self, method, url, **kwargs):
        self.request_calls += 1
        return FakeJsonResponse()

    def build_request(self, method, url, **kwargs):
        return {"method": method, "url": url, "kwargs": kwargs}

    async def send(self, request, stream=False):
        self.send_calls += 1
        self.send_stream = stream
        return self.stream_response

    async def aclose(self):
        return None


class GatewayHttpTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.original_cache_path = main.CACHE_PATH
        self.original_api_key = main.GATEWAY_API_KEY
        main.CACHE_PATH = os.path.join(self.tmp.name, "cache.sqlite3")
        main.GATEWAY_API_KEY = "test-secret"

        self.context = TestClient(main.app)
        self.client = self.context.__enter__()
        self.original_http_client = main.app.state.client
        self.fake_http = FakeAsyncClient()
        main.app.state.client = self.fake_http
        self.headers = {"Authorization": "Bearer test-secret"}
        self.payload = {
            "model": "qwen3:1.7b",
            "messages": [{"role": "user", "content": "Reply exactly CACHE-CHECK-42"}],
            "temperature": 0,
        }

    def tearDown(self):
        main.app.state.client = self.original_http_client
        self.context.__exit__(None, None, None)
        main.CACHE_PATH = self.original_cache_path
        main.GATEWAY_API_KEY = self.original_api_key
        self.tmp.cleanup()

    def test_missing_key_is_401_and_does_not_call_upstream(self):
        response = self.client.post("/v1/chat/completions", json=self.payload)
        self.assertEqual(response.status_code, 401)
        self.assertEqual(self.fake_http.request_calls, 0)

    def test_successful_identical_requests_return_miss_then_hit(self):
        first = self.client.post(
            "/v1/chat/completions", json=self.payload, headers=self.headers
        )
        second = self.client.post(
            "/v1/chat/completions", json=self.payload, headers=self.headers
        )
        self.assertEqual(first.status_code, 200)
        self.assertEqual(first.headers.get("x-ai-cache"), "MISS")
        self.assertEqual(second.headers.get("x-ai-cache"), "HIT")
        self.assertEqual(second.json()["choices"][0]["message"]["content"], "cached answer")
        self.assertEqual(self.fake_http.request_calls, 1)

    def test_client_can_force_cache_bypass(self):
        response = self.client.post(
            "/v1/chat/completions",
            json=self.payload,
            headers={**self.headers, "X-AI-Cache-Bypass": "1"},
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers.get("x-ai-cache"), "BYPASS")
        self.assertEqual(self.fake_http.request_calls, 1)

    def test_stream_is_forwarded_as_stream_and_not_cached(self):
        response = self.client.post(
            "/v1/chat/completions",
            json={**self.payload, "stream": True},
            headers=self.headers,
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers.get("x-ai-cache"), "BYPASS")
        self.assertIn(b"data: [DONE]", response.content)
        self.assertEqual(self.fake_http.send_calls, 1)
        self.assertTrue(self.fake_http.send_stream)
        self.assertTrue(self.fake_http.stream_response.iterated)
        self.assertTrue(self.fake_http.stream_response.closed)
        self.assertEqual(self.fake_http.request_calls, 0)


if __name__ == "__main__":
    unittest.main()
