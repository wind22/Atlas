"""算法原理页（自包含 HTML）。

把三条铁律、五维度评分 T、风险分 R、制度映射、固定参数与局限讲清楚。
所有数值取自 :mod:`atlas.config`，与系统实际运行一致——改参数则本页同步。
"""
from __future__ import annotations

from datetime import datetime, timezone

from . import config

_ARCH_URL = "https://github.com/wind22/Atlas/blob/main/architecture.md"


def _rows(items: list[tuple[str, object]]) -> str:
    return "".join(
        f'<tr><td>{cond}</td><td class="num"><b>{pts}</b></td></tr>' for cond, pts in items
    )


def render_about_page(*, source: str | None = None, generated_at: str | None = None) -> str:
    c = config
    if generated_at is None:
        generated_at = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    src = source or "yfinance"

    direction = _rows([
        (f"收盘价 &gt; {c.MA_LONG} 日均线（长期牛熊线）", c.DIR_ABOVE_MA200),
        (f"收盘价 &gt; {c.MA_SHORT} 日均线（中期）", c.DIR_ABOVE_MA50),
        (f"{c.MA_SHORT} 日线 &gt; {c.MA_LONG} 日线（多头排列）", c.DIR_MA50_ABOVE_MA200),
        (f"{c.MA_LONG} 日线斜率向上（今值 &gt; {c.MA_SLOPE_LOOKBACK} 日前）", c.DIR_MA200_RISING),
    ])
    momentum = _rows([
        ("12-1 月动量为正（剔除最近 1 月）", c.MOM_12_1_POS),
        (f"{c.RET_MID_MONTHS} 个月收益为正", c.MOM_6M_POS),
        (f"相对 SPY 的 {c.RS_MONTHS} 个月相对强度为正", c.MOM_RS_POS),
        (f"距 52 周高点 ≤ {c.NEAR_HIGH_PCT * 100:.0f}%（贴近新高）", c.MOM_NEAR_HIGH),
    ])
    strength = _rows([
        (f"ADX ≥ {c.ADX_TREND}（有明确趋势，而非震荡）", c.STR_ADX),
        ("MACD 柱状为正（DIF &gt; DEA）", c.STR_MACD),
    ])
    risk = _rows([
        (f"收盘价跌破 {c.MA_LONG} 日均线", f"+{c.RISK_BELOW_MA200}"),
        (f"死叉：{c.MA_SHORT} 日线 &lt; {c.MA_LONG} 日线", f"+{c.RISK_DEATH_CROSS}"),
        (f"自近 {c.DRAWDOWN_LOOKBACK} 日高点回撤 &gt; {c.DRAWDOWN_WARN*100:.0f}%"
         f"（&gt; {c.DRAWDOWN_SEVERE*100:.0f}% 再 +{c.RISK_DRAWDOWN_SEVERE_EXTRA}）", f"+{c.RISK_DRAWDOWN}"),
        (f"近 {c.VOL_WINDOW} 日波动率 &gt; 过去一年均值 × {c.VOL_SPIKE_MULT}", f"+{c.RISK_VOL_SPIKE}"),
        (f"VIX &gt; {c.VIX_ELEVATED:.0f}（&gt; {c.VIX_PANIC:.0f} 再 +{c.RISK_VIX_PANIC_EXTRA}）"
         "，大盘/多资产层专用", f"+{c.RISK_VIX}"),
        (f"广度：&lt; {c.BREADTH_WEAK*100:.0f}% 行业在 {c.MA_LONG} 日线上方", f"+{c.RISK_BREADTH_WEAK}"),
    ])

    return f'''<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>算法原理 · Atlas</title>
<meta name="theme-color" content="#0f131a">
<style>
  :root{{--bg:#f4f6f9;--panel:#fff;--ink:#1a1f28;--muted:#5c6673;--line:#e2e7ee;--good:#2e9658;--bad:#cf3b3b;--warn:#c98a12}}
  @media (prefers-color-scheme:dark){{:root{{--bg:#0f131a;--panel:#171d27;--ink:#e7ecf3;--muted:#9aa5b3;--line:#262e3a}}}}
  *{{box-sizing:border-box}} body{{margin:0}}
  .wrap{{max-width:820px;margin:0 auto;padding:18px 16px 64px;background:var(--bg);color:var(--ink);
    font:15px/1.7 -apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC","Microsoft YaHei",sans-serif;min-height:100vh}}
  a.back{{color:var(--muted);text-decoration:none;font-size:14px}} a.back:hover{{color:var(--ink)}}
  h1{{font-size:24px;margin:10px 0 2px}} .sub{{color:var(--muted);margin-bottom:18px}}
  h2{{font-size:19px;margin:26px 0 8px;padding-top:14px;border-top:1px solid var(--line)}}
  h3{{font-size:15px;margin:14px 0 4px;color:var(--muted)}}
  .card{{background:var(--panel);border:1px solid var(--line);border-radius:12px;padding:2px 14px;margin:8px 0}}
  table{{width:100%;border-collapse:collapse;font-size:14px}}
  th,td{{padding:7px 4px;border-bottom:1px solid var(--line);text-align:left;vertical-align:top}}
  td.num{{text-align:right;font-variant-numeric:tabular-nums;white-space:nowrap;padding-left:12px}}
  tr:last-child td{{border-bottom:none}}
  .law{{display:flex;gap:12px;margin:10px 0}} .law .n{{font-size:22px;font-weight:800;color:var(--good);line-height:1.2}}
  .law b{{font-size:15px}} .law div{{flex:1}}
  .pill{{display:inline-block;padding:2px 10px;border-radius:999px;font-size:13px;font-weight:600;margin:2px 4px 2px 0}}
  .p-on{{background:rgba(46,150,88,.16);color:var(--good)}} .p-cau{{background:rgba(201,138,18,.18);color:var(--warn)}}
  .p-off{{background:rgba(207,59,59,.16);color:var(--bad)}} .p-os{{background:rgba(214,140,40,.18);color:var(--warn)}}
  .muted{{color:var(--muted)}} .good{{color:var(--good)}} .bad{{color:var(--bad)}}
  .note{{background:var(--panel);border:1px solid var(--line);border-left:3px solid var(--good);
    border-radius:8px;padding:10px 12px;margin:10px 0;font-size:14px}}
  .links a{{display:inline-block;margin:6px 14px 6px 0;color:var(--good);text-decoration:none;font-weight:600}}
  footer{{color:var(--muted);font-size:12px;margin-top:26px;line-height:1.7}}
</style></head><body><div class="wrap">
  <a class="back" href="index.html">← 返回看板</a>
  <h1>算法原理</h1>
  <div class="sub">Atlas 怎么把「市场偏危险还是偏机会」算成两个分数和一盏灯。数值均取自系统实际参数。</div>

  <div class="note"><b>一句话</b>：这不是择时器，而是一副「求生仪」——它读市场先生的情绪与体温，
  在危险升高时提示收敛、在趋势明确时提示别过早下车。帮你活得久，从而让复利有机会发生。</div>

  <h2>一、三条铁律</h2>
  <div class="law"><div class="n">Ⅰ</div><div><b>生存优先（反脆弱）</b><br>
    <span class="muted">先保证不出局，再谈复利。亏 50% 要涨 100% 才回本——避免破产在数学上优先于追逐收益。所以系统的首要职责是<b>刹车（风险分 R）</b>，不是油门。</span></div></div>
  <div class="law"><div class="n">Ⅱ</div><div><b>不预测，只响应（谦逊）</b><br>
    <span class="muted">顶底不可预测。系统只度量<b>已经发生</b>的趋势与风险，输出状态与概率，绝不猜点位、不给买卖指令。</span></div></div>
  <div class="law"><div class="n">Ⅲ</div><div><b>不对称下注（凸性）</b><br>
    <span class="muted">亏时亏小、赢时赢大：截断亏损、放任盈利。用一连串小假信号的「保费」，换一次避开大熊市的「赔付」。</span></div></div>
  <div class="muted" style="font-size:13px">思想来源：塔勒布（反脆弱/遍历性）· 巴菲特（规则一/市场先生）· 芒格（反过来想）· 格雷厄姆（安全边际）· 马克斯（周期定位）· 利弗莫尔/海龟（顺势、截断亏损）。</div>

  <h2>二、趋势分 T（0–100，越高越强）</h2>
  <p class="muted" style="margin:2px 0">T = 方向 {c.W_DIRECTION} + 动量 {c.W_MOMENTUM} + 强度 {c.W_STRENGTH} + 广度 {c.W_BREADTH}。多维度共振才给强信号——这是行为上的「安全边际」。</p>
  <h3>① 趋势方向 Direction（0–{c.W_DIRECTION}）</h3>
  <div class="card"><table>{direction}</table></div>
  <h3>② 动量 Momentum（0–{c.W_MOMENTUM}）</h3>
  <div class="card"><table>{momentum}</table></div>
  <h3>③ 趋势强度 Strength（0–{c.W_STRENGTH}）</h3>
  <div class="card"><table>{strength}</table></div>
  <h3>④ 广度 Breadth（0–{c.W_BREADTH}，仅大盘层）</h3>
  <p class="muted" style="margin:2px 0">在 {c.MA_LONG} 日线上方的行业 ETF 占比线性给分：≥ {c.BREADTH_FULL*100:.0f}% 给满分 {c.W_BREADTH}，≤ {c.BREADTH_ZERO*100:.0f}% 趋近 0。少数龙头拉动、多数板块转弱＝顶部内部背离。个股/行业默认继承大盘广度。</p>

  <h2>三、风险分 R（0–100，越高越危险）</h2>
  <p class="muted" style="margin:2px 0">独立计算，用作「刹车」。这是芒格式的<b>否决清单</b>——先问「什么情况下会受伤」。任一条件成立即累加，上限 {c.RISK_CAP}。</p>
  <div class="card"><table>{risk}</table></div>

  <h2>四、市场制度（一盏灯）</h2>
  <p class="muted" style="margin:2px 0">把 T 和 R 映射到四种制度。<b>防御优先于进攻</b>：R 高（或 T 弱）时一律防御，无论 T 多高。</p>
  <div class="card"><table>
    <tr><td><span class="pill p-on">🟢 进攻 Risk-On</span></td><td>T ≥ {c.T_STRONG} 且 R ≤ {c.R_LOW}</td><td class="muted">顺势持有</td></tr>
    <tr><td><span class="pill p-cau">🟡 警戒 Caution</span></td><td>居中</td><td class="muted">减少新增、收紧止损</td></tr>
    <tr><td><span class="pill p-off">🔴 防御 Risk-Off</span></td><td>R ≥ {c.R_HIGH} 或 T ≤ {c.T_WEAK}</td><td class="muted">降低敞口</td></tr>
    <tr><td><span class="pill p-os">🟠 超卖观察 Oversold</span></td><td>防御条件下现企稳信号</td><td class="muted">观察，不急抄底</td></tr>
  </table></div>
  <div class="note"><b>抑制假信号</b>：制度切换需连续 <b>{c.REGIME_CONFIRM_DAYS} 日</b>满足条件才确认（防 whipsaw）。<b>层级优先</b>：大盘制度是总闸——大盘防御时，个股再强也整体降敞口。</div>

  <h2>五、固定参数（不做历史最优化）</h2>
  <p class="muted" style="margin:2px 0">全部采用行业公认值，<b>绝不针对历史回测调参</b>（防过拟合，铁律 Ⅱ）：</p>
  <div class="card"><table>
    <tr><td>长期/中期均线</td><td class="num">{c.MA_LONG} / {c.MA_SHORT} 日</td></tr>
    <tr><td>ADX 趋势阈值</td><td class="num">{c.ADX_TREND}</td></tr>
    <tr><td>VIX 警戒 / 恐慌</td><td class="num">{c.VIX_ELEVATED:.0f} / {c.VIX_PANIC:.0f}</td></tr>
    <tr><td>RSI 超卖</td><td class="num">&lt; {c.RSI_OVERSOLD}</td></tr>
    <tr><td>制度确认天数</td><td class="num">{c.REGIME_CONFIRM_DAYS} 日</td></tr>
  </table></div>

  <h2>六、诚实的边界</h2>
  <ul class="muted">
    <li><b>滞后</b>：信号出现在顶/底附近之后——目标是避大跌，不是抄顶摸底。</li>
    <li><b>震荡假信号</b>：横盘会有 whipsaw，靠多指标共振 + 确认机制降低频率。</li>
    <li><b>不含基本面与价值</b>：只读情绪与危险，不判断「便宜/贵」。</li>
    <li><b>防不住跳空</b>：只能截断「有过程」的下跌，隔夜跳空/闪崩无能为力，靠仓位与分散兜底。</li>
    <li><b>跨市场近似</b>：港股/A股/韩股的「相对 SPY 强度」为近似口径，仅供参考。</li>
  </ul>

  <div class="links" style="margin-top:20px">
    <a href="backtest.html">📊 生存回测报告（真实数据验证）</a>
    <a href="{_ARCH_URL}">📄 完整设计文档 architecture.md</a>
    <a href="index.html">← 返回看板</a>
  </div>

  <footer>数据来源 {src}｜参数取自系统实际配置｜本页生成于 {generated_at}。<br>
  仅供研究，不构成投资建议。任何量化系统都可能失效，历史表现不代表未来。</footer>
</div></body></html>'''


def write_about_page(path: str, *, source: str | None = None, generated_at: str | None = None) -> None:
    import os
    parent = os.path.dirname(os.path.abspath(path))
    if parent:
        os.makedirs(parent, exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(render_about_page(source=source, generated_at=generated_at))
