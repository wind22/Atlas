"""流水线编排层 (Static-first Atlas v3, 方案 §5).

把各层（data / core / report / storage / site）串成一次可运行的作业。业务编排放在
这里，CLI 入口留在 :mod:`atlas.runner`，两者分离。

  * :mod:`atlas.pipelines.daily`  —— 每日流水线：拉数 → 指标 → 评分 → 制度 + 提示
    → 报告层 → 数据产物 → 静态页面。
"""
