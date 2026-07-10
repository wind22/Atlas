"""历史相似状态：找出「当时状态和今天最像」的历史日子。

用状态向量 (SPY 的 T、R、市场广度、VIX) 的距离，在可见历史里检索最接近今天的
若干天，作为**回看锚点**——让人自己去看当时前后发生了什么。

⚠️ 铁律 Ⅱ「不预测，只响应」红线（本模块存在的全部意义就在于守住它）：
  * 输出**只描述历史日子当时的状态**（日期、制度、T/R/广度/VIX、相似度距离）。
  * **绝不**附带任何前向信息——不给「之后 30 天涨跌了多少」「下次大概率如何」
    之类的字段。带上未来收益，就把描述性锚点变成了变相预测，违反铁律。
  * 距离度量用**固定尺度**（非拟合历史），符合反过拟合原则。

纯派生：只消费 DailyReport 序列，不读数据源、不新增指标。
"""
from __future__ import annotations

import math

from .. import config
from ..types import DailyReport

# 各特征的固定归一尺度（把 T/R(0–100)、广度(0–1)、VIX(~10–50) 拉到可比区间）。
# 这些是结构性选择，不对历史优化。
_SCALE: dict[str, float] = {"T": 100.0, "R": 100.0, "breadth": 1.0, "vix": 50.0}


def _features(report: DailyReport) -> dict | None:
    """今日状态向量：SPY 的 T/R + 广度 + VIX。缺 SPY 评分则无法比较，返回 None。"""
    spy = report.results.get(config.BENCHMARK)
    if spy is None:
        return None
    return {
        "T": float(spy.T),
        "R": float(spy.R),
        "breadth": float(report.breadth_pct),
        "vix": None if report.vix is None else float(report.vix),
    }


def _distance(a: dict, b: dict) -> float:
    """两状态向量的归一化欧氏距离；某维任一侧缺失则跳过该维。"""
    ss = 0.0
    for dim, scale in _SCALE.items():
        va, vb = a[dim], b[dim]
        if va is None or vb is None:
            continue
        ss += ((va - vb) / scale) ** 2
    return math.sqrt(ss)


def build_similar(
    report: DailyReport,
    recent_reports: list[DailyReport] | None,
    *,
    top_n: int = config.SIMILARITY_TOP_N,
    min_gap: int = config.SIMILARITY_MIN_GAP_DAYS,
) -> dict:
    """检索状态最接近今天的历史日子（升序距离）。

    ``recent_reports`` 为 ``snapshot.load_recent`` 结果（date 降序）。跳过最近
    ``min_gap`` 个交易日，使匹配是真正的历史片段而非紧邻的今天。历史不足时优雅
    降级为空列表。
    """
    today = _features(report)
    if today is None:
        return {"similar_periods": []}

    candidates = list(recent_reports or [])[min_gap:]   # 排除最近 ~1 个月
    scored: list[tuple[float, DailyReport, dict]] = []
    for rep in candidates:
        f = _features(rep)
        if f is None:
            continue
        scored.append((_distance(today, f), rep, f))
    scored.sort(key=lambda x: x[0])

    periods = [{
        "date": rep.date,
        "regime": rep.market_regime.regime.value,   # 当时的制度（描述，非预测）
        "T_spy": round(f["T"], 1),
        "R_spy": round(f["R"], 1),
        "breadth_pct": round(f["breadth"], 3),
        "vix": None if f["vix"] is None else round(f["vix"], 1),
        "distance": round(dist, 3),                  # 越小越像
    } for dist, rep, f in scored[:top_n]]

    return {"similar_periods": periods}
