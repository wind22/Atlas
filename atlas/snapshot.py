"""向后兼容垫片：持久化实现已迁到 :mod:`atlas.storage.snapshot_store`（方案 §5）。

保留本模块，使既有导入 ``from atlas import snapshot`` 继续可用；新代码请直接从
:mod:`atlas.storage.snapshot_store` 导入。
"""
from __future__ import annotations

from .storage.snapshot_store import (
    load_previous,
    load_recent,
    load_report,
    save_report,
)

__all__ = ["save_report", "load_report", "load_previous", "load_recent"]
