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
from datetime import date, timedelta

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
        d = date.today() - timedelta(days=i)
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
