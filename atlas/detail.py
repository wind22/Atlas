"""向后兼容垫片：个股详情页已迁到 :mod:`atlas.site.detail`（方案 §5）。

保留本模块，使既有导入 ``from atlas import detail`` / ``from atlas.detail import
...`` 继续可用；新代码请直接从 :mod:`atlas.site.detail` 导入。
"""
from __future__ import annotations

from .site.detail import render_detail_page, render_detail_pages, safe_name

__all__ = ["safe_name", "render_detail_page", "render_detail_pages"]
