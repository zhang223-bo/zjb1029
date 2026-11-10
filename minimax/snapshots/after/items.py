"""阅读列表查询。

CODE_VERSION 用来在测试日志里标出改前还是改后。
"""

CODE_VERSION = "items-after-1"


def list_items(items, status=None):
    """按 updated_at 从新到旧返回条目。status 有值时只保留该状态。"""
    ordered = sorted(items, key=lambda item: item["updated_at"], reverse=True)
    if status is None:
        return ordered
    return [item for item in ordered if item["status"] == status]
