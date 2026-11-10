"""阅读列表查询。

CODE_VERSION 用来在测试日志里标出改前还是改后。
"""

CODE_VERSION = "items-before-1"


def list_items(items, status=None):
    """按 updated_at 从新到旧返回条目。

    status 已出现在参数里，但还不过滤。功能测试会因此失败。
    """
    return sorted(items, key=lambda item: item["updated_at"], reverse=True)
