# Atlas · 趋势风险与机会监控系统

> Trend-based Risk & Opportunity Monitor — 每天回答一个问题：**现在，市场偏危险还是偏机会？**

Atlas 不做精确择时，只把趋势跟踪的规则量化成几个分数和一盏红黄绿的灯。
它是一个**静态发布的数据产品**：每天收盘后由 GitHub Actions 跑一遍批处理管线，
产出稳定的 JSON 数据契约（`public/data/*.json`），再由自包含的静态页面消费——**没有后端**。

完整设计与第一性原理见 [`architecture.md`](architecture.md)；三条铁律：
**① 生存优先 ② 不预测只响应 ③ 不对称下注**。

## 在线看板

- **每日看板**（美股收盘后自动刷新）：<https://wind22.github.io/Atlas/>
- **生存回测报告**：<https://wind22.github.io/Atlas/backtest.html>
- **数据契约**（页面消费的原始 JSON）：<https://wind22.github.io/Atlas/data/latest.json>

生存回测（真实数据）结论：历次大跌中，制度调仓把最大回撤从 **SPY −55%→−20%**、
**QQQ −80%→−24%** 大幅收敛——系统抓不住顶，但坐过了每一次主跌段。

## 快速开始

```bash
pip install -r requirements.txt

# 离线演示：用确定性合成数据跑通全流程，生成自包含 HTML 仪表盘 + 数据契约
python -m atlas --offline --output public/index.html

# 实盘：从 yfinance 拉取真实日线（需可访问 Yahoo Finance 的网络）
python -m atlas --stocks AAPL,NVDA,MSFT --output public/index.html

# 回测：验证「生存」而非收益——制度调仓能否规避深跌
python -m atlas.backtest --ticker SPY            # 离线合成
python -m atlas.backtest --ticker SPY --online   # 真实历史
```

打开生成的 `public/index.html` 查看当日市场姿态；同目录下的 `public/data/` 是页面消费的 JSON 数据契约。

## 部署到 Zeabur / 自有服务器

仓库根目录提供 `Dockerfile`，把静态看板、纽约时区的每日更新调度器和 HTTP 服务封装在
同一个容器里。服务监听 Zeabur 注入的 `PORT`，并提供 `/healthz` 健康检查。

部署时必须把一个持久卷挂载到 `/var/lib/atlas`；SQLite、生成后的站点和 JSON 数据契约
都存放在这里，容器重启或重新部署不会丢失制度确认状态。空卷首次启动时会尝试从公开
的 `data` 分支恢复最新状态，失败则降级到镜像内的冻结种子。

常用环境变量：

| 变量 | 默认值 | 说明 |
|---|---|---|
| `ATLAS_STOCKS` | 内置清单 | 逗号分隔的自选股 |
| `ATLAS_PERIOD` | `3y` | 行情历史窗口 |
| `ATLAS_RUN_ON_START` | `true` | 容器启动后立即刷新一次 |
| `ATLAS_SCHEDULE_HOUR` | `16` | 纽约时区运行小时 |
| `ATLAS_SCHEDULE_MINUTE` | `30` | 纽约时区运行分钟 |
| `ATLAS_OFFLINE` | `false` | 仅演示/诊断时使用合成数据 |
| `ATLAS_STATE_ARCHIVE_URL` | GitHub `data` 分支归档 | 空卷恢复来源；设为空可禁用 |

本地验证容器：

```bash
docker build -t atlas-monitor .
docker run --rm -p 8080:8080 -v atlas-state:/var/lib/atlas atlas-monitor
curl http://localhost:8080/healthz
```

真实券商或模拟盘凭证不得放进这个公开网站容器。交易执行应作为独立的私有 worker
部署，使用单独的持久卷和环境变量，只消费 Atlas 的状态结果。

### 命令行选项（`python -m atlas`）

| 选项 | 说明 |
|---|---|
| `--offline` | 使用确定性合成数据，无需网络（演示 / 自测） |
| `--stocks A,B,C` | 自选个股清单（逗号分隔）；省略时用内置自选清单（见 `config.DEFAULT_STOCKS`） |
| `--period` | yfinance 历史窗口，默认 `2y` |
| `--output` | 仪表盘输出路径，默认 `dashboard.html`（同目录下另写 `data/*.json` 数据契约） |
| `--db` | 快照数据库路径，默认 `atlas_snapshots.sqlite` |
| `--date` | 覆盖报告日期（用于回放 / 测试） |
| `--no-details` | 不生成每只自选股的历史详情页 |

## 系统怎么运作

四层监控（大盘 / 行业 / 多资产 / 自选个股）→ 每个标的算五维度 → 合成**趋势分 T**
（0–100，油门）和**风险分 R**（0–100，刹车）→ 映射到四种市场制度：

| 🟢 进攻 Risk-On | 🟡 警戒 Caution | 🔴 防御 Risk-Off | 🟠 超卖观察 Oversold |
|---|---|---|---|
| T≥60 且 R≤30 | 居中 | R≥60 或 T≤35（防御优先） | 防御条件下出现企稳信号 |

制度切换需连续 2 日确认以抑制假信号（whipsaw）；大盘制度是「总闸」，防御时个股整体降敞口。

在制度灯之上，报告层还产出**面向人的每日结论**：今日 headline、主要风险 / 机会、
较昨日变化、当前制度已持续多久、以及「历史上哪些日子的状态和今天最像」（仅供回看，
**不含任何未来走势预测**——铁律 Ⅱ）。

## 数据契约（`public/data/`）

发布出去的 JSON 才是产品核心，HTML 只是它的一种消费方式。`schema.json` 是版本化的
机器可读契约（`schema_version` 只增不改，字段只增不删不改类型）。

| 文件 | 内容 |
|---|---|
| `latest.json` / `daily/{date}.json` | 当日报告信封：`meta` + `report` + `explain`（结论/风险/机会/变化）+ `state`（制度持续/上次切换）+ `similar`（历史相似状态） |
| `regime_history.json` | 轻量制度时间序列（跨运行累积） |
| `dashboard_view.json` | 看板视图模型——HTML 与它同源，用它重渲染可逐字节复现 `index.html` |
| `universe.json` | 四层 ticker → 中文名 |
| `manifest.json` | 可用日期 + latest 指针 + 文件索引 |
| `schema.json` | 契约自述 + `schema_version` |

## 模块结构

分层组织，每层可单独测试 / 替换：

| 路径 | 职责 |
|---|---|
| `atlas/config.py` | universe、参数、阈值、权重（行业标准固定值，不做历史最优化） |
| `atlas/types.py` | 模块间共享的数据契约（`DailyReport` 等） |
| `atlas/data_fetch.py` | 拉取 OHLCV（yfinance）/ 确定性合成数据；处理缺失与除权 |
| `atlas/indicators.py` | 均线、ADX、MACD、RSI、相对强度、回撤、波动率、广度 |
| `atlas/scoring.py` | 五维度打分 → T 与 R（§3 规格） |
| `atlas/regime.py` | T/R → 制度 + 2 日确认（§4 规格） |
| `atlas/alerts.py` | 风险 / 机会离散提示（§5 规格） |
| `atlas/backtest.py` | 生存回测：制度调仓 vs 买入持有的最大回撤 |
| `atlas/report/` | 报告层（纯派生）：`explain` 每日结论 · `state_machine` 制度状态 · `similarity` 历史相似状态 |
| `atlas/storage/` | `snapshot_store` SQLite 快照 · `artifacts` 发布 `public/data/*.json` 契约 |
| `atlas/site/` | 静态渲染：`dashboard` 看板 + 视图模型 · `detail` 个股详情 · `about` 算法原理页 |
| `atlas/pipelines/daily.py` | 编排单个交易日全流程 |
| `atlas/runner.py` | argparse CLI（`python -m atlas`），委托给 `pipelines.daily` |

> `atlas/snapshot.py`、`atlas/dashboard.py`、`atlas/detail.py`、`atlas/about.py` 为向后兼容垫片，
> 真实实现已迁到 `storage/` 与 `site/`；新代码请从新位置导入。

## 状态与自动化

每日状态（SQLite 快照 + 已发布的 `public/data/`）存在一个机器专用的 **`data` 分支**，
不写回 `main`。`deploy-pages` 工作流在构建开始时从 `data` 分支恢复昨日状态，结束时把
今日状态推回——它是该分支唯一写者，因此没有提交竞争。这保证了 2 日制度确认、「较昨日变化」
高亮、增量提示在临时 CI 容器里也能跨日连续工作。

## 测试

```bash
python -m pytest        # 80 个黑盒单测：indicators / scoring / regime / alerts /
                        # 数据契约 / 解释层 / 状态机 / 相似度 / 视图模型 / 回测 / 持久化
```

测试为黑盒风格：用固定随机种子与固定日期构造数据，可复现。三条铁律被写成断言——
任何 JSON 产物都不得出现前向字段；Risk-Off 的结论恒为降敞口。

## 免责声明

本项目仅供学习与研究。评分、阈值、制度划分均为方法示例，不构成投资建议。
任何量化系统都可能失效，趋势策略存在滞后与亏损风险，历史表现不代表未来。
