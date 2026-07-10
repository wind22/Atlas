"""静态展示层 (Static-first Atlas v3, 方案 §5).

把 :class:`~atlas.types.DailyReport` / 视图模型渲染成自包含的静态页面。所有展示
逻辑（配色、格式化、排版）都在此层；上游只产出数据，本层只负责「怎么显示」。

  * :mod:`atlas.site.dashboard`  —— 每日看板（一页自包含 HTML）+ 视图模型。
  * :mod:`atlas.site.detail`     —— 每只自选股一页历史详情。
  * :mod:`atlas.site.about`      —— 算法原理页（数据无关）。
"""
