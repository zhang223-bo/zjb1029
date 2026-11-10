# 阅读列表

## 排序

GET /items 不带查询参数时，返回全部条目，按 updated_at 从新到旧排序。

## 筛选

GET /items 接受可选查询参数 status。
允许的取值是 read 和 unread。
传入 status 时只返回该状态的条目，顺序仍按 updated_at 从新到旧。
不传 status 时行为与「排序」相同。
