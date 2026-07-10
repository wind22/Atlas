"""制度状态机：从确认制度的时间线派生「当前制度处于第几天、上次何时切换」。

现有 regime 模块只回答「今天是什么制度、较昨日是否变化」。本模块补上**持续时间与
上次切换**这层状态，输出（进 ``latest.json.state``）：

  * ``current_regime``        今日确认制度。
  * ``days_in_regime``        当前制度已连续确认的天数（含今日；受可见历史窗口限制，
                              窗口内全同制度时为下界）。
  * ``previous_regime``       切换前的制度（可见历史内未发生切换则 None）。
  * ``last_transition_date``  切换到当前制度的日期（未知则 None）。
  * ``transition_reason``     那次切换的原因（复用当日 RegimeState.reason）。

**纪律（铁律 Ⅱ）**：纯描述已发生的状态——已持续多久、上次何时因何切换。**绝不**
输出「还会持续多久」「接下来会切到哪」之类的预测。纯派生：只消费 DailyReport 序列。
"""
from __future__ import annotations

from ..types import DailyReport


def build_state(
    report: DailyReport, recent_reports: list[DailyReport] | None
) -> dict:
    """从今日报告 + 近期快照（date 降序）派生制度持续状态。

    ``recent_reports`` 为 ``snapshot.load_recent`` 的结果：严格早于今日、最近在前。
    """
    current = report.market_regime.regime

    # 时间线：今日在前，其后接近期快照（本就 date 降序）→ 整体新→旧。
    timeline = [report] + list(recent_reports or [])

    streak = 0
    transition_report = report        # 当前连续段里最旧的一天（= 切换发生的那天）
    previous = None
    for rep in timeline:
        if rep.market_regime.regime == current:
            streak += 1
            transition_report = rep
        else:
            previous = rep.market_regime.regime   # 段外第一天 = 切换前的制度
            break

    if previous is not None:
        last_transition_date: str | None = transition_report.date
        transition_reason: str | None = transition_report.market_regime.reason
    else:
        # 可见历史窗口内始终同一制度 → 切换发生在窗口之前，诚实地标为未知。
        last_transition_date = None
        transition_reason = None

    return {
        "current_regime": current.value,
        "days_in_regime": streak,
        "previous_regime": previous.value if previous is not None else None,
        "last_transition_date": last_transition_date,
        "transition_reason": transition_reason,
    }
