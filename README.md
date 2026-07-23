# 行业国家队 ETF 雷达（GitHub Pages 版）

按**行业 / 主题**追踪中国「国家队」——中央汇金、汇金资管、社保基金、证金公司（中国证券金融）——
持有的 ETF 及其份额、持仓占比变化。**纯静态站点，无服务器、无数据库**：
GitHub Actions 每天定时爬取 → 生成 JSON 提交回仓库 → GitHub Pages 托管前端。

🔗 在线：**https://h-jett.github.io/etf-radar/**

> 灵感来自 [kitaki-Ciallo/etf-national-tracker](https://github.com/kitaki-Ciallo/etf-national-tracker)（Flask + PostgreSQL 版），
> 本项目重构为**静态托管 + 行业维度聚合 + 点位准确的历史**。

## ✨ 功能（4 个页面）

- **行业总览** `index.html`：各行业国家队持有市值排行、平均占比、较上期变化、机构分布（表 + 条形图 + 饼图）；
  顶部有**当天采集状态**模块（✅/⚠️/❌ + 最近心跳，可展开看指标与警告）。
- **行业走势** `trends.html`：各行业「总份额」日频曲线（**日/周/月**切换 + 拖动）与「国家队持仓份额/占比」报告期序列，附数据表格（首列固定）。
- **行业详情** `detail.html`：选一个行业 → 该行业**所有成员 ETF 的份额多线 + 总量线**（图例勾选显隐）；下拉切换查看**单只 ETF 的十大持有人**。
- **采集日志** `logs.html`：每次运行的完整记录（按月翻页），一眼看出每天是否正常、异常原因。

交互：日/周/月与拖动的时间段用 `localStorage` **记忆并跨页共享**；图表本地渲染（`js/vendor/echarts.min.js`，不依赖 CDN）。

## 🧭 关键口径（务必了解）

- **时间一律北京时间（UTC+8）**。
- **份额 T+1**：ETF 份额当日数据次日才披露，所以「行情日」永远是**前一交易日**；收盘价对齐到该日（价、份同日）。
- **持仓占比 / 份额 = 报告期口径**：十大持有人只在半年报 / 年报披露，最长滞后约 6 个月；
  历史各期为**点位准确**——每期只计入「当期实际持有」的 ETF（含后来已退出的），不被当前成员低估。
- **持有市值** ≈ 报告期持有份额 × 最新收盘价，为近似值。

## 🗂️ 目录结构

```
etf-nt-industry/
├── collector/                # 采集层（纯 requests，无 pandas/bs4/DB）
│   ├── config.py             # 国家队关键词、接口、路径、参数
│   ├── industry.py           # ETF 名/拟合指数 → 行业分类
│   ├── sources.py            # 上交所份额 / 深交所份额(fund_jjgm) / 东财K线 / 新浪持有人
│   ├── collect.py            # 共享库：run_init / run_daily / run_deep_history 及各采集聚合函数
│   ├── init.py               # 入口：初始化/全量（--start / --deep-history）
│   ├── daily.py              # 入口：每日增量
│   └── requirements.txt
├── docs/                     # ← GitHub Pages 根目录
│   ├── index / trends / detail / logs .html
│   ├── css/style.css
│   ├── js/{util,main,trends,detail,logs}.js + js/vendor/echarts.min.js
│   └── data/                 # Actions 自动生成/提交
│       ├── meta.json                 # 更新时间/报告期/series_years/industry_order/统计
│       ├── industries.json           # 各行业当前快照聚合（首页）
│       ├── etfs.json                 # 各国家队 ETF 快照 + 十大持有人 + years
│       ├── universe.json             # 国家队 ETF 清单（日更复用）
│       ├── status.json               # 最新运行状态 + 最近 20 次
│       ├── series/<code>/<year>.json # 每 ETF 每年日频 {prices, shares}
│       ├── trends/<year>.json        # 各行业当年日频总份额
│       ├── industry/<id>/<year>.json # 行业内各成员 ETF 份额打包 + index.json
│       ├── holders/periods.json      # 各行业国家队持仓 报告期(半年)序列（点位准确）
│       ├── holders/etf/<code>.json   # 每 ETF 分期国家队持仓（永久留存，支撑点位准确）
│       └── runs/<YYYY-MM>.json       # 全量运行日志（按月分片）+ index.json
└── .github/workflows/collect.yml
```

**为什么按年/月分片**：份额/价格回补到 ~10 年，若整只 ETF 一个大文件，每天提交都要重写整份、git 历史迅速膨胀。
按「ETF×年份」「行业×年份」分片后，每天只改「当年」小片，旧年份永不再动，仓库精简。

## 📊 数据源

| 数据 | 来源 | 说明 |
|---|---|---|
| 上交所 ETF 逐日份额 | `query.sse.com.cn` COMMON_SSE_...ETFGM | 按 STAT_DATE 取任意历史日，TOT_VOL(万份) |
| 深交所 ETF 逐日份额 | `fund.szse.cn` ShowReport `CATALOGID=fund_jjgm` | 按 txtStart/txtEnd 取历史，current_size(万份)，单窗≤~110交易日需分窗+分页 |
| 收盘价（全历史日 K） | 东方财富 `push2his.eastmoney.com` kline | 与份额同期、对齐到行情日 |
| 十大持有人 + 报告期 | 新浪财经 CaihuiFundInfoService | 半年报/年报 |

## 🚀 部署到 GitHub Pages（3 步）

1. 新建仓库并推送本项目（先本地跑一次生成初始数据一起提交，见下）。
2. `Settings → Pages`：Source 选 **Deploy from a branch → `main` → `/docs`**。
3. `Settings → Actions → General → Workflow permissions`：选 **Read and write**（否则 Actions 无法提交数据）。

之后 Actions 每天北京时间约 09:03 自动更新并推送，Pages 随之刷新。也可在 Actions 手动 `Run workflow`（`init` 可指定 `start` 补更久历史）。

> GitHub Pages 经典分支源有 **~10 次/小时构建配额**，短时间频繁推送会导致站点延迟重建——攒批提交即可。

## 💻 本地运行

```bash
cd collector
pip install -r requirements.txt

python init.py                     # 初始化/全量（默认从 2016-01-01）
python init.py --start 2018-01-01  # 指定起始日
python init.py --deep-history      # 深度回补：扫全市场历史报告期，补齐已退出但仍上市的历史国家队 ETF
python daily.py                    # 每日增量（Actions 调用；发现新报告期自动升级初始化）

cd ../docs && python -m http.server 8899   # 本地预览 http://localhost:8899
```

**容错 / 幂等**：份额、收盘价按日期合并去重，**重复运行不产生重复、不破坏数据**；拉不到数据则跳过不写盘并落 error 状态；`daily` 无既有数据自动回退初始化。

## ⚠️ 已知边界

- 深市份额历史约回溯到 2016（fund_jjgm 起始）；2026-07 前就**彻底摘牌下市**的老 ETF，公开接口取不到，无法纳入历史（极少数）。
- 十大持有人为**每只基金各自披露**，无"行业十大汇总"概念；行业层面看首页/走势页的国家队汇总。

数据源：上交所、深交所、东方财富、新浪财经。**本站为公开数据聚合，仅供研究参考，不构成投资建议。**

## 📄 许可

MIT
