"""Atlas — 趋势风险与机会监控系统.

Trend-based Risk & Opportunity Monitor. See architecture.md for the full
specification; §3 (scoring) and §4 (regime) are the executable spec.

Governing first principles (三条铁律):
  Ⅰ 生存优先  — the risk score R is a brake; defense outranks offense.
  Ⅱ 不预测只响应 — only measure trends that have already happened.
  Ⅲ 不对称下注  — cut losses short, let winners run.
"""

__version__ = "1.0.0"
