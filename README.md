# Atlas · 趋势风险与机会监控系统

> Trend-based Risk & Opportunity Monitor — 每天回答一个问题：**现在，市场偏危险还是偏机会？**

Atlas 不做精确择时，只把趋势跟踪的规则量化成几个分数和一盏红黄绿的灯。
完整设计与第一性原理见 [`architecture.md`](architecture.md)；三条铁律：
**① 生存优先 ② 不预测只响应 ③ 不对称下注**。

## 快速开始

```bash
pip install -r requirements.txt

# 离线演示：用确定性合成数据跑通全流程，生成自包含 HTML 仪表盘
python -m atlas --offline --output dashboard.html

# 实盘：从 yfinance 拉取真实日线（需可访问 Yahoo Finance 的网络）
python -m atlas --stocks AAPL,NVDA,MSFT --output dashboard.html

# 回测：验证「生存」而非收益——制度调仓能否规避深跌
python -m atlas.backtest --ticker SPY            # 离线合成
python -m atlas.backtest --ticker SPY --online   # 真实历史
```

打开生成的 `dashboard.html` 即可查看当日市场姿态。

### 命令行选项（`python -m atlas`）

| 选项 | 说明 |
|---|---|
| `--offline` | 使用确定性合成数据，无需网络（演示 / 自测） |
| `--stocks A,B,C` | 自选个股清单（逗号分隔），默认 AAPL,NVDA,MSFT |
| `--period` | yfinance 历史窗口，默认 `2y` |
| `--output` | 仪表盘输出路径，默认 `dashboard.html` |
| `--db` | 快照数据库路径，默认 `atlas_snapshots.sqlite` |
| `--date` | 覆盖报告日期（用于回放 / 测试） |

## 系统怎么运作

四层监控（大盘 / 行业 / 多资产 / 自选个股）→ 每个标的算五维度 → 合成**趋势分 T**
（0–100，油门）和**风险分 R**（0–100，刹车）→ 映射到四种市场制度：

| 🟢 进攻 Risk-On | 🟡 警戒 Caution | 🔴 防御 Risk-Off | 🟠 超卖观察 Oversold |
|---|---|---|---|
| T≥60 且 R≤30 | 居中 | R≥60 或 T≤35（防御优先） | 防御条件下出现企稳信号 |

制度切换需连续 2 日确认以抑制假信号（whipsaw）；大盘制度是「总闸」，防御时个股整体降敞口。

## 模块结构

| 模块 | 职责 |
|---|---|
| `atlas/config.py` | universe、参数、阈值、权重（行业标准固定值，不做历史最优化） |
| `atlas/types.py` | 模块间共享的数据契约 |
| `atlas/data_fetch.py` | 拉取 OHLCV（yfinance）/ 确定性合成数据 |
| `atlas/indicators.py` | 均线、ADX、MACD、RSI、相对强度、回撤、波动率、广度 |
| `atlas/scoring.py` | 五维度打分 → T 与 R（§3 规格） |
| `atlas/regime.py` | T/R → 制度 + 2 日确认（§4 规格） |
| `atlas/alerts.py` | 风险 / 机会离散提示（§5 规格） |
| `atlas/snapshot.py` | SQLite 快照，供日间对比与回测 |
| `atlas/dashboard.py` | 渲染自包含 HTML 仪表盘（§6） |
| `atlas/runner.py` | 编排全流程 + CLI |
| `atlas/backtest.py` | 制度回测：对比买入持有 vs 制度调仓的最大回撤 |

## 测试

```bash
python -m pytest        # 26 个黑盒单测，覆盖 indicators / scoring / regime / alerts
```

## 免责声明

本项目仅供学习与研究。评分、阈值、制度划分均为方法示例，不构成投资建议。
任何量化系统都可能失效，趋势策略存在滞后与亏损风险，历史表现不代表未来。
