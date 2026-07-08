"""制度回测 — 验证 SURVIVAL（避开大跌），而非收益（architecture.md §9）.

铁律 Ⅰ（生存优先）的直接检验：回放历史，在每根 K 线上用**固定参数**重算 T/R
与制度，喂入真实 VIX，应用 N 日确认，得到一条「随制度调整敞口」的资金曲线，
再对比它与买入持有——看系统能否在历次大跌中及时把敞口降下来、少受重伤。

要点：
  * 多标的（SPY、QQQ）。QQQ 高 β、回撤更深，是更严的生存考验。
  * 真实 VIX 逐日喂入风险分 R（2000/2008/2020 的恐慌飙升靠它捕捉）。
  * 广度维度在回测中置为中性（缺行业 ETF 历史成分）——制度主要由价格 / 均线 /
    回撤 / 波动 / VIX 驱动，正是生存验证关注的部分。见报告方法学注记。
  * 用尾窗（~320 根）重算指标而非全量切片：Wilder/EMA 早已收敛，结果等价，速度快。

离线（合成数据）可跑通全部逻辑用于自测；真实历史需联网（GitHub Actions）。
"""
from __future__ import annotations

import argparse
import json
import os

import pandas as pd

from . import config, data_fetch, indicators, regime, scoring
from .types import Regime

# 敞口映射：每种确认制度下持有的仓位（次日生效，无前视）。
_EXPOSURE: dict[Regime, float] = {
    Regime.RISK_ON: 1.0,
    Regime.CAUTION: 0.5,
    Regime.OVERSOLD: 0.5,
    Regime.RISK_OFF: 0.0,
}

# 需要有足够尾窗让所有回看（MA200、12-1 动量的 253 根、1 年波动 272 根）与
# Wilder/EMA 收敛都成立。
_WINDOW = 320

# 历史大跌（用于「是否及时转防御」的逐次检验）。日期为标普基准的峰/谷附近。
CRISES: list[dict] = [
    {"key": "2000", "name": "2000 互联网泡沫", "peak": "2000-03-24", "trough": "2002-10-09"},
    {"key": "2008", "name": "2008 金融危机", "peak": "2007-10-09", "trough": "2009-03-09"},
    {"key": "2018Q4", "name": "2018Q4 急跌", "peak": "2018-09-20", "trough": "2018-12-24"},
    {"key": "2020", "name": "2020 疫情崩盘", "peak": "2020-02-19", "trough": "2020-03-23"},
    {"key": "2022", "name": "2022 加息熊市", "peak": "2022-01-03", "trough": "2022-10-12"},
]


# --------------------------------------------------------------------------
# 指标 / 制度时间线
# --------------------------------------------------------------------------
def _max_drawdown(equity: pd.Series) -> float:
    if equity is None or len(equity) == 0:
        return 0.0
    peak = equity.cummax()
    return float(((peak - equity) / peak).max())


def _confirm_series(raw: list[Regime]) -> list[Regime]:
    """N 日确认门（时间顺序）：切换到 X 需连续 REGIME_CONFIRM_DAYS 根 raw==X。"""
    n = config.REGIME_CONFIRM_DAYS
    confirmed: list[Regime] = []
    current: Regime | None = None
    for i, today in enumerate(raw):
        if current is None:
            current = today
        elif today != current:
            window = raw[i - n + 1 : i + 1]
            if len(window) >= n and all(r == today for r in window):
                current = today
        confirmed.append(current)
    return confirmed


def _cagr(equity: pd.Series, index: pd.DatetimeIndex) -> float:
    if len(equity) < 2:
        return 0.0
    years = (index[-1] - index[0]).days / 365.25
    if years <= 0 or equity.iloc[0] <= 0:
        return 0.0
    return float((equity.iloc[-1] / equity.iloc[0]) ** (1.0 / years) - 1.0)


def regime_timeline(
    ticker: str,
    df: pd.DataFrame,
    bench: pd.DataFrame,
    vix: pd.Series | None,
) -> pd.DataFrame:
    """逐根 K 线算 T/R/制度，返回带 close/T/R/raw/regime/exposure/资金曲线的表。"""
    name = config.name_of(ticker)
    layer = config.layer_of(ticker)

    bench = bench.reindex(df.index).ffill().bfill()
    vix_aligned = (
        vix.reindex(df.index).ffill() if vix is not None else pd.Series(index=df.index, dtype=float)
    )

    dates: list = []
    rows: list[dict] = []
    for i in range(_WINDOW - 1, len(df)):
        window = df.iloc[i - _WINDOW + 1 : i + 1]
        bench_window = bench.iloc[i - _WINDOW + 1 : i + 1]
        try:
            ind = indicators.compute_indicators(
                window, bench_window, ticker=ticker, name=name, layer=layer
            )
        except ValueError:
            continue
        vix_val = None
        if vix is not None:
            v = vix_aligned.iloc[i]
            vix_val = float(v) if pd.notna(v) else None
            ind.vix = vix_val
            ind.prev_vix = (
                float(vix_aligned.iloc[i - 1]) if i > 0 and pd.notna(vix_aligned.iloc[i - 1]) else None
            )
        result = scoring.score_ticker(ind, breadth_pct=0.5, vix=vix_val)
        raw, _ = regime.classify(result)
        dates.append(df.index[i])
        rows.append({
            "close": float(df["Close"].iloc[i]),
            "T": result.T,
            "R": result.R,
            "raw_regime": raw.value,
        })

    out = pd.DataFrame(rows, index=pd.DatetimeIndex(dates))
    if out.empty:
        return out

    confirmed = _confirm_series([Regime(v) for v in out["raw_regime"]])
    out["regime"] = [r.value for r in confirmed]
    out["exposure"] = [_EXPOSURE[r] for r in confirmed]

    bh_ret = out["close"].pct_change().fillna(0.0)
    gated_ret = out["exposure"].shift(1).fillna(0.0) * bh_ret  # 次日生效
    out["bh_equity"] = (1.0 + bh_ret).cumprod()
    out["gated_equity"] = (1.0 + gated_ret).cumprod()
    return out


# --------------------------------------------------------------------------
# 指标汇总 / 逐次危机
# --------------------------------------------------------------------------
def _whipsaw_and_time(out: pd.DataFrame) -> dict:
    conf = [Regime(v) for v in out["regime"]]
    # 防御段
    stretches: list[int] = []
    i, n = 0, len(conf)
    while i < n:
        if conf[i] == Regime.RISK_OFF:
            j = i
            while j + 1 < n and conf[j + 1] == Regime.RISK_OFF:
                j += 1
            stretches.append(j - i + 1)
            i = j + 1
        else:
            i += 1
    time_pct = {
        r.value: round(100.0 * sum(1 for c in conf if c == r) / max(1, n), 1)
        for r in Regime
    }
    return {
        "defensive_episodes": len(stretches),
        "whipsaw_episodes": sum(1 for s in stretches if s < 10),  # 短促防御=假信号
        "time_pct": time_pct,
    }


def _crisis_metrics(out: pd.DataFrame) -> list[dict]:
    results: list[dict] = []
    for c in CRISES:
        peak_d = pd.Timestamp(c["peak"])
        trough_d = pd.Timestamp(c["trough"])
        seg = out[(out.index >= peak_d - pd.Timedelta(days=45)) & (out.index <= trough_d + pd.Timedelta(days=45))]
        if len(seg) < 10:
            continue  # 该标的无此段数据（如 QQQ 无 1999 前）
        pre = seg[seg.index <= trough_d + pd.Timedelta(days=10)]
        peak_idx = pre["close"].idxmax()
        peak_px = float(pre.loc[peak_idx, "close"])
        post = pre[pre.index >= peak_idx]
        trough_idx = post["close"].idxmin()
        trough_px = float(post.loc[trough_idx, "close"])
        bh_drop = trough_px / peak_px - 1.0

        window = out[(out.index >= peak_idx) & (out.index <= trough_idx)]
        risk_off = window[window["regime"] == Regime.RISK_OFF.value]
        row = {
            "key": c["key"],
            "name": c["name"],
            "peak_date": str(peak_idx.date()),
            "trough_date": str(trough_idx.date()),
            "bh_drop": round(bh_drop * 100, 1),
        }
        if len(risk_off):
            flip_idx = risk_off.index[0]
            flip_px = float(out.loc[flip_idx, "close"])
            pos = list(out.index)
            days_from_peak = pos.index(flip_idx) - pos.index(peak_idx)
            gated_seg = window["gated_equity"]
            gated_change = float(gated_seg.iloc[-1] / gated_seg.iloc[0] - 1.0)
            row.update({
                "flipped": True,
                "flip_date": str(flip_idx.date()),
                "days_from_peak": int(days_from_peak),
                "drop_at_flip": round((flip_px / peak_px - 1.0) * 100, 1),
                "avoided_after_flip": round((trough_px / flip_px - 1.0) * 100, 1),
                "gated_change": round(gated_change * 100, 1),
            })
        else:
            row.update({"flipped": False})
        results.append(row)
    return results


def run_backtest(
    tickers: list[str], *, offline: bool = False, period: str = "max"
) -> dict:
    """拉数据、对每个标的算制度时间线与指标，返回可渲染的 payload。"""
    need = list(dict.fromkeys(tickers + [config.BENCHMARK]))
    if offline:
        frames = data_fetch.synthetic_prices(need + [config.VIX_TICKER])
        vix_df = frames.get(config.VIX_TICKER)
    else:
        frames = data_fetch.fetch_prices(need, period=period)
        vix_df = data_fetch.fetch_vix(period=period)
    vix = vix_df["Close"] if vix_df is not None and "Close" in vix_df else None

    bench = frames.get(config.BENCHMARK)
    if bench is None:
        raise RuntimeError(f"基准 {config.BENCHMARK} 数据缺失，无法回测")

    payload: dict = {"period": period, "tickers": {}, "as_of": None}
    for t in tickers:
        df = frames.get(t)
        if df is None or len(df) < _WINDOW:
            payload["tickers"][t] = {"error": "数据不足"}
            continue
        out = regime_timeline(t, df, bench, vix)
        if out.empty:
            payload["tickers"][t] = {"error": "无有效制度序列"}
            continue
        overall = {
            "name": config.name_of(t),
            "start": str(out.index[0].date()),
            "end": str(out.index[-1].date()),
            "bars": int(len(out)),
            "bh_maxdd": round(_max_drawdown(out["bh_equity"]) * 100, 1),
            "gated_maxdd": round(_max_drawdown(out["gated_equity"]) * 100, 1),
            "bh_cagr": round(_cagr(out["bh_equity"], out.index) * 100, 1),
            "gated_cagr": round(_cagr(out["gated_equity"], out.index) * 100, 1),
            "bh_total": round((out["bh_equity"].iloc[-1] - 1.0) * 100, 1),
            "gated_total": round((out["gated_equity"].iloc[-1] - 1.0) * 100, 1),
            **_whipsaw_and_time(out),
        }
        overall["dd_saved"] = round(overall["bh_maxdd"] - overall["gated_maxdd"], 1)
        payload["tickers"][t] = {
            "overall": overall,
            "crises": _crisis_metrics(out),
            "_chart": _svg_chart(out, config.name_of(t), t),
        }
        payload["as_of"] = str(out.index[-1].date())
    return payload


# --------------------------------------------------------------------------
# 图表 (inline SVG) + 报告
# --------------------------------------------------------------------------
def _svg_chart(out: pd.DataFrame, name: str, ticker: str) -> str:
    W, Hp, He, pad = 960, 240, 130, 8
    n = len(out)
    step = max(1, n // 700)
    d = out.iloc[::step]
    m = len(d)
    if m < 2:
        return ""
    import math

    xs = [pad + (W - 2 * pad) * i / (m - 1) for i in range(m)]
    logp = [math.log(max(1e-9, v)) for v in d["close"]]
    lo, hi = min(logp), max(logp)
    span = (hi - lo) or 1.0

    def py(v):  # 价格 y（对数）
        return pad + (Hp - 2 * pad) * (1 - (v - lo) / span)

    price_pts = " ".join(f"{xs[i]:.1f},{py(logp[i]):.1f}" for i in range(m))

    # 防御区红色阴影
    shades = []
    regs = list(d["regime"])
    i = 0
    while i < m:
        if regs[i] == Regime.RISK_OFF.value:
            j = i
            while j + 1 < m and regs[j + 1] == Regime.RISK_OFF.value:
                j += 1
            x0 = xs[i]
            x1 = xs[min(j + 1, m - 1)]
            shades.append(f'<rect x="{x0:.1f}" y="{pad}" width="{max(1,x1-x0):.1f}" height="{Hp-2*pad}" fill="#cf3b3b" opacity="0.14"/>')
            i = j + 1
        else:
            i += 1

    # 资金曲线（对数，同图下方面板）
    def ey(v, elo, espan, top):
        return top + (He - 2 * pad) * (1 - (math.log(max(1e-9, v)) - elo) / espan)

    bh = list(d["bh_equity"])
    ga = list(d["gated_equity"])
    allv = [math.log(max(1e-9, v)) for v in bh + ga]
    elo, ehi = min(allv), max(allv)
    espan = (ehi - elo) or 1.0
    top = Hp + 24
    bh_pts = " ".join(f"{xs[i]:.1f},{ey(bh[i],elo,espan,top):.1f}" for i in range(m))
    ga_pts = " ".join(f"{xs[i]:.1f},{ey(ga[i],elo,espan,top):.1f}" for i in range(m))

    yr0, yr1 = d.index[0].year, d.index[-1].year
    return f'''<svg viewBox="0 0 {W} {Hp+He+40}" width="100%" preserveAspectRatio="xMidYMid meet" role="img" aria-label="{ticker} 制度与资金曲线">
  <text x="{pad}" y="16" font-size="13" font-weight="700" fill="currentColor">{name} {ticker} · 对数价格（红=确认防御区）· {yr0}–{yr1}</text>
  {''.join(shades)}
  <polyline points="{price_pts}" fill="none" stroke="#1f6feb" stroke-width="1.3"/>
  <text x="{pad}" y="{Hp+18}" font-size="12" font-weight="700" fill="currentColor">资金曲线（对数）：<tspan fill="#8a94a3">灰=买入持有</tspan> · <tspan fill="#1f6feb">蓝=制度调仓</tspan></text>
  <polyline points="{bh_pts}" fill="none" stroke="#8a94a3" stroke-width="1.2"/>
  <polyline points="{ga_pts}" fill="none" stroke="#1f6feb" stroke-width="1.5"/>
</svg>'''


def _crisis_rows(crises: list[dict]) -> str:
    if not crises:
        return '<tr><td colspan="6" class="muted">该标的无覆盖此区间的数据。</td></tr>'
    out = []
    for c in crises:
        if c.get("flipped"):
            flip = (f'{c["flip_date"]}<br><span class="muted">峰后 {c["days_from_peak"]} 日'
                    f'（已跌 {c["drop_at_flip"]}%）</span>')
            avoided = f'<b class="good">{c["avoided_after_flip"]}%</b>'
            gated = f'{c["gated_change"]}%'
        else:
            flip = '<span class="bad">未转防御</span>'
            avoided = '—'
            gated = '—'
        out.append(
            f'<tr><td><b>{c["name"]}</b><br><span class="muted">{c["peak_date"]}→{c["trough_date"]}</span></td>'
            f'<td class="num bad">{c["bh_drop"]}%</td>'
            f'<td class="num">{gated}</td>'
            f'<td>{flip}</td>'
            f'<td class="num">{avoided}</td></tr>'
        )
    return "".join(out)


def render_html(payload: dict) -> str:
    blocks = []
    for t, data in payload["tickers"].items():
        if "error" in data:
            blocks.append(f'<section class="card"><h2>{t}</h2><p class="bad">{data["error"]}</p></section>')
            continue
        o = data["overall"]
        saved_cls = "good" if o["dd_saved"] > 0 else "bad"
        blocks.append(f'''<section class="card">
      <h2>{o["name"]} · {t} <span class="muted">{o["start"]} → {o["end"]}（{o["bars"]} 交易日）</span></h2>
      <div class="kpis">
        <div class="kpi"><div class="lab">买入持有 最大回撤</div><div class="val bad">{o["bh_maxdd"]}%</div></div>
        <div class="kpi"><div class="lab">制度调仓 最大回撤</div><div class="val good">{o["gated_maxdd"]}%</div></div>
        <div class="kpi"><div class="lab">回撤改善</div><div class="val {saved_cls}">{o["dd_saved"]:+} pp</div></div>
        <div class="kpi"><div class="lab">防御时间占比</div><div class="val">{o["time_pct"].get("risk_off",0)}%</div></div>
        <div class="kpi"><div class="lab">假信号(短促防御)</div><div class="val">{o["whipsaw_episodes"]} / {o["defensive_episodes"]}</div></div>
        <div class="kpi"><div class="lab">年化 持有 / 调仓</div><div class="val small">{o["bh_cagr"]}% / {o["gated_cagr"]}%</div></div>
      </div>
      <table>
        <thead><tr><th>大跌事件</th><th>买入持有<br>峰→谷</th><th>制度调仓<br>同期</th><th>转防御时点</th><th>转防御后<br>指数又跌(避开)</th></tr></thead>
        <tbody>{_crisis_rows(data["crises"])}</tbody>
      </table>
      {data["_chart"]}
    </section>''')

    return f'''<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Atlas 生存回测 · {payload.get("as_of","")}</title>
<style>
  :root{{--bg:#f4f6f9;--panel:#fff;--ink:#1a1f28;--muted:#5c6673;--line:#e2e7ee;--good:#2e9658;--bad:#cf3b3b;}}
  @media (prefers-color-scheme:dark){{:root{{--bg:#0f131a;--panel:#171d27;--ink:#e7ecf3;--muted:#9aa5b3;--line:#262e3a;}}}}
  *{{box-sizing:border-box}} body{{margin:0}}
  .wrap{{max-width:1040px;margin:0 auto;padding:28px 18px 60px;background:var(--bg);color:var(--ink);
    font:15px/1.6 -apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC","Microsoft YaHei",sans-serif;min-height:100vh}}
  h1{{font-size:24px;margin:0 0 4px}} h2{{font-size:18px;margin:0 0 12px}}
  .sub{{color:var(--muted);margin-bottom:20px}}
  .method{{background:var(--panel);border:1px solid var(--line);border-radius:12px;padding:14px 16px;margin-bottom:20px;font-size:13px;color:var(--muted)}}
  .method b{{color:var(--ink)}}
  .card{{background:var(--panel);border:1px solid var(--line);border-radius:14px;padding:18px;margin-bottom:22px}}
  .muted{{color:var(--muted);font-weight:400;font-size:13px}}
  .good{{color:var(--good)}} .bad{{color:var(--bad)}}
  .kpis{{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:12px;margin-bottom:16px}}
  .kpi{{background:var(--bg);border:1px solid var(--line);border-radius:10px;padding:10px 12px}}
  .kpi .lab{{font-size:11px;color:var(--muted)}} .kpi .val{{font-size:22px;font-weight:700;font-variant-numeric:tabular-nums}}
  .kpi .val.small{{font-size:16px}}
  table{{width:100%;border-collapse:collapse;margin:8px 0 16px;font-size:14px}}
  th,td{{padding:8px 10px;border-bottom:1px solid var(--line);text-align:left;vertical-align:top}}
  th{{font-size:12px;color:var(--muted);font-weight:600}} td.num{{text-align:right;font-variant-numeric:tabular-nums}}
  svg{{background:var(--bg);border:1px solid var(--line);border-radius:10px;margin-top:8px;color:var(--ink)}}
  footer{{color:var(--muted);font-size:12px;margin-top:24px;line-height:1.7}}
</style></head><body><div class="wrap">
  <h1>Atlas · 生存回测报告</h1>
  <div class="sub">验证系统能否在历次大跌中<b>及时转防御</b>——评估的是「避开深跌」，不是收益。数据截至 {payload.get("as_of","")}</div>
  <div class="method">
    <b>方法学</b>：逐交易日用<b>固定行业标准参数</b>（200/50 日、ADX 25、VIX 20 …，不做历史最优化）重算趋势分 T 与风险分 R →
    四制度 + {config.REGIME_CONFIRM_DAYS} 日确认。敞口映射：进攻 1.0 / 警戒·超卖 0.5 / 防御 0.0，<b>次日生效</b>（无前视）。
    风险分喂入<b>真实 VIX</b>。广度维度置为中性（回测缺行业 ETF 历史成分），故制度主要由价格 / 均线 / 回撤 / 波动 / VIX 驱动。
    数据源：yfinance（Yahoo）。「转防御后指数又跌」即系统<b>降敞口后避开的那段下跌</b>。
  </div>
  {''.join(blocks)}
  <footer>本报告为方法示例，不构成投资建议。回测存在前视/幸存者偏差与实现摩擦（滑点、成本）未计入；
  制度调仓在震荡市会有假信号、在 V 型底反应滞后——这是「避开深熊」所付的保费。历史表现不代表未来。</footer>
</div></body></html>'''


def write_report(payload: dict, out_dir: str = "reports") -> tuple[str, str]:
    os.makedirs(out_dir, exist_ok=True)
    html_path = os.path.join(out_dir, "backtest.html")
    json_path = os.path.join(out_dir, "backtest.json")
    with open(html_path, "w", encoding="utf-8") as fh:
        fh.write(render_html(payload))
    slim = {k: v for k, v in payload.items()}
    slim["tickers"] = {
        t: {kk: vv for kk, vv in d.items() if kk != "_chart"}
        for t, d in payload["tickers"].items()
    }
    with open(json_path, "w", encoding="utf-8") as fh:
        json.dump(slim, fh, ensure_ascii=False, indent=2)
    return html_path, json_path


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------
def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Atlas 生存回测 — 验证避开大跌，非收益。")
    p.add_argument("--tickers", default="SPY,QQQ", help="逗号分隔，默认 SPY,QQQ")
    p.add_argument("--online", action="store_true", help="用 yfinance 真实数据（默认离线合成）")
    p.add_argument("--period", default="max", help="在线历史窗口（默认 max）")
    p.add_argument("--out", default="reports", help="报告输出目录（默认 reports/）")
    args = p.parse_args(argv)

    tickers = [t.strip().upper() for t in args.tickers.split(",") if t.strip()]
    payload = run_backtest(tickers, offline=not args.online, period=args.period)
    html_path, json_path = write_report(payload, args.out)

    print(f"=== Atlas 生存回测（数据截至 {payload.get('as_of')}）===")
    for t, d in payload["tickers"].items():
        if "error" in d:
            print(f"  {t}: {d['error']}")
            continue
        o = d["overall"]
        print(f"  {t} {o['start']}→{o['end']}: 买入持有回撤 {o['bh_maxdd']}% vs 制度调仓 {o['gated_maxdd']}% "
              f"(改善 {o['dd_saved']:+}pp) · 假信号 {o['whipsaw_episodes']}/{o['defensive_episodes']}")
        for c in d["crises"]:
            if c.get("flipped"):
                print(f"     {c['name']}: 买入持有 {c['bh_drop']}% | 峰后{c['days_from_peak']}日转防御, 避开随后 {c['avoided_after_flip']}%")
            else:
                print(f"     {c['name']}: 买入持有 {c['bh_drop']}% | ⚠️未转防御")
    print(f"报告 -> {html_path} , {json_path}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
