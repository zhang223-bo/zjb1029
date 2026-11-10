import unittest

from minimax_sdd.context import TODO_TEXT, render_context
from minimax_sdd.seekdb_store import tokenize
from minimax_sdd.state import PASS_TEXT


def rows(spec_body: str):
    return [
        {
            "doc_key": "spec:items",
            "kind": "spec",
            "source_path": "specs/items.md",
            "symbol": "筛选",
            "title": "阅读列表规格",
            "body": spec_body,
        },
        {
            "doc_key": "code:items.list",
            "kind": "code",
            "source_path": "src/items.py",
            "symbol": "items.list",
            "title": "阅读列表实现",
            "body": "def list_items(items, status=None):\n    return items\n",
        },
        {
            "doc_key": "test:items",
            "kind": "test",
            "source_path": "tests/test_items.py",
            "symbol": "test_filter_by_status",
            "title": "阅读列表测试",
            "body": "def test_filter_by_status(self):\n    pass\n\ndef test_list_sorted_by_time(self):\n    pass\n",
        },
    ]


class ContextRuleTest(unittest.TestCase):
    def test_task_tokens_keep_status_and_chinese_phrase(self):
        tokens = tokenize("为阅读列表增加 status 筛选")
        self.assertIn("status", tokens)
        self.assertIn("筛选", tokens)

    def test_context_quotes_the_spec_returned_by_the_query(self):
        spec = "允许的取值是 read 和 unread。\n"
        text = render_context("为阅读列表增加 status 筛选", rows(spec), [], "SELECT 1")
        self.assertIn("specs/items.md", text)
        self.assertIn("items.list", text)
        self.assertIn("test_filter_by_status", text)
        self.assertIn("test_list_sorted_by_time", text)
        self.assertIn("tests/test_items.py", text)
        self.assertIn("read 和 unread", text)
        self.assertNotIn("archived", text)
        self.assertIn(TODO_TEXT, text)

    def test_reimported_spec_replaces_the_quoted_text(self):
        spec = "允许的取值是 read、unread 和 archived。\n"
        text = render_context("为阅读列表增加 status 筛选", rows(spec), [], "SELECT 1")
        self.assertIn("archived", text)

    def test_saved_verification_removes_the_todo(self):
        text = render_context(
            "为阅读列表增加 status 筛选",
            rows("允许的取值是 read 和 unread。\n"),
            [PASS_TEXT],
            "SELECT 1",
        )
        self.assertIn(PASS_TEXT, text)
        self.assertNotIn(f"- {TODO_TEXT}", text)
        self.assertIn("待办：\n- （无）", text)


if __name__ == "__main__":
    unittest.main()
