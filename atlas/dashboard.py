"""向后兼容垫片：看板渲染已迁到 :mod:`atlas.site.dashboard`（方案 §5）。

保留本模块，使既有导入 ``from atlas import dashboard`` / ``from atlas.dashboard
import ...`` 继续可用；新代码请直接从 :mod:`atlas.site.dashboard` 导入。
"""
from __future__ import annotations

from .site.dashboard import (
    build_view_model,
    render_dashboard,
    render_view,
    write_dashboard,
)

__all__ = ["build_view_model", "render_view", "render_dashboard", "write_dashboard"]
