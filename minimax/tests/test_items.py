import unittest

from items import list_items


class ListItemsTest(unittest.TestCase):
    def test_filter_by_status(self):
        items = [
            {"id": 1, "status": "unread", "updated_at": "2026-11-01"},
            {"id": 2, "status": "read", "updated_at": "2026-11-03"},
            {"id": 3, "status": "unread", "updated_at": "2026-11-02"},
        ]
        result = list_items(items, status="unread")
        self.assertEqual([item["id"] for item in result], [3, 1])

    def test_list_sorted_by_time(self):
        items = [
            {"id": 1, "status": "unread", "updated_at": "2026-11-01"},
            {"id": 2, "status": "read", "updated_at": "2026-11-03"},
        ]
        result = list_items(items)
        self.assertEqual([item["id"] for item in result], [2, 1])


if __name__ == "__main__":
    unittest.main()
