"""持久化 / 数据产物层 (Static-first Atlas v3, 见方案 §2).

本层负责把一天的 :class:`~atlas.types.DailyReport` 落成两类东西：

  * 内部状态         —— SQLite 快照（当前仍由 :mod:`atlas.snapshot` 负责）。
  * 公开数据契约     —— ``public/data/*.json``（本包的 :mod:`atlas.storage.artifacts`）。

设计原则：**上游产出稳定数据契约，下游（静态页面）只消费契约**。HTML 不再是
系统核心，``public/data/latest.json`` 才是。契约的稳定性规则见 ``schema.json``：
``schema_version`` 只增不改，字段只增不删不改类型。
"""
