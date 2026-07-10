"""向后兼容垫片：算法原理页已迁到 :mod:`atlas.site.about`（方案 §5）。

保留本模块，使既有导入 ``from atlas import about`` / ``from atlas.about import
...`` 继续可用；新代码请直接从 :mod:`atlas.site.about` 导入。
"""
from __future__ import annotations

from .site.about import render_about_page, write_about_page

__all__ = ["render_about_page", "write_about_page"]
