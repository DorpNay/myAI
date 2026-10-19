import unittest

from main import canonical_key, is_cacheable


class CachePolicyTests(unittest.TestCase):
    def test_identical_payload_has_stable_key(self):
        payload = {"model": "qwen3:4b", "messages": [{"role": "user", "content": "What is cache?"}]}
        self.assertEqual(canonical_key(payload), canonical_key(dict(reversed(list(payload.items())))))

    def test_model_and_messages_are_part_of_key(self):
        first = {"model": "qwen3:4b", "messages": [{"role": "user", "content": "hello"}]}
        second = {"model": "qwen3:1.7b", "messages": [{"role": "user", "content": "hello"}]}
        self.assertNotEqual(canonical_key(first), canonical_key(second))

    def test_basic_non_streaming_chat_is_cacheable(self):
        self.assertTrue(is_cacheable({"messages": [{"role": "user", "content": "hello"}], "stream": False}))

    def test_dynamic_requests_bypass_cache(self):
        cases = [
            {"messages": [{"role": "user", "content": "hello"}], "stream": True},
            {"messages": [{"role": "user", "content": "hello"}], "temperature": 0.8},
            {"messages": [{"role": "user", "content": "hello"}], "tools": [{"type": "function"}]},
            {"messages": [{"role": "tool", "content": "result"}]},
            {"messages": []},
        ]
        for payload in cases:
            with self.subTest(payload=payload):
                self.assertFalse(is_cacheable(payload))


if __name__ == "__main__":
    unittest.main()
