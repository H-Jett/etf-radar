# -*- coding: utf-8 -*-
"""
行业国家队 ETF 看板 —— 采集配置

所有可调参数集中在此。不依赖数据库，产物为 docs/data 下的静态 JSON，
供 GitHub Pages 前端直接 fetch。
"""
import os

# ------------------------------------------------------------------
# 路径
# ------------------------------------------------------------------
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # 项目根
DATA_DIR = os.path.join(BASE_DIR, "docs", "data")          # 快照 JSON
SERIES_DIR = os.path.join(DATA_DIR, "series")              # series/<code>/<year>.json 逐 ETF 逐年
TRENDS_DIR = os.path.join(DATA_DIR, "trends")              # trends/<year>.json 各行业逐年日频
HOLDERS_DIR = os.path.join(DATA_DIR, "holders")            # holders/periods.json 报告期序列
HOLDERS_ETF_DIR = os.path.join(HOLDERS_DIR, "etf")         # holders/etf/<code>.json 每只ETF分期持仓(永久留存)
INDUSTRY_DIR = os.path.join(DATA_DIR, "industry")          # industry/<id>/<year>.json 行业内各ETF份额打包
LOG_DIR = os.path.join(BASE_DIR, "logs")

# ------------------------------------------------------------------
# “国家队”识别关键词（命中十大持有人名称即视为国家队）
#   —— 中央汇金 / 汇金资管 / 社保基金 / 证金公司（中国证券金融）
# ------------------------------------------------------------------
NT_KEYWORDS = [
    "中央汇金", "汇金资管", "汇金资产管理", "汇金投资",
    "社保基金", "全国社保", "基本养老", "养老保险基金",
    "证金", "中国证券金融",
]

# 机构归一化：把五花八门的持有人名称归到大类，便于跨 ETF/行业汇总
NT_GROUPS = [
    ("中央汇金", ["中央汇金投资", "汇金投资"]),
    ("汇金资管", ["汇金资产管理", "汇金资管"]),
    ("证金公司", ["证金", "中国证券金融"]),
    ("社保基金", ["社保基金", "全国社保"]),
    ("养老基金", ["基本养老", "养老保险基金"]),
]


def normalize_nt_group(name: str) -> str:
    """把持有人名称归一化到国家队大类；非国家队返回空串。"""
    for group, kws in NT_GROUPS:
        if any(kw in name for kw in kws):
            return group
    return ""


def is_nt_holder(name: str) -> bool:
    return any(kw in name for kw in NT_KEYWORDS)


# ------------------------------------------------------------------
# 采集参数
# ------------------------------------------------------------------
REQUEST_TIMEOUT = 20         # 单请求超时（秒）
SCAN_WORKERS = 12            # 持有人扫描并发线程
SCAN_DELAY = 0.15            # 每请求随机基准间隔（秒），防封
PRICE_WORKERS = 6            # 价格采集并发（东财对高并发较敏感，取保守值）
SHARE_BACKFILL_WORKERS = 8   # 份额历史并发（对上交所温和一些）
HOLDER_HISTORY_MAX_PERIODS = 0   # 历史报告期上限，0=尽可能全
MAX_RETRIES = 3

# 初始化默认起始日（init.py 可用 --start 覆盖）。份额与收盘价都从此日回补。
DEFAULT_START_DATE = "2016-01-01"

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")

# ------------------------------------------------------------------
# 数据源 URL（均为境外可达的 HTTPS 接口，适配 GitHub Actions）
# ------------------------------------------------------------------
SSE_SHARE_URL = (
    "https://query.sse.com.cn/commonQuery.do?isPagination=true"
    "&pageHelp.pageSize=10000&pageHelp.pageNo=1&pageHelp.beginPage=1"
    "&pageHelp.cacheSize=1&pageHelp.endPage=1"
    "&sqlId=COMMON_SSE_ZQPZ_ETFZL_XXPL_ETFGM_SEARCH_L&STAT_DATE={date}"
)
SZSE_LIST_URL = "https://www.szse.cn/api/report/ShowReport/data"
SINA_HOLDER_PAGE = "https://stock.finance.sina.com.cn/fundInfo/view/FundInfo_JJCYR.php?symbol={code}"
SINA_HOLDER_API = "https://stock.finance.sina.com.cn/fundInfo/api/openapi.php/CaihuiFundInfoService.getFundHolder"
SINA_KLINE_URL = (
    "https://quotes.sina.cn/cn/api/jsonp_v2.php/var%20_data=/CN_MarketDataService.getKLineData"
    "?symbol={prefix}{code}&scale=240&ma=no&datalen={datalen}"
)
