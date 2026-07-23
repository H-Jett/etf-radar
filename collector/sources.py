# -*- coding: utf-8 -*-
"""
数据源抓取（纯 requests + 正则 + json，无 bs4/pandas 依赖）

覆盖：
  - 上交所 ETF 份额（含名称）           SSE commonQuery
  - 深交所 ETF 列表（含拟合指数、规模）  SZSE ShowReport
  - 新浪财经 十大持有人 + 报告期列表     Sina CaihuiFundInfoService
  - 新浪财经 日 K 线（价格/成交量）      Sina getKLineData
"""
import re
import json
import time
import logging
import subprocess
from datetime import datetime, timedelta, timezone

_CN_TZ = timezone(timedelta(hours=8))   # 所有日期按北京时间(UTC+8)确定

import requests
import urllib3

from config import (
    SSE_SHARE_URL, SZSE_LIST_URL, SINA_HOLDER_PAGE, SINA_HOLDER_API,
    SINA_KLINE_URL, UA, REQUEST_TIMEOUT, MAX_RETRIES,
)

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
log = logging.getLogger("sources")

_SSE_HEADERS = {"Referer": "https://www.sse.com.cn/", "User-Agent": UA}
_SZSE_HEADERS = {
    "User-Agent": UA,
    "Referer": "https://www.szse.cn/market/product/list/etfList/index.html",
    "Accept": "application/json, text/javascript, */*; q=0.01",
}
_SINA_HEADERS = {"User-Agent": UA, "Referer": "https://finance.sina.com.cn/"}


def exchange_prefix(code: str) -> str:
    """沪 sh / 深 sz 前缀判断。"""
    return "sz" if code.startswith(("15", "16")) else "sh"


# ==================================================================
# 上交所份额
# ==================================================================
def fetch_sse_shares(date_str: str) -> dict:
    """
    抓取某日全部上交所 ETF 份额。
    date_str: 'YYYY-MM-DD'
    返回 {code: {"name": str, "share": 份额(份)}}
    """
    url = SSE_SHARE_URL.format(date=date_str)
    rows = _get_json_with_curl_fallback(url, _SSE_HEADERS)
    out = {}
    for item in (rows.get("result", []) if isinstance(rows, dict) else []):
        code = str(item.get("SEC_CODE", "")).strip()
        name = str(item.get("SEC_NAME", "")).strip()
        try:
            share = float(item.get("TOT_VOL", 0)) * 10000  # 万份 -> 份
        except (TypeError, ValueError):
            share = 0.0
        if code:
            out[code] = {"name": name, "share": share}
    return out


def fetch_sse_shares_latest(max_lookback: int = 12):
    """从今天往回找最近一个有数据的交易日。返回 (date_str, {code:{...}})。"""
    for i in range(1, max_lookback + 1):
        d = datetime.now(_CN_TZ).date() - timedelta(days=i)
        ds = d.strftime("%Y-%m-%d")
        data = fetch_sse_shares(ds)
        if data:
            log.info("上交所份额：最新交易日 %s，共 %d 只 ETF", ds, len(data))
            return ds, data
        log.info("上交所份额：%s 无数据（非交易日？）", ds)
    return None, {}


# ==================================================================
# 深交所列表（含拟合指数、规模）
# ==================================================================
_RE_U = re.compile(r"<u>(.*?)</u>")


def fetch_szse_list() -> dict:
    """
    抓取深交所全部 ETF。返回 {code: {"name","index_name","share"}}。
    share 由「当前规模」(万份) 估算 -> 份；深交所无逐日份额接口，仅取当前规模。
    """
    out = {}
    page = 1
    total_pages = None
    while True:
        params = {"SHOWTYPE": "JSON", "CATALOGID": "1945", "TABKEY": "tab1",
                  "PAGENO": str(page), "random": "0.123"}
        try:
            r = requests.get(SZSE_LIST_URL, headers=_SZSE_HEADERS, params=params,
                             timeout=REQUEST_TIMEOUT)
            blocks = r.json()
        except Exception as e:  # noqa
            log.warning("深交所列表第 %d 页失败：%s", page, e)
            break
        if not blocks:
            break
        blk = blocks[0]
        meta = blk.get("metadata", {})
        if total_pages is None:
            total_pages = int(meta.get("pagecount", 1))
        for row in blk.get("data", []):
            code_m = _RE_U.search(row.get("sys_key", ""))
            name_m = _RE_U.search(row.get("kzjcurl", ""))
            if not code_m:
                continue
            code = code_m.group(1).strip()
            name = name_m.group(1).strip() if name_m else ""
            # nhzs 形如 "399266 创新能源"
            nhzs = str(row.get("nhzs", "")).strip()
            index_name = nhzs.split(None, 1)[1] if " " in nhzs else nhzs
            try:
                share = float(str(row.get("dqgm", "0")).replace(",", "")) * 10000
            except ValueError:
                share = 0.0
            out[code] = {"name": name, "index_name": index_name, "share": share}
        log.info("深交所列表：第 %d/%s 页，累计 %d 只", page, total_pages, len(out))
        if page >= (total_pages or 1):
            break
        page += 1
        time.sleep(0.1)
    return out


# ==================================================================
# 深交所 ETF 历史逐日份额（fund.szse.cn fund_jjgm，按日期区间；单窗口≤~110 交易日）
# ==================================================================
_SZSE_FUND_URL = "https://fund.szse.cn/api/report/ShowReport/data"
_SZSE_FUND_HEADERS = {"User-Agent": UA, "Referer": "https://fund.szse.cn/"}


def fetch_szse_shares_history(code, start, end):
    """
    深市 ETF 在 [start, end] 区间的历史逐日份额。start/end='YYYY-MM-DD'。
    返回 [[date, share(份)]...]。单个区间上限约 110 个交易日，调用方需分窗。
    current_size 单位万份 → ×10000 转份。
    """
    out = []
    page = 1
    total_pages = None
    while True:
        params = {"SHOWTYPE": "JSON", "CATALOGID": "fund_jjgm", "TABKEY": "tab1",
                  "txtDm": code, "txtStart": start, "txtEnd": end, "PAGENO": str(page)}
        try:
            r = requests.get(_SZSE_FUND_URL, params=params,
                             headers=_SZSE_FUND_HEADERS, timeout=REQUEST_TIMEOUT)
            blocks = r.json()
        except Exception:  # noqa
            break
        if not blocks:
            break
        blk = blocks[0]
        if total_pages is None:
            total_pages = int(blk.get("metadata", {}).get("pagecount", 1) or 1)
        for row in blk.get("data", []):
            d = row.get("size_date")
            cs = row.get("current_size")
            if d and cs:
                try:
                    out.append([d, float(str(cs).replace(",", "")) * 10000])
                except ValueError:
                    continue
        if page >= (total_pages or 1):
            break
        page += 1
        time.sleep(0.1)
    return out


# ==================================================================
# 新浪 十大持有人
# ==================================================================
_RE_OPTION = re.compile(r'<option\s+value="([0-9]{4}-[0-9]{2}-[0-9]{2})"')


def fetch_report_dates(code: str):
    """返回可用报告期列表（新->旧），如 ['2025-12-31','2025-06-30',...]。"""
    url = SINA_HOLDER_PAGE.format(code=code)
    try:
        r = requests.get(url, headers=_SINA_HEADERS, timeout=REQUEST_TIMEOUT)
        r.encoding = "gbk"
        return _RE_OPTION.findall(r.text)
    except Exception:  # noqa
        return []


def fetch_holders(code: str, report_date: str):
    """
    返回某报告期十大持有人：[{"rank","name","amount"(份),"ratio"(%)}...]
    """
    params = {"symbol": code, "date": report_date}
    headers = {**_SINA_HEADERS,
               "Referer": SINA_HOLDER_PAGE.format(code=code)}
    try:
        r = requests.get(SINA_HOLDER_API, params=params, headers=headers,
                         timeout=REQUEST_TIMEOUT)
        raw = r.json().get("result", {}).get("data", [])
    except Exception:  # noqa
        return []
    holders = []
    for i, h in enumerate(raw, 1):
        name = str(h.get("cyrmc", "")).strip()
        if not name:
            continue
        try:
            amount = float(h.get("cyfe", 0) or 0)
        except (TypeError, ValueError):
            amount = 0.0
        try:
            ratio = float(h.get("zfeb", 0) or 0)
        except (TypeError, ValueError):
            ratio = 0.0
        holders.append({"rank": i, "name": name, "amount": amount, "ratio": ratio})
    return holders


# ==================================================================
# 新浪 日 K 线
# ==================================================================
_RE_KDATA = re.compile(r"var\s+_data\s*=\s*\((\[.*?\])\)", re.DOTALL)


def fetch_kline(code: str, datalen: int = 120):
    """返回 [{"day","open","high","low","close","volume"}...]（旧->新）。"""
    prefix = exchange_prefix(code)
    url = SINA_KLINE_URL.format(prefix=prefix, code=code, datalen=datalen)
    try:
        r = requests.get(url, headers=_SINA_HEADERS, timeout=REQUEST_TIMEOUT)
        m = _RE_KDATA.search(r.text)
        if not m:
            return []
        return json.loads(m.group(1))
    except Exception:  # noqa
        return []


# ==================================================================
# 东方财富 日 K（全历史，收盘价与份额可对齐；境外可达更稳）
# ==================================================================
_EM_KLINE_URL = "https://push2his.eastmoney.com/api/qt/stock/kline/get"


_EM_HEADERS = {"User-Agent": UA, "Referer": "https://quote.eastmoney.com/"}


def fetch_kline_em(code: str, exchange: str, beg: str = "0"):
    """
    东方财富全历史日 K。返回 [[date, close]...]（旧->新）。
    beg: 起始日 'YYYYMMDD' 或 '0'（全历史）。exchange: 'sh'/'sz'。

    东财在密集请求下会断连/返回空 → 带重试 + 退避;区分“接口抖动”与“确实无数据”：
    仅当拿到 rc==0 且 klines 明确为空时才判定无数据（不再重试）。
    """
    secid = ("1." if exchange == "sh" else "0.") + code
    params = {
        "secid": secid, "fields1": "f1,f2,f3",
        "fields2": "f51,f53",             # f51=日期, f53=收盘价（不复权）
        "klt": "101", "fqt": "0", "beg": beg, "end": "20500101", "lmt": "1000000",
    }
    def _parse(j):
        data = (j or {}).get("data")
        if data is None:
            return None                                # 抖动/限流 → 需重试
        out = []
        for row in data.get("klines", []) or []:
            parts = row.split(",")
            if len(parts) >= 2:
                try:
                    out.append([parts[0], float(parts[1])])
                except ValueError:
                    continue
        return out                                     # 明确结果（含空列表）

    for attempt in range(1, MAX_RETRIES + 1):
        # requests 优先
        try:
            r = requests.get(_EM_KLINE_URL, params=params,
                             headers=_EM_HEADERS, timeout=REQUEST_TIMEOUT)
            res = _parse(r.json())
            if res is not None:
                return res
        except Exception:  # noqa 断连/超时/JSON 解析失败
            pass
        # curl 兜底（常能绕过 requests 被重置的连接）
        try:
            qs = "&".join(f"{k}={v}" for k, v in params.items())
            cmd = ["curl", "-s", "--max-time", str(REQUEST_TIMEOUT),
                   "-H", f"User-Agent: {UA}", "-H", "Referer: https://quote.eastmoney.com/",
                   f"{_EM_KLINE_URL}?{qs}"]
            out = subprocess.run(cmd, capture_output=True, timeout=REQUEST_TIMEOUT + 5)
            if out.returncode == 0 and out.stdout:
                res = _parse(json.loads(out.stdout.decode("utf-8", errors="replace")))
                if res is not None:
                    return res
        except Exception:  # noqa
            pass
        time.sleep(1.2 * attempt + 0.3)
    log.warning("东财 K 线多次重试(含 curl)仍失败：%s", secid)
    return []


# ==================================================================
# 底层：requests 优先，失败回退 curl（个别接口在某些网络下 requests 会被重置）
# ==================================================================
def _get_json_with_curl_fallback(url: str, headers: dict):
    last_err = None
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            r = requests.get(url, headers=headers, timeout=REQUEST_TIMEOUT, verify=False)
            return r.json()
        except Exception as e:  # noqa
            last_err = e
            time.sleep(1.5 * attempt)
    # curl 兜底
    try:
        cmd = ["curl", "-s", "--max-time", str(REQUEST_TIMEOUT + 10)]
        for k, v in headers.items():
            cmd += ["-H", f"{k}: {v}"]
        cmd.append(url)
        out = subprocess.run(cmd, capture_output=True, timeout=REQUEST_TIMEOUT + 15)
        if out.returncode == 0 and out.stdout:
            return json.loads(out.stdout.decode("utf-8", errors="replace"))
    except Exception as e:  # noqa
        last_err = e
    log.warning("请求失败（含 curl 兜底）：%s | %s", url[:80], last_err)
    return {}
