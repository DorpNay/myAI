import unittest

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
        self.assertNotEqual(
            original, canonical_key(self.payload, path="responses")
        )
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
            self.payload,
            method="POST",
            path="chat/completions",
            headers={},
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
                    payload,
                    method="POST",
                    path="chat/completions",
                    headers={},
                ))

    def test_missing_or_invalid_payload_bypasses_cache(self):
        for payload in (None, {}, {"messages": "not-a-list"}):
            self.assertTrue(should_bypass_cache(
                payload, method="POST", path="chat/completions", headers={}
            ))


if __name__ == "__main__":
    unittest.main()
