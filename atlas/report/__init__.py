"""报告层 (Static-first Atlas v3, 方案 §5/§6).

把信号核心（indicators/scoring/regime/alerts）已经算好的东西，组织成**面向人的
每日报告**——解释、状态、变化。本层**不新增任何指标、不做任何预测**（铁律 Ⅱ）：
只把「已经发生」的事实换成人话，且每条结论都可溯源到某条规则或某条 alert。

  * :mod:`atlas.report.explain`  —— 今日结论 / 风险 / 机会 / 较昨日变化。
"""
