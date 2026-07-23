# 行业国家队 ETF 看板（GitHub Pages 版）

按**行业 / 主题**追踪中国「国家队」——中央汇金、汇金资管、社保基金、证金公司（中国证券金融）——
持有的 ETF 及其份额变化。**纯静态站点，无需服务器、无需数据库**：
数据由 GitHub Actions 定时爬取并生成 JSON 提交回仓库，GitHub Pages 直接托管前端。

> 灵感来自 [kitaki-Ciallo/etf-national-tracker](https://github.com/kitaki-Ciallo/etf-national-tracker)（Flask + PostgreSQL 版）。
> 本项目把它重构为**静态托管 + 行业维度聚合**。

## ✨ 特性

- **行业总览**：各行业国家队持有市值排行、平均持有占比、较上期份额变化、主要出资机构（首页表格 + 条形图 + 机构分布饼图）。
- **ETF 详情 / 图表浏览器**：
  - 下拉切换任意国家队 ETF（按行业分组）；
  - **日 / 周 / 月**三种周期切换；
  - 图表底部滑块**可拖动**查看不同时间段（ECharts dataZoom）；
  - 单位净值（左轴）+ 总份额（右轴）双轴叠加；
  - 十大持有人明细，国家队机构高亮，含本期 vs 上期占比变化、新进标记。
- **每天北京时间 09:00 自动更新**，并在半年报 / 年报发布时**自动重扫**全市场持有人。
- **数据结构化、无数据库**：全部是 `docs/data/` 下的 JSON 文件。

## 🗂️ 目录结构

```
etf-nt-industry/
├── collector/                # 采集层（Python，纯 requests，无 pandas/bs4/DB）
│   ├── config.py             # 国家队关键词、接口 URL、参数
│   ├── industry.py           # ETF 名 / 拟合指数 → 行业分类规则
│   ├── sources.py            # 上交所 / 深交所 / 新浪 数据抓取
│   ├── collect.py            # 主程序（--full 全量 / 默认日更）
│   └── requirements.txt
├── docs/                     # ← GitHub Pages 根目录
│   ├── index.html            # 行业总览
│   ├── trends.html           # 行业走势（份额/持仓占比随时间，日/周/月）
│   ├── detail.html           # ETF 详情 / 图表浏览器
│   ├── css/style.css
│   ├── js/{main,trends,detail}.js
│   └── data/                 # 自动生成的 JSON（由 Actions 提交）
│       ├── meta.json             # 更新时间、报告期、series_years、总量统计
│       ├── industries.json       # 各行业国家队当前快照汇总（首页核心）
│       ├── etfs.json             # 各国家队 ETF 快照 + 十大持有人 + years
│       ├── universe.json         # 国家队 ETF 代码清单（日更复用）
│       ├── series/<code>/<year>.json   # 每 ETF 每年日频 {prices,shares}
│       ├── trends/<year>.json           # 各行业该年日频总份额
│       └── holders/periods.json         # 各行业国家队持仓份额/占比 报告期序列
└── .github/workflows/collect.yml
```

**为什么按「ETF × 年份」「行业 × 年份」切分？** 份额是日频、往回补到约 10 年，若整只 ETF 存一个文件，每天提交都要重写整份大文件，git 历史会迅速膨胀。按年切分后，**每天只改「当年」那一小片**，旧年份分片永不再变，仓库保持精简、单文件都小。

## 📊 数据结构（JSON）

`industries.json`（首页）—— 数组，每行业一项：`nt_amount`（国家队持有份额，报告期口径）、`nt_value`（≈份额×最新净值）、`nt_ratio`（份额加权平均占比%）、`amount_change`（较上期）、`new_entries`、`groups`（各机构份额）、`etfs`（成员）。

`etfs.json` —— 每只国家队 ETF：`nt_holders`（国家队持有人，带上期占比/新进/环比）、`all_holders`（完整十大）、`report_date`、`years`（有数据的年份）。

`series/<code>/<year>.json` —— `{"prices":[["2026-07-22",4.765],…], "shares":[["2026-07-22",2.4e10],…]}`，日频；前端按需加载该 ETF 各年份分片并按日/周/月聚合。

`trends/<year>.json` —— `{"dates":[…], "industries":{"宽基":{"total_share":[…]}}}`，各行业当年日频总份额（行业走势页用）。

`holders/periods.json` —— `{"periods":["2016-06-30",…,"2025-12-31"], "industries":{"宽基":{"nt_amount":[…],"nt_ratio":[…],"num_etfs":[…]}}}`，**半年一个点**的国家队持仓演变（尽可能回溯到各 ETF 成立）。

## 🚀 部署到 GitHub Pages（3 步）

1. **新建仓库并推送本项目**（见下方「本地运行」先生成一份初始数据一起提交）。
2. **开启 Pages**：仓库 `Settings → Pages → Build and deployment`：
   - Source 选 **Deploy from a branch**；
   - Branch 选 **`main`**，目录选 **`/docs`**，保存。
   - 稍等片刻，站点地址形如 `https://<用户名>.github.io/<仓库名>/`。
3. **授权 Actions 写权限**：`Settings → Actions → General → Workflow permissions`
   选 **Read and write permissions**（否则 Actions 无法把数据提交回仓库）。

之后 Actions 每天北京时间 09:00 自动更新数据并推送，Pages 随之刷新。
也可在 `Actions → 采集国家队 ETF 数据 → Run workflow` 手动触发（可选 `full` 全量）。

> ⚠️ GitHub 托管的 runner 位于境外。本项目所有接口（上交所 / 深交所 / 新浪）**境外一般可达**，
> 并内置 `requests → curl` 兜底与重试。若个别时段被限流，可改用**中国境内的 self-hosted runner**，
> 或本地跑 `collect.py` 后提交。

## 💻 本地运行

```bash
cd collector
pip install -r requirements.txt

python collect.py --full     # 首次：全量扫描全市场，重建国家队 universe（约 1-2 分钟）
python collect.py            # 日更：仅刷新份额/价格；发现新报告期会自动升级为全量

# 本地预览前端
cd ../docs && python -m http.server 8899   # 打开 http://localhost:8899
```

## 🏦 「国家队」口径与数据说明

- **识别方式**：扫描每只 ETF 半年报 / 年报披露的**十大持有人**，命中以下关键词即计入国家队：
  中央汇金、汇金资管、汇金投资、社保基金、全国社保、基本养老、证金、中国证券金融。
  （可在 `collector/config.py` 的 `NT_KEYWORDS` / `NT_GROUPS` 调整。）
- **重要时滞**：持有人数据依公募披露规则，**仅在半年报 / 年报出现，最长滞后约 6 个月**，
  展示的占比 / 变化均为**最近披露报告期**的快照。
- **持有市值**为近似值 = 报告期持有份额 × 最新单位净值（份额天天变动，持仓半年才披露，二者口径不同）。
- **行业分类**基于 ETF 简称 + 拟合指数名的关键词规则（`collector/industry.py`），
  宽基指数单列为「宽基」。规则可自行扩充。

数据源：上海证券交易所、深圳证券交易所、新浪财经。**本站仅为公开数据聚合，供研究参考，不构成任何投资建议。**

## 📄 许可

MIT
