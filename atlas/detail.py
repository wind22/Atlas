"""每只自选股的历史详情页（每只单独一页）。

复用 backtest.regime_timeline 从历史价格重算「制度时间线」，渲染成自包含 HTML：
价格 + MA50/MA200 趋势线、按当时制度着色的背景带、关键节点标记（跌破/收复 200 线、
金叉/死叉、创 52 周新高），以及一个带日期的事件清单。看板上的标的名链接到这里。

节点/制度均由**同一套固定规则**从历史重算，与系统其它部分一致（非每日快照）。
"""
from __future__ import annotations

import json
import os

import pandas as pd

from . import config
from .backtest import regime_timeline
from .types import REGIME_LABEL, REGIME_LIGHT, Regime, TickerResult

# 制度背景色（浅，半透明，明暗皆可读）
_REGIME_FILL: dict[str, str] = {
    Regime.RISK_ON.value: "rgba(46,150,88,.11)",
    Regime.CAUTION.value: "rgba(201,138,18,.13)",
    Regime.RISK_OFF.value: "rgba(207,59,59,.13)",
    Regime.OVERSOLD.value: "rgba(214,140,40,.13)",
}
_GOOD, _BAD, _WARN = "#2e9658", "#cf3b3b", "#c98a12"


def safe_name(ticker: str) -> str:
    """把 ticker 变成安全的文件名（1810.HK -> 1810_HK）。"""
    return "".join(c if c.isalnum() else "_" for c in ticker)


# --------------------------------------------------------------------------
# 事件抽取
# --------------------------------------------------------------------------
def _collect_events(tl: pd.DataFrame) -> list[dict]:
    """从时间线抽取关键事件，返回按时间倒序的列表。"""
    ev: list[dict] = []
    regimes = list(tl["regime"])
    idx = tl.index
    prev_nh = False
    for i in range(len(tl)):
        d = str(idx[i].date())
        px = float(tl["close"].iloc[i])
        row = tl.iloc[i]
        if bool(row["broke_ma200"]):
            ev.append({"date": d, "label": "跌破 200 日均线", "cls": "bad", "px": px})
        if bool(row["reclaimed_ma200"]):
            ev.append({"date": d, "label": "收复 200 日均线", "cls": "good", "px": px})
        if bool(row["death_cross"]):
            ev.append({"date": d, "label": "死叉（50 日下穿 200 日）", "cls": "bad", "px": px})
        if bool(row["golden_cross"]):
            ev.append({"date": d, "label": "金叉（50 日上穿 200 日）", "cls": "good", "px": px})
        nh = bool(row["new_high"])
        if nh and not prev_nh:
            ev.append({"date": d, "label": "创 52 周新高", "cls": "warn", "px": px})
        prev_nh = nh
        if i > 0 and regimes[i] != regimes[i - 1]:
            r = Regime(regimes[i])
            ev.append({"date": d, "label": f"制度转为 {REGIME_LIGHT[r]} {REGIME_LABEL[r]}",
                       "cls": "reg", "px": px})
    ev.reverse()
    return ev


# --------------------------------------------------------------------------
# SVG 图
# --------------------------------------------------------------------------
def _svg(tl: pd.DataFrame, name: str, ticker: str, chart_id: str = "ch") -> str:
    W, H, pad = 900, 320, 12
    n = len(tl)
    step = max(1, n // 480)   # 控制点数；典型 <480 时 step=1，不丢事件
    d = tl.iloc[::step]
    m = len(d)
    if m < 2:
        return ""
    xs = [pad + (W - 2 * pad) * i / (m - 1) for i in range(m)]
    close = list(d["close"])
    ma50 = list(d["ma50"])
    ma200 = list(d["ma200"])
    vals = [v for v in close + ma50 + ma200 if v == v and v > 0]
    lo, hi = min(vals), max(vals)
    span = (hi - lo) or 1.0

    def y(v: float) -> float:
        return pad + (H - 2 * pad) * (1 - (v - lo) / span)

    # 制度背景带
    regs = list(d["regime"])
    bands = []
    i = 0
    while i < m:
        j = i
        while j + 1 < m and regs[j + 1] == regs[i]:
            j += 1
        fill = _REGIME_FILL.get(regs[i], "transparent")
        x0, x1 = xs[i], xs[min(j + 1, m - 1)]
        bands.append(f'<rect x="{x0:.1f}" y="{pad}" width="{max(1, x1 - x0):.1f}" '
                     f'height="{H - 2 * pad}" fill="{fill}"/>')
        i = j + 1

    def poly(series, stroke, w, dash=""):
        pts = " ".join(f"{xs[k]:.1f},{y(series[k]):.1f}" for k in range(m) if series[k] == series[k])
        da = f' stroke-dasharray="{dash}"' if dash else ""
        return f'<polyline points="{pts}" fill="none" stroke="{stroke}" stroke-width="{w}"{da}/>'

    # 事件标记（圆点）+ 每点事件标签（供交互提示）
    marks = []
    ev_labels: list[str] = []
    prev_nh = False
    for k in range(m):
        row = d.iloc[k]
        ev = None
        if bool(row["broke_ma200"]):
            ev = (_BAD, "跌破200日线")
        elif bool(row["death_cross"]):
            ev = (_BAD, "死叉")
        elif bool(row["reclaimed_ma200"]):
            ev = (_GOOD, "收复200日线")
        elif bool(row["golden_cross"]):
            ev = (_GOOD, "金叉")
        nh = bool(row["new_high"])
        if ev is None and nh and not prev_nh:
            ev = (_WARN, "创52周新高")
        prev_nh = nh
        ev_labels.append(ev[1] if ev else "")
        if ev:
            marks.append(f'<circle cx="{xs[k]:.1f}" cy="{y(close[k]):.1f}" r="3.6" '
                         f'fill="{ev[0]}" stroke="#fff" stroke-width="0.8"><title>{ev[1]}</title></circle>')

    yr0, yr1 = d.index[0].date(), d.index[-1].date()

    # 交互所需的紧凑数据（供内嵌 JS 做十字光标 + 悬浮提示）
    data = {
        "t": [str(dt.date()) for dt in d.index],
        "c": [round(float(v), 2) for v in close],
        "a": [round(float(v), 2) for v in ma50],
        "b": [round(float(v), 2) for v in ma200],
        "g": list(regs),
        "e": ev_labels,
        "geom": {"W": W, "H": H, "pad": pad, "lo": round(lo, 4), "span": round(span, 4)},
    }
    payload = json.dumps(data, ensure_ascii=False, separators=(",", ":"))

    return f'''<div class="chart" id="{chart_id}">
<svg viewBox="0 0 {W} {H + 8}" width="100%" preserveAspectRatio="xMidYMid meet" role="img" aria-label="{name} {ticker} 历史趋势">
  {''.join(bands)}
  {poly(ma200, _WARN, 1.1)}
  {poly(ma50, "#8a94a3", 1.0, "3 3")}
  {poly(close, "#1f6feb", 1.7)}
  {''.join(marks)}
  <g class="cursor" style="display:none">
    <line class="cvl" y1="{pad}" y2="{H - pad}" stroke="currentColor" stroke-opacity=".4" stroke-width="1"/>
    <circle class="cdot" r="4" fill="#1f6feb" stroke="#fff" stroke-width="1.2"/>
  </g>
  <rect class="hit" x="{pad}" y="{pad}" width="{W - 2 * pad}" height="{H - 2 * pad}" fill="transparent" style="cursor:crosshair"/>
  <text x="{pad}" y="{H + 4}" font-size="11" fill="currentColor">{yr0} → {yr1}　·　<tspan fill="#1f6feb">价格</tspan> <tspan fill="{_WARN}">MA200</tspan> <tspan fill="#8a94a3">MA50</tspan>　·　背景色=当时制度</text>
</svg>
<div class="tip" hidden></div>
<script>(function(){{
  var D={payload}, G=D.geom, m=D.t.length, plotW=G.W-2*G.pad;
  var RM={{risk_on:"🟢 进攻",caution:"🟡 警戒",risk_off:"🔴 防御",oversold:"🟠 超卖观察"}};
  var box=document.getElementById("{chart_id}"), svg=box.querySelector("svg");
  var hit=box.querySelector(".hit"), cur=box.querySelector(".cursor");
  var vl=box.querySelector(".cvl"), dot=box.querySelector(".cdot"), tip=box.querySelector(".tip");
  function xOf(i){{return G.pad+plotW*i/(m-1);}}
  function yOf(v){{return G.pad+(G.H-2*G.pad)*(1-(v-G.lo)/G.span);}}
  function move(ev){{
    var r=svg.getBoundingClientRect();
    var i=Math.round(((ev.clientX-r.left)/r.width*G.W-G.pad)/plotW*(m-1));
    if(i<0)i=0; if(i>m-1)i=m-1;
    var x=xOf(i), yv=yOf(D.c[i]);
    vl.setAttribute("x1",x); vl.setAttribute("x2",x);
    dot.setAttribute("cx",x); dot.setAttribute("cy",yv); cur.style.display="";
    var h="<b>"+D.t[i]+"</b><br>价 "+D.c[i].toFixed(2)+"　MA50 "+D.a[i].toFixed(2)+"　MA200 "+D.b[i].toFixed(2)+"<br>"+(RM[D.g[i]]||D.g[i]);
    if(D.e[i])h+="<br>★ "+D.e[i];
    tip.innerHTML=h; tip.hidden=false;
    var br=box.getBoundingClientRect(), tx=ev.clientX-br.left+14, ty=ev.clientY-br.top+14;
    if(tx>br.width-170)tx-=190; tip.style.left=tx+"px"; tip.style.top=ty+"px";
  }}
  function hide(){{cur.style.display="none"; tip.hidden=true;}}
  hit.addEventListener("pointermove",move);
  hit.addEventListener("pointerdown",move);
  hit.addEventListener("pointerleave",hide);
}})();</script>
</div>'''


# --------------------------------------------------------------------------
# 页面
# --------------------------------------------------------------------------
def _event_rows(events: list[dict]) -> str:
    if not events:
        return '<tr><td colspan="3" class="muted">近期无关键节点。</td></tr>'
    cls_map = {"good": "good", "bad": "bad", "warn": "warn", "reg": ""}
    out = []
    for e in events[:20]:
        out.append(f'<tr><td class="muted">{e["date"]}</td>'
                   f'<td class="{cls_map.get(e["cls"], "")}">{e["label"]}</td>'
                   f'<td class="num">{e["px"]:.2f}</td></tr>')
    return "".join(out)


def render_detail_page(
    ticker: str, name: str, tl: pd.DataFrame,
    current: TickerResult | None, *, source: str, generated_at: str,
) -> str:
    last = tl.iloc[-1]
    reg = Regime(str(last["regime"]))
    price = float(last["close"])
    chg = (price / float(tl["close"].iloc[-2]) - 1.0) * 100 if len(tl) >= 2 else 0.0
    t_val = float(current.T) if current else float(last["T"])
    r_val = float(current.R) if current else float(last["R"])
    events = _collect_events(tl)

    return f'''<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>{name} {ticker} · Atlas 详情</title>
<meta name="theme-color" content="#0f131a">
<style>
  :root{{--bg:#f4f6f9;--panel:#fff;--ink:#1a1f28;--muted:#5c6673;--line:#e2e7ee;--good:#2e9658;--bad:#cf3b3b;--warn:#c98a12}}
  @media (prefers-color-scheme:dark){{:root{{--bg:#0f131a;--panel:#171d27;--ink:#e7ecf3;--muted:#9aa5b3;--line:#262e3a}}}}
  *{{box-sizing:border-box}} body{{margin:0}}
  .wrap{{max-width:940px;margin:0 auto;padding:18px 14px 60px;background:var(--bg);color:var(--ink);
    font:15px/1.6 -apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC","Microsoft YaHei",sans-serif;min-height:100vh}}
  a.back{{color:var(--muted);text-decoration:none;font-size:14px}} a.back:hover{{color:var(--ink)}}
  h1{{font-size:22px;margin:10px 0 2px}} .tk{{color:var(--muted);font-size:14px;font-weight:500}}
  .head{{display:flex;flex-wrap:wrap;gap:18px;align-items:baseline;margin:6px 0 14px}}
  .px{{font-size:26px;font-weight:700;font-variant-numeric:tabular-nums}}
  .tag{{display:inline-block;padding:2px 10px;border-radius:999px;font-size:13px;font-weight:600}}
  .r-on{{background:rgba(46,150,88,.16);color:var(--good)}} .r-cau{{background:rgba(201,138,18,.18);color:var(--warn)}}
  .r-off{{background:rgba(207,59,59,.16);color:var(--bad)}} .r-os{{background:rgba(214,140,40,.18);color:var(--warn)}}
  .scores{{color:var(--muted);font-size:14px}}
  .panel{{background:var(--panel);border:1px solid var(--line);border-radius:14px;padding:14px;margin-bottom:18px}}
  .chart{{position:relative}}
  .tip{{position:absolute;pointer-events:none;z-index:5;background:var(--panel);border:1px solid var(--line);
    border-radius:8px;padding:6px 9px;font-size:12px;line-height:1.5;white-space:nowrap;
    box-shadow:0 2px 8px rgba(0,0,0,.18)}}
  .tip[hidden]{{display:none}}
  svg{{background:var(--bg);border-radius:10px;color:var(--ink);touch-action:none}}
  table{{width:100%;border-collapse:collapse;font-size:14px}}
  th,td{{padding:7px 9px;border-bottom:1px solid var(--line);text-align:left}}
  th{{font-size:12px;color:var(--muted)}} td.num{{text-align:right;font-variant-numeric:tabular-nums}}
  .good{{color:var(--good)}} .bad{{color:var(--bad)}} .warn{{color:var(--warn)}} .muted{{color:var(--muted)}}
  .pos{{color:var(--good)}} .neg{{color:var(--bad)}}
  footer{{color:var(--muted);font-size:12px;margin-top:20px;line-height:1.7}}
</style></head><body><div class="wrap">
  <a class="back" href="../index.html">← 返回看板</a>
  <h1>{name} <span class="tk">{ticker}</span></h1>
  <div class="head">
    <div class="px">{price:,.2f} <span class="{'pos' if chg >= 0 else 'neg'}" style="font-size:15px">{chg:+.2f}%</span></div>
    <div><span class="tag {_regime_cls(reg)}">{REGIME_LIGHT[reg]} {REGIME_LABEL[reg]}</span></div>
    <div class="scores">趋势分 T <b>{t_val:.0f}</b>　·　风险分 R <b>{r_val:.0f}</b></div>
  </div>

  <div class="panel">{_svg(tl, name, ticker)}</div>

  <h3 style="font-size:15px;margin:0 0 6px">关键节点 / 趋势状态（近期在前）</h3>
  <div class="panel" style="padding:4px 14px">
    <table><thead><tr><th>日期</th><th>事件</th><th>价格</th></tr></thead><tbody>{_event_rows(events)}</tbody></table>
  </div>

  <footer>
    数据来源 {source}｜历史节点由固定规则从价格重算，与系统一致｜报告生成于 {generated_at}。<br>
    仅供研究，不构成投资建议。
  </footer>
</div></body></html>'''


def _regime_cls(reg: Regime) -> str:
    return {Regime.RISK_ON: "r-on", Regime.CAUTION: "r-cau",
            Regime.RISK_OFF: "r-off", Regime.OVERSOLD: "r-os"}[reg]


def render_detail_pages(
    frames: dict, bench, vix, stocks: dict[str, str], site_dir: str,
    results: dict[str, TickerResult], *, source: str, generated_at: str,
) -> dict[str, str]:
    """为每只自选股生成详情页，返回 {ticker: 相对链接}。取数/计算失败的自动跳过。"""
    links: dict[str, str] = {}
    tdir = os.path.join(site_dir, "t")
    os.makedirs(tdir, exist_ok=True)
    for ticker, name in stocks.items():
        df = frames.get(ticker)
        if df is None or len(df) < config.MA_LONG + config.MA_SLOPE_LOOKBACK + 5:
            continue
        try:
            tl = regime_timeline(ticker, df, bench, vix)
        except Exception:  # noqa: BLE001 — 一只失败不影响其它
            continue
        if tl.empty:
            continue
        html = render_detail_page(ticker, name, tl, results.get(ticker),
                                  source=source, generated_at=generated_at)
        fn = f"{safe_name(ticker)}.html"
        with open(os.path.join(tdir, fn), "w", encoding="utf-8") as fh:
            fh.write(html)
        links[ticker] = f"t/{fn}"
    return links
