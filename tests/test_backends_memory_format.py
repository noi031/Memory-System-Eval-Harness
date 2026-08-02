from __future__ import annotations

import unittest

from backends.memory_format import format_memory_section


class _FakeItem:
    """Minimal stand-in for a SearchResult-like object with attribute access."""

    def __init__(self, uri, score, content) -> None:
        self.uri = uri
        self.score = score
        self.content = content


class FormatMemorySectionEmptyTests(unittest.TestCase):
    def test_empty_items_returns_empty_string(self) -> None:
        self.assertEqual("", format_memory_section([]))


class FormatMemorySectionObjectTests(unittest.TestCase):
    def test_single_object_item_full_format(self) -> None:
        result = format_memory_section([_FakeItem("echo://m/1", 0.9, "content one")])
        expected = (
            "### Retrieved memories:\n\n"
            "[1] (score: 0.90) uri: echo://m/1\n"
            "content one"
        )
        self.assertEqual(expected, result)

    def test_multiple_object_items_numbered_sequentially(self) -> None:
        items = [
            _FakeItem("u1", 0.9, "c1"),
            _FakeItem("u2", 0.8, "c2"),
            _FakeItem("u3", 0.7, "c3"),
        ]
        result = format_memory_section(items)
        expected = (
            "### Retrieved memories:\n\n"
            "[1] (score: 0.90) uri: u1\nc1"
            "\n\n"
            "[2] (score: 0.80) uri: u2\nc2"
            "\n\n"
            "[3] (score: 0.70) uri: u3\nc3"
        )
        self.assertEqual(expected, result)

    def test_header_prepended(self) -> None:
        result = format_memory_section([_FakeItem("u1", 0.9, "c1")])
        self.assertTrue(result.startswith("### Retrieved memories:\n\n"))

    def test_items_joined_with_double_newline(self) -> None:
        items = [_FakeItem("u1", 0.9, "c1"), _FakeItem("u2", 0.8, "c2")]
        result = format_memory_section(items)
        self.assertIn("c1\n\n[2]", result)


class FormatMemorySectionDictTests(unittest.TestCase):
    def test_single_dict_item_full_format(self) -> None:
        result = format_memory_section([{"uri": "u1", "score": 0.9, "content": "c1"}])
        expected = "### Retrieved memories:\n\n[1] (score: 0.90) uri: u1\nc1"
        self.assertEqual(expected, result)

    def test_multiple_dict_items(self) -> None:
        items = [
            {"uri": "u1", "score": 0.9, "content": "c1"},
            {"uri": "u2", "score": 0.8, "content": "c2"},
        ]
        result = format_memory_section(items)
        self.assertIn("[1] (score: 0.90) uri: u1\nc1", result)
        self.assertIn("[2] (score: 0.80) uri: u2\nc2", result)

    def test_dict_with_missing_keys_uses_defaults(self) -> None:
        result = format_memory_section([{"content": "c1"}])
        self.assertIn("[1] (score: 0.00) uri: \nc1", result)


class FormatMemorySectionMixedTests(unittest.TestCase):
    def test_mixed_object_and_dict_items(self) -> None:
        items = [
            _FakeItem("u1", 0.9, "c1"),
            {"uri": "u2", "score": 0.8, "content": "c2"},
        ]
        result = format_memory_section(items)
        self.assertIn("[1] (score: 0.90) uri: u1\nc1", result)
        self.assertIn("[2] (score: 0.80) uri: u2\nc2", result)


class FormatMemorySectionBudgetTests(unittest.TestCase):
    def test_default_budget_includes_all_items(self) -> None:
        items = [_FakeItem(f"u{i}", 0.1 * i, f"c{i}") for i in range(1, 6)]
        result = format_memory_section(items)
        for i in range(1, 6):
            self.assertIn(f"[{i}]", result)

    def test_large_budget_includes_all_items(self) -> None:
        items = [_FakeItem("u1", 0.9, "c1"), _FakeItem("u2", 0.8, "c2")]
        result = format_memory_section(items, budget_chars=100_000)
        self.assertIn("[1]", result)
        self.assertIn("[2]", result)

    def test_budget_limits_to_subset(self) -> None:
        items = [
            _FakeItem("u1", 0.9, "c1"),
            _FakeItem("u2", 0.8, "c2"),
            _FakeItem("u3", 0.7, "c3"),
        ]
        # Each block has identical length; budget for exactly two.
        block_len = len("[1] (score: 0.90) uri: u1\nc1")
        result = format_memory_section(items, budget_chars=block_len * 2)
        self.assertIn("[1]", result)
        self.assertIn("[2]", result)
        self.assertNotIn("[3]", result)
        self.assertNotIn("c3", result)

    def test_exceeding_item_dropped_not_truncated(self) -> None:
        items = [
            _FakeItem("u1", 0.9, "c1"),
            _FakeItem("u2", 0.8, "longer content that should not appear at all"),
        ]
        block_len = len("[1] (score: 0.90) uri: u1\nc1")
        result = format_memory_section(items, budget_chars=block_len)
        self.assertIn("c1", result)
        # The exceeding item must be fully absent, not truncated.
        self.assertNotIn("longer content", result)
        self.assertNotIn("[2]", result)

    def test_all_items_exceed_budget_returns_header_only(self) -> None:
        items = [_FakeItem("u1", 0.9, "c1")]
        result = format_memory_section(items, budget_chars=1)
        self.assertEqual("### Retrieved memories:\n\n", result)


class FormatMemorySectionEdgeCaseTests(unittest.TestCase):
    def test_item_with_empty_fields_handled_gracefully(self) -> None:
        result = format_memory_section([_FakeItem("", 0.0, "")])
        self.assertIn("[1] (score: 0.00) uri: \n", result)

    def test_item_with_none_fields_handled_gracefully(self) -> None:
        result = format_memory_section([_FakeItem(None, None, None)])  # type: ignore[arg-type]
        self.assertIn("[1] (score: 0.00) uri: \n", result)

    def test_score_always_two_decimal_places(self) -> None:
        for score, expected in [
            (0.9, "0.90"),
            (1.0, "1.00"),
            (0.0, "0.00"),
            (0.856, "0.86"),
        ]:
            with self.subTest(score=score):
                result = format_memory_section([_FakeItem("u", score, "c")])
                self.assertIn(f"(score: {expected})", result)


if __name__ == "__main__":
    unittest.main()
