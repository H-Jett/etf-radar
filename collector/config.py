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
PRICE_DIR = os.path.join(DATA_DIR, "prices")               # 每只 ETF 的价格/份额时间序列
HISTORY_DIR = os.path.join(DATA_DIR, "history")            # 行业/份额历史累积
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
PRICE_WORKERS = 10           # 价格采集并发
PRICE_DAYS = 120             # 价格/份额时间序列回溯天数
SHARE_BACKFILL_DAYS = 90     # 首次运行时份额历史回溯的自然日数
MAX_RETRIES = 3

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
