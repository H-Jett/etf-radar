# -*- coding: utf-8 -*-
"""
行业国家队 ETF 看板 —— 采集主程序（产出静态 JSON，供 GitHub Pages 前端）

两种模式：
  python collect.py --full      # 全量：扫描全市场 ETF 十大持有人，重建国家队 universe
  python collect.py             # 日更：仅刷新已知 universe 的份额/价格并重新聚合

产物（写入 docs/data/）：
  meta.json          总览元信息（更新时间、报告期、统计）
  industries.json    各行业国家队持有汇总（前端首页核心）
  etfs.json          各国家队 ETF 快照（含十大持有人）
  universe.json      国家队 ETF 代码清单（供日更模式复用）
  prices/<code>.json 每只 ETF 的价格/份额时间序列（详情页图表）
"""
import os
import sys
import json
import time
import random
import logging
from datetime import datetime, date, timedelta, timezone
from concurrent.futures import ThreadPoolExecutor, as_completed

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import config as C
from config import is_nt_holder, normalize_nt_group
from industry import classify, INDUSTRY_ORDER
import sources as S

CN_TZ = timezone(timedelta(hours=8))


# ------------------------------------------------------------------
# 日志
# ------------------------------------------------------------------
def setup_logging():
    os.makedirs(C.LOG_DIR, exist_ok=True)
    logfile = os.path.join(C.LOG_DIR, "collect.log")
    fmt = "%(asctime)s [%(levelname)s] %(message)s"
    logging.basicConfig(
        level=logging.INFO, format=fmt, datefmt="%H:%M:%S",
        handlers=[logging.StreamHandler(sys.stdout),
                  logging.FileHandler(logfile, encoding="utf-8")],
    )
    return logging.getLogger("collect")


log = logging.getLogger("collect")


def init_logging():
    """入口脚本调用：配置并绑定模块级 logger。"""
    global log
    log = setup_logging()
    return log


def now_cn():
    return datetime.now(CN_TZ).strftime("%Y-%m-%d %H:%M:%S")


def write_json(path, obj, quiet=False):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, separators=(",", ":"))
    if not quiet:
        log.info("写出 %s (%d bytes)", os.path.relpath(path, C.BASE_DIR),
                 os.path.getsize(path))


def read_json(path, default=None):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return default


# ------------------------------------------------------------------
# 运行报告（当天采集摘要 / 异常，供网页状态模块展示）
# ------------------------------------------------------------------
RUN = None


def run_start(mode):
    global RUN
    RUN = {"mode": mode, "t0": time.time(), "warnings": [], "errors": [], "stats": {}}


def run_warn(msg):
    log.warning(msg)
    if RUN is not None:
        RUN["warnings"].append(msg)


def run_err(msg):
    log.error(msg)
    if RUN is not None:
        RUN["errors"].append(msg)


def write_status(trade_date, num_nt_etfs=None, num_industries=None,
                 rescan=False, old_meta=None, committed=None):
    """把本次运行摘要写入 docs/data/status.json（latest + 最近 20 次）。"""
    warnings = RUN["warnings"] if RUN else []
    errors = RUN["errors"] if RUN else []
    level = "error" if errors else ("warning" if warnings else "ok")
    old_td = (old_meta or {}).get("trade_date")
    latest = {
        "run_at": now_cn(),
        "mode": RUN["mode"] if RUN else "?",
        "status": level,
        "trade_date": trade_date,
        "is_new_trading_day": bool(trade_date and old_td and trade_date != old_td),
        "num_nt_etfs": num_nt_etfs,
        "num_industries": num_industries,
        "report_rescan": rescan,
        "duration_sec": round(time.time() - RUN["t0"], 1) if RUN else None,
        "warnings": warnings,
        "errors": errors,
        "stats": RUN["stats"] if RUN else {},
    }
    prev = read_json(os.path.join(C.DATA_DIR, "status.json"), {}) or {}
    recent = ([latest] + prev.get("recent", []))[:20]
    write_json(os.path.join(C.DATA_DIR, "status.json"),
               {"latest": latest, "recent": recent})

    # 全量运行日志：按月分片累积（供日志页分页浏览，全部保留）
    month = datetime.now(CN_TZ).strftime("%Y-%m")
    mfile = os.path.join(C.RUNS_DIR, f"{month}.json")
    marr = read_json(mfile, []) or []
    marr.append(latest)
    write_json(mfile, marr, quiet=True)
    idx = read_json(os.path.join(C.RUNS_DIR, "index.json"), {}) or {}
    months = sorted(set(idx.get("months", []) + [month]), reverse=True)
    write_json(os.path.join(C.RUNS_DIR, "index.json"),
               {"months": months, "total": idx.get("total", 0) + 1}, quiet=True)
    return level


def write_run_error(msg):
    """入口脚本捕获未预期异常时调用，落一条 error 状态（尽力而为）。"""
    if RUN is None:
        run_start("?")
    run_err(msg)
    try:
        write_status(None, old_meta=read_json(os.path.join(C.DATA_DIR, "meta.json"), {}))
    except Exception:  # noqa
        pass


# ------------------------------------------------------------------
# 时间序列分片（series/<code>/<year>.json）读写与增量
# ------------------------------------------------------------------
def _year_of(date_str):
    return date_str[:4]


def load_existing_series(code):
    """把某 ETF 已存的各年分片合并为 {'prices':{date:v}, 'shares':{date:v}}。"""
    d = os.path.join(C.SERIES_DIR, code)
    prices, shares = {}, {}
    if os.path.isdir(d):
        for fn in os.listdir(d):
            if fn.endswith(".json"):
                obj = read_json(os.path.join(d, fn), {})
                for dt, v in obj.get("prices", []):
                    prices[dt] = v
                for dt, v in obj.get("shares", []):
                    shares[dt] = v
    return prices, shares


def existing_share_dates_by_code():
    """返回 {code: set(已有份额日期)}。份额增量补缺按**每只 ETF**判断，
    避免新晋成员因日期在别的 ETF 里存在而被整体跳过（否则新成员无历史）。"""
    by_code = {}
    if not os.path.isdir(C.SERIES_DIR):
        return by_code
    for code in os.listdir(C.SERIES_DIR):
        d = os.path.join(C.SERIES_DIR, code)
        if not os.path.isdir(d):
            continue
        s = set()
        for fn in os.listdir(d):
            if fn.endswith(".json"):
                for dt, _ in read_json(os.path.join(d, fn), {}).get("shares", []):
                    s.add(dt)
        by_code[code] = s
    return by_code


def write_series_sharded(code, prices, shares):
    """把某 ETF 的 {date:close}/{date:share} 按年写成 series/<code>/<year>.json。"""
    years = {}
    for dt, v in prices.items():
        years.setdefault(_year_of(dt), {"prices": [], "shares": []})
    for dt, v in shares.items():
        years.setdefault(_year_of(dt), {"prices": [], "shares": []})
    for dt, v in sorted(prices.items()):
        years[_year_of(dt)]["prices"].append([dt, v])
    for dt, v in sorted(shares.items()):
        years[_year_of(dt)]["shares"].append([dt, v])
    for yr, obj in years.items():
        write_json(os.path.join(C.SERIES_DIR, code, f"{yr}.json"), obj, quiet=True)
    return sorted(years.keys())


# ==================================================================
# 1. 全市场 ETF 基础信息（沪 + 深）
# ==================================================================
def build_etf_master():
    """返回 (trade_date, {code: {name, exchange, index_name, share}})。"""
    trade_date, sse = S.fetch_sse_shares_latest()
    szse = S.fetch_szse_list()
    master = {}
    for code, info in sse.items():
        master[code] = {"name": info["name"], "exchange": "sh",
                        "index_name": "", "share": info["share"]}
    for code, info in szse.items():
        master[code] = {"name": info["name"], "exchange": "sz",
                        "index_name": info.get("index_name", ""),
                        "share": info["share"]}
    log.info("全市场 ETF：沪 %d + 深 %d = %d 只", len(sse), len(szse), len(master))
    if RUN is not None:
        RUN["stats"]["sse_etfs"] = len(sse)
        RUN["stats"]["szse_etfs"] = len(szse)
        RUN["stats"]["trade_date"] = trade_date
    if not sse:
        run_err("上交所份额接口无数据（当日无行情或接口不可达）")
    if not szse:
        run_warn("深交所列表接口无数据/不完整")
    return trade_date, master


# ==================================================================
# 2. 扫描国家队候选（1 次请求/ETF：报告期 + 是否含国家队关键词）
# ==================================================================
def scan_candidate(code):
    time.sleep(random.uniform(0, C.SCAN_DELAY))
    try:
        import requests
        r = requests.get(S.SINA_HOLDER_PAGE.format(code=code),
                         headers=S._SINA_HEADERS, timeout=C.REQUEST_TIMEOUT)
        r.encoding = "gbk"
        text = r.text
    except Exception:  # noqa
        return code, [], False
    dates = S._RE_OPTION.findall(text)
    hit = any(kw in text for kw in C.NT_KEYWORDS)
    return code, dates, hit


def find_candidates(codes):
    """并发扫描，返回 {code: [report_dates...]} 仅含命中国家队关键词者。"""
    candidates = {}
    done = 0
    total = len(codes)
    log.info("开始扫描 %d 只 ETF 的持有人页（识别国家队候选）...", total)
    with ThreadPoolExecutor(max_workers=C.SCAN_WORKERS) as ex:
        futs = {ex.submit(scan_candidate, c): c for c in codes}
        for fut in as_completed(futs):
            done += 1
            code, dates, hit = fut.result()
            if hit and dates:
                candidates[code] = dates
            if done % 200 == 0 or done == total:
                log.info("  扫描进度 %d/%d，累计候选 %d", done, total, len(candidates))
    log.info("候选国家队 ETF：%d 只", len(candidates))
    return candidates


# ==================================================================
# 3. 拉取候选 ETF 的十大持有人（最新期 + 上一期），构建 ETF 记录
# ==================================================================
def build_nt_etf(code, dates, master):
    latest = dates[0]
    prev = dates[1] if len(dates) > 1 else None
    holders_latest = S.fetch_holders(code, latest)
    holders_prev = S.fetch_holders(code, prev) if prev else []
    time.sleep(random.uniform(0, C.SCAN_DELAY))

    prev_ratio_map = {h["name"]: h["ratio"] for h in holders_prev}
    prev_amt_map = {h["name"]: h["amount"] for h in holders_prev}

    nt_holders = []
    nt_amount = nt_ratio = 0.0
    for h in holders_latest:
        if not is_nt_holder(h["name"]):
            continue
        pr = prev_ratio_map.get(h["name"])
        nt_holders.append({
            "name": h["name"],
            "group": normalize_nt_group(h["name"]),
            "amount": h["amount"],
            "ratio": h["ratio"],
            "prev_ratio": pr,
            "is_new": pr is None,
            "delta_ratio": (h["ratio"] - pr) if pr is not None else None,
        })
        nt_amount += h["amount"]
        nt_ratio += h["ratio"]

    if not nt_holders:
        return None  # 关键词命中但结构化后无国家队（极少数误命中）

    nt_amount_prev = sum(prev_amt_map.get(nh["name"], 0.0) for nh in nt_holders)
    nt_ratio_prev = sum((nh["prev_ratio"] or 0.0) for nh in nt_holders)

    info = master.get(code, {})
    name = info.get("name", "")
    industry = classify(name, info.get("index_name", ""))
    # 报告期总份额（由国家队占比反推，与持有人同口径）
    report_share = (nt_amount / (nt_ratio / 100)) if nt_ratio > 0 else 0.0

    return {
        "code": code,
        "name": name,
        "exchange": info.get("exchange", S.exchange_prefix(code)),
        "industry": industry,
        "index_name": info.get("index_name", ""),
        "total_share": info.get("share", 0.0),   # 今日最新份额
        "report_share": report_share,            # 报告期总份额（同口径）
        "close": None,          # 价格阶段回填
        "nt_amount": nt_amount,
        "nt_amount_prev": nt_amount_prev,
        "nt_ratio": round(nt_ratio, 4),
        "nt_ratio_prev": round(nt_ratio_prev, 4),
        "nt_value": None,       # 价格阶段回填 = nt_amount * close
        "is_new": all(nh["is_new"] for nh in nt_holders),
        "report_date": latest,
        "prev_report_date": prev,
        "nt_holders": sorted(nt_holders, key=lambda x: -x["amount"]),
        "all_holders": [
            {"rank": h["rank"], "name": h["name"], "amount": h["amount"],
             "ratio": h["ratio"], "is_nt": is_nt_holder(h["name"])}
            for h in holders_latest
        ],
    }


def collect_nt_etfs(candidates, master):
    etfs = []
    total = len(candidates)
    log.info("拉取 %d 只候选 ETF 的十大持有人明细...", total)
    with ThreadPoolExecutor(max_workers=C.SCAN_WORKERS) as ex:
        futs = {ex.submit(build_nt_etf, c, d, master): c
                for c, d in candidates.items()}
        done = 0
        for fut in as_completed(futs):
            done += 1
            try:
                rec = fut.result()
            except Exception as e:  # noqa
                rec = None
                log.warning("  %s 明细失败：%s", futs[fut], e)
            if rec:
                etfs.append(rec)
            if done % 50 == 0 or done == total:
                log.info("  明细进度 %d/%d，确认国家队 ETF %d", done, total, len(etfs))
    log.info("确认国家队 ETF：%d 只", len(etfs))
    if RUN is not None:
        RUN["stats"]["candidates"] = len(candidates)
        RUN["stats"]["nt_etfs"] = len(etfs)
    return etfs


# ==================================================================
# 4. 价格采集（回填 close / nt_value，并写时间序列）
# ==================================================================
def collect_prices(etfs, beg="0", cap_date=None):
    """采集收盘价序列（东方财富全历史，与份额同期）。beg='YYYYMMDD' 或 '0'。
    cap_date='YYYY-MM-DD' 时，只保留 ≤ 该日的收盘价（与份额日对齐，不记录更新的一天）。"""
    log.info("采集 %d 只 ETF 收盘价（东财，beg=%s，截至 %s）...", len(etfs), beg, cap_date or "最新")

    def one(rec):
        series = S.fetch_kline_em(rec["code"], rec["exchange"], beg=beg)
        if cap_date:
            series = [p for p in series if p[0] <= cap_date]   # 对齐份额日
        time.sleep(random.uniform(0, C.SCAN_DELAY))
        if not series:
            return rec["code"], None, []
        return rec["code"], series[-1][1], series

    price_series = {}
    with ThreadPoolExecutor(max_workers=C.PRICE_WORKERS) as ex:
        futs = {ex.submit(one, r): r for r in etfs}
        done = 0
        for fut in as_completed(futs):
            done += 1
            code, close, series = fut.result()
            price_series[code] = series
            if done % 30 == 0 or done == len(etfs):
                log.info("  价格进度 %d/%d", done, len(etfs))

    missing = []
    for rec in etfs:
        close = None
        series = price_series.get(rec["code"], [])
        if series:
            close = series[-1][1]
        else:
            missing.append(rec["code"])
        rec["close"] = close
        rec["nt_value"] = (rec["nt_amount"] * close) if close else None

    if RUN is not None:
        RUN["stats"]["price_ok"] = len(etfs) - len(missing)
        RUN["stats"]["price_total"] = len(etfs)
    # 部分未取到 = 收盘价接口抖动，数据仍写入(有历史兜底)，不算异常；
    # 只有“全部取不到”才判为警告(收盘价源可能整体不可用)。
    if missing and len(missing) == len(etfs):
        run_warn("收盘价接口整体不可用：%d 只 ETF 全部未取到" % len(missing))
    elif missing:
        log.info("  %d 只 ETF 本次未取到收盘价(接口抖动，用历史值)", len(missing))
    return price_series


def write_series_files(etfs, price_series, share_hist, cap_date=None):
    """合并已存分片 + 本轮新数据，按年写 series/<code>/<year>.json，并回填 rec['years']。
    cap_date 时清理收盘价里晚于份额日的点（与份额对齐）。"""
    for rec in etfs:
        code = rec["code"]
        old_p, old_s = load_existing_series(code)
        for dt, v in price_series.get(code, []):
            old_p[dt] = v
        for dt, v in share_hist.get(code, []):
            old_s[dt] = v
        if cap_date:
            old_p = {dt: v for dt, v in old_p.items() if dt <= cap_date}
        rec["years"] = write_series_sharded(code, old_p, old_s)
    log.info("写出 series 分片：%d 只 ETF", len(etfs))


# ==================================================================
# 5. 份额历史（上交所逐日回补，约 10 年；并发 + 增量补缺）
# ==================================================================
def backfill_share_history(etfs, trade_date, start_date):
    """上交所逐日份额回补，从 start_date 到 trade_date；增量跳过已有日期。"""
    sh_codes = {r["code"] for r in etfs if r["exchange"] == "sh"}
    hist = {r["code"]: [] for r in etfs}
    if not sh_codes:
        return hist

    end = datetime.strptime(trade_date, "%Y-%m-%d").date()
    start = datetime.strptime(start_date, "%Y-%m-%d").date()
    by_code = existing_share_dates_by_code()          # {code: set(已有日期)}
    all_dates = set().union(*by_code.values()) if by_code else set()  # 已知交易日
    lo = min(all_dates) if all_dates else None
    hi = max(all_dates) if all_dates else None
    empty = set()
    todo = []
    d = end
    while d >= start:
        ds = d.strftime("%Y-%m-%d")
        if all_dates and lo <= ds <= hi:
            # 已覆盖区间内：只补“已知交易日且某个成员缺失”的（新成员/历史空洞），
            # 非交易日（不在 all_dates）跳过，避免重复请求周末/节假日
            if ds in all_dates and any(ds not in by_code.get(c, empty) for c in sh_codes):
                todo.append(ds)
        else:
            # 区间两端（更早/更新）：直接抓以发现新交易日
            todo.append(ds)
        d -= timedelta(days=1)
    log.info("回补上交所份额：%d 只 ETF，%s→%s，需补 %d 天（已知交易日 %d）...",
             len(sh_codes), start_date, trade_date, len(todo), len(all_dates))

    def fetch_day(ds):
        data = S.fetch_sse_shares(ds)
        if not data:
            return None
        return ds, {c: data[c]["share"] for c in sh_codes if c in data}

    got = 0
    with ThreadPoolExecutor(max_workers=C.SHARE_BACKFILL_WORKERS) as ex:
        futs = {ex.submit(fetch_day, ds): ds for ds in todo}
        done = 0
        for fut in as_completed(futs):
            done += 1
            res = fut.result()
            if res:
                got += 1
                ds, day = res
                for code, sh in day.items():
                    hist[code].append([ds, sh])
            if done % 200 == 0 or done == len(todo):
                log.info("  补缺进度 %d/%d（有效交易日 %d）", done, len(todo), got)

    # 深交所：fund.szse.cn/fund_jjgm 逐日历史份额（分窗 ≤150 自然日 + 分页 + 增量去重）
    sz_codes = [r["code"] for r in etfs if r["exchange"] == "sz"]
    if sz_codes:
        log.info("回补深交所份额：%d 只（fund_jjgm 历史逐日）...", len(sz_codes))

        def sz_one(code):
            have = by_code.get(code, set())
            pts = []
            w_end = end
            while w_end >= start:
                w_start = max(start, w_end - timedelta(days=150))
                for d, v in S.fetch_szse_shares_history(
                        code, w_start.strftime("%Y-%m-%d"), w_end.strftime("%Y-%m-%d")):
                    if d not in have:
                        pts.append([d, v])
                w_end = w_start - timedelta(days=1)
            return code, pts

        szdone = 0
        with ThreadPoolExecutor(max_workers=6) as ex:
            for code, pts in ex.map(sz_one, sz_codes):
                hist[code].extend(pts)
                szdone += 1
                if szdone % 5 == 0 or szdone == len(sz_codes):
                    log.info("  深市份额 %d/%d", szdone, len(sz_codes))

    for c in hist:
        hist[c].sort(key=lambda x: x[0])
    log.info("份额回补完成，上交所新增 %d 个交易日", got)
    if RUN is not None:
        RUN["stats"]["share_days_added"] = got
    return hist


# ==================================================================
# 5b. 历史报告期国家队持仓（半年一个点，供持仓比例/份额走势）
# ==================================================================
def _scan_nt_periods(code):
    """扫一只 ETF 的所有报告期，返回 {period: (nt_amount, nt_ratio, report_share)}。"""
    dates = S.fetch_report_dates(code)
    if C.HOLDER_HISTORY_MAX_PERIODS:
        dates = dates[:C.HOLDER_HISTORY_MAX_PERIODS]
    out = {}
    for d in dates:
        hs = S.fetch_holders(code, d)
        amt = sum(h["amount"] for h in hs if is_nt_holder(h["name"]))
        rat = sum(h["ratio"] for h in hs if is_nt_holder(h["name"]))
        if amt > 0 and rat > 0:
            out[d] = (amt, rat, amt / (rat / 100))
        time.sleep(random.uniform(0, C.SCAN_DELAY))
    return out


def _persist_etf_holder_record(code, name, industry, per):
    """持久化一只 ETF 的分期国家队持仓到 holders/etf/<code>.json（永久留存）。"""
    os.makedirs(C.HOLDERS_ETF_DIR, exist_ok=True)
    write_json(os.path.join(C.HOLDERS_ETF_DIR, f"{code}.json"), {
        "code": code, "name": name, "industry": industry,
        "periods": {d: [round(a), round(rt, 2), round(rs)]
                    for d, (a, rt, rs) in per.items()},
    }, quiet=True)


def _aggregate_periods_from_disk():
    """从**所有**已持久化记录（在册 + 已退出）做点位准确聚合。"""
    from collections import defaultdict
    agg = defaultdict(lambda: defaultdict(lambda: [0.0, 0.0, 0]))  # ind->period->[amt,rshare,cnt]
    all_periods = set()
    if os.path.isdir(C.HOLDERS_ETF_DIR):
        for fn in os.listdir(C.HOLDERS_ETF_DIR):
            if not fn.endswith(".json"):
                continue
            rec = read_json(os.path.join(C.HOLDERS_ETF_DIR, fn), {}) or {}
            ind = rec.get("industry", "其他主题")
            for d, vals in rec.get("periods", {}).items():
                amt, _rat, rshare = vals
                all_periods.add(d)
                cell = agg[ind][d]
                cell[0] += amt; cell[1] += rshare; cell[2] += 1
    periods = sorted(all_periods)
    industries = {}
    for ind, pmap in agg.items():
        nt_amount, nt_ratio, num = [], [], []
        for p in periods:
            if p in pmap:
                amt, rshare, cnt = pmap[p]
                nt_amount.append(round(amt) if amt else None)
                nt_ratio.append(round(amt / rshare * 100, 2) if rshare else None)
                num.append(cnt or None)
            else:
                nt_amount.append(None); nt_ratio.append(None); num.append(None)
        industries[ind] = {"nt_amount": nt_amount, "nt_ratio": nt_ratio,
                           "num_etfs": num}
    return {"periods": periods, "industries": industries}


def build_holder_periods(etfs):
    """
    爬当前国家队 ETF 的所有历史报告期持有人，持久化后做点位准确聚合。
    产出 {periods:[...], industries:{ind:{nt_amount,nt_ratio,num_etfs}}}。
    """
    ind_of = {r["code"]: r["industry"] for r in etfs}
    name_of = {r["code"]: r["name"] for r in etfs}
    codes = [r["code"] for r in etfs]
    log.info("爬取 %d 只国家队 ETF 的全部历史报告期持有人...", len(codes))
    done = 0
    with ThreadPoolExecutor(max_workers=C.SCAN_WORKERS) as ex:
        futs = {ex.submit(_scan_nt_periods, c): c for c in codes}
        for fut in as_completed(futs):
            done += 1
            code = futs[fut]
            try:
                per = fut.result()
            except Exception:  # noqa
                per = {}
            if per:
                _persist_etf_holder_record(code, name_of.get(code, ""),
                                           ind_of.get(code, "其他主题"), per)
            if done % 20 == 0 or done == len(codes):
                log.info("  报告期进度 %d/%d", done, len(codes))
    hp = _aggregate_periods_from_disk()
    log.info("历史报告期聚合(点位准确,含已退出):%d 期 × %d 行业",
             len(hp["periods"]), len(hp["industries"]))
    return hp


def run_deep_history():
    """
    深度历史回补：扫描**全市场** ETF 的历史报告期，把"曾经是国家队、现已退出但
    仍上市"的 ETF 历史持仓也补进 holders/etf/，再重建 holders/periods.json。
    一次性操作，耗时较长（全市场 × 各报告期）。
    """
    t0 = time.time()
    run_start("deep")
    log.info("===== 深度历史回补（全市场历史国家队 ETF）%s =====", now_cn())
    trade_date, master = build_etf_master()
    if not master or not trade_date:
        run_err("无法获取全市场列表，终止（不写盘）")
        write_status(trade_date)
        return False
    codes = list(master.keys())
    log.info("扫描全市场 %d 只 ETF 的历史报告期国家队持仓（较慢，请耐心）...", len(codes))
    done = found = 0
    with ThreadPoolExecutor(max_workers=C.SCAN_WORKERS) as ex:
        futs = {ex.submit(_scan_nt_periods, c): c for c in codes}
        for fut in as_completed(futs):
            done += 1
            code = futs[fut]
            try:
                per = fut.result()
            except Exception:  # noqa
                per = {}
            if per:
                info = master.get(code, {})
                ind = classify(info.get("name", ""), info.get("index_name", ""))
                _persist_etf_holder_record(code, info.get("name", ""), ind, per)
                found += 1
            if done % 100 == 0 or done == len(codes):
                log.info("  全市场扫描 %d/%d，累计历史国家队 ETF %d 只", done, len(codes), found)
    hp = _aggregate_periods_from_disk()
    write_json(os.path.join(C.HOLDERS_DIR, "periods.json"), hp)
    RUN["stats"]["deep_found"] = found
    RUN["stats"]["deep_scanned"] = len(codes)
    write_status(trade_date, num_industries=len(hp["industries"]))
    log.info("===== 深度回补完成，用时 %.0fs：历史国家队 ETF %d 只、%d 期 =====",
             time.time() - t0, found, len(hp["periods"]))
    return True


# ==================================================================
# 6. 聚合：按行业汇总国家队持有
# ==================================================================
def build_industry_timeseries(etfs, price_series, share_hist):
    """
    各行业「总份额」日频时间序列（供行业走势页做日/周/月切换 + 拖动）。

    只做**总份额**这一真实日频量：total_share = 行业成员 ETF 当日总份额之和。
    国家队「持仓份额 / 占比」是报告期口径（半年一个点），单独由
    build_holder_periods() 产出，不在日频里伪造。

    仅纳入有足够份额历史的成员（主要是上交所 ETF；深交所无逐日份额接口）。
    """
    from collections import defaultdict
    share_map = {c: dict(s) for c, s in share_hist.items()}

    all_dates = set()
    for s in share_map.values():
        all_dates.update(s.keys())
    dates = sorted(all_dates)
    if not dates:
        return {"dates": [], "industries": {}, "note": "暂无份额历史"}

    MIN_DAILY = 5   # 有逐日份额历史(≥5点)才计入总线；深市"仅当前1点"的成员排除，避免末日跳变
    ind_members = defaultdict(list)
    for r in etfs:
        if len(share_map.get(r["code"], {})) >= 1:   # 含任一点即算该行业成员(行业集合一致)
            ind_members[r["industry"]].append(r)

    out = {}
    for ind, members in ind_members.items():
        daily = [r for r in members if len(share_map.get(r["code"], {})) >= MIN_DAILY]
        ts_share = []
        for d in dates:
            tot = 0.0
            has = False
            for r in daily:                          # 只累加有逐日历史的成员
                sh = share_map.get(r["code"], {}).get(d)
                if sh is not None:
                    tot += sh; has = True
            ts_share.append(round(tot) if has else None)
        out[ind] = {"total_share": ts_share, "num_etfs": len(members),
                    "daily_members": len(daily), "codes": [r["code"] for r in members]}
    return {"dates": dates, "industries": out}


def build_industry_bundles(etfs, share_hist):
    """
    每个行业按年打包成 industry/<id>/<year>.json，含该行业各成员 ETF 的日频份额
    与合计总量，供「行业详情」页一次拉取（避免逐 ETF 分片几百个请求）。
    另写 industry/index.json 供前端建行业选择框与 名称->id 映射。
    """
    from collections import defaultdict
    share_map = {c: dict(s) for c, s in share_hist.items()}
    members = defaultdict(list)
    for r in etfs:
        if len(share_map.get(r["code"], {})) >= 1:      # 含任一份额点即纳入（口径统一）
            members[r["industry"]].append(r)

    # 行业 id 稳定：从既有 index.json 读 name->id 注册表，已有行业沿用旧 id，
    # 新行业分配一个从未用过的新 id（不复用、不删旧文件 → 换名/新增都兼容，
    # 且不会因成员集合变化导致 id 整体漂移、把所有行业文件重写一遍）。
    prev_index = read_json(os.path.join(C.INDUSTRY_DIR, "index.json"), {}) or {}
    name2id = {x["name"]: x["id"] for x in prev_index.get("industries", [])}
    next_id = (max(name2id.values()) + 1) if name2id else 0

    def ord_key(ind):
        return INDUSTRY_ORDER.index(ind) if ind in INDUSTRY_ORDER else 999
    inds = sorted(members.keys(), key=lambda x: (ord_key(x), x))   # 显示顺序
    index = {"industries": []}
    for ind in inds:
        if ind in name2id:
            iid = name2id[ind]
        else:
            iid = next_id
            next_id += 1
            name2id[ind] = iid
        mem = members[ind]
        # 只有逐日历史(≥5点)的成员计入总线；深市单点成员仍作为个股线展示,不进总量(避免跳变)
        daily = [r for r in mem if len(share_map[r["code"]]) >= 5]
        all_dates = sorted({d for r in mem for d in share_map[r["code"]]})
        years = sorted({d[:4] for d in all_dates})
        index["industries"].append({
            "id": iid, "name": ind, "num_etfs": len(mem),
            "codes": [r["code"] for r in mem], "years": years,
        })
        by_year = defaultdict(list)
        for d in all_dates:
            by_year[d[:4]].append(d)
        for yr, dts in by_year.items():
            etf_series = [{"code": r["code"], "name": r["name"],
                           "shares": [share_map[r["code"]].get(d) for d in dts]}
                          for r in mem]
            total = []
            for d in dts:
                t = 0.0; has = False
                for r in daily:
                    v = share_map[r["code"]].get(d)
                    if v is not None:
                        t += v; has = True
                total.append(round(t) if has else None)
            write_json(os.path.join(C.INDUSTRY_DIR, str(iid), f"{yr}.json"),
                       {"industry": ind, "dates": dts, "total": total,
                        "etfs": etf_series}, quiet=True)
    write_json(os.path.join(C.INDUSTRY_DIR, "index.json"), index)
    log.info("写出 industry 打包：%d 个行业（id 稳定复用）", len(inds))


def write_trends_sharded(ts):
    """把行业日频总份额按年份写成 trends/<year>.json。返回涉及的年份列表。"""
    dates = ts.get("dates", [])
    if not dates:
        return []
    idx_by_year = {}
    for i, d in enumerate(dates):
        idx_by_year.setdefault(_year_of(d), []).append(i)
    for yr, idxs in idx_by_year.items():
        obj = {"dates": [dates[i] for i in idxs], "industries": {}}
        for ind, s in ts["industries"].items():
            obj["industries"][ind] = {
                "total_share": [s["total_share"][i] for i in idxs],
                "num_etfs": s["num_etfs"],
            }
        write_json(os.path.join(C.TRENDS_DIR, f"{yr}.json"), obj, quiet=True)
    log.info("写出 trends 分片：%d 个年份", len(idx_by_year))
    return sorted(idx_by_year.keys())


def aggregate_industries(etfs):
    by_ind = {}
    for r in etfs:
        ind = r["industry"]
        b = by_ind.setdefault(ind, {
            "industry": ind, "num_etfs": 0,
            "nt_amount": 0.0, "nt_amount_prev": 0.0, "nt_value": 0.0,
            "total_share": 0.0, "report_share": 0.0, "_ratio_w": 0.0,
            "groups": {}, "etfs": [], "new_entries": 0,
        })
        b["num_etfs"] += 1
        b["nt_amount"] += r["nt_amount"]
        b["nt_amount_prev"] += r["nt_amount_prev"]
        b["nt_value"] += r["nt_value"] or 0.0
        b["total_share"] += r["total_share"] or 0.0
        b["report_share"] += r.get("report_share", 0.0)
        b["_ratio_w"] += r["nt_ratio"] * r["nt_amount"]   # 份额加权
        if r["is_new"]:
            b["new_entries"] += 1
        for nh in r["nt_holders"]:
            g = nh["group"] or "其他"
            b["groups"][g] = b["groups"].get(g, 0.0) + nh["amount"]
        b["etfs"].append({
            "code": r["code"], "name": r["name"],
            "nt_amount": r["nt_amount"], "nt_value": r["nt_value"],
            "nt_ratio": r["nt_ratio"], "total_share": r["total_share"],
            "close": r["close"], "is_new": r["is_new"],
        })

    industries = []
    for ind, b in by_ind.items():
        # 行业国家队平均持有占比 = 各 ETF 报告期占比按国家队持有份额加权
        b["nt_ratio"] = round(b["_ratio_w"] / b["nt_amount"], 2) if b["nt_amount"] else 0.0
        b.pop("_ratio_w", None)
        chg = (b["nt_amount"] - b["nt_amount_prev"])
        b["amount_change"] = chg
        b["amount_change_pct"] = round(chg / b["nt_amount_prev"], 4) if b["nt_amount_prev"] else None
        b["etfs"].sort(key=lambda x: -(x["nt_value"] or x["nt_amount"]))
        b["groups"] = dict(sorted(b["groups"].items(), key=lambda kv: -kv[1]))
        b["order"] = INDUSTRY_ORDER.index(ind) if ind in INDUSTRY_ORDER else 999
        industries.append(b)

    industries.sort(key=lambda x: -(x["nt_value"] or x["nt_amount"]))
    return industries


# ==================================================================
# 7. 主流程
# ==================================================================
def run_init(start_date=None, no_holder_history=False):
    """
    初始化 / 全量：扫描全市场识别国家队 ETF，回补自 start_date 起的份额与收盘价、
    历史报告期持有人，重建全部快照与分片。

    幂等且兼容已有数据：份额按日期增量补缺、收盘价按日期合并、分片合并已存，
    不会重复或覆盖历史（相同内容不会产生新提交）。
    """
    t0 = time.time()
    run_start("init")
    old_meta = read_json(os.path.join(C.DATA_DIR, "meta.json"), {})
    start_date = start_date or C.DEFAULT_START_DATE
    beg = start_date.replace("-", "")
    log.info("===== 初始化/全量采集开始 %s（起始 %s）=====", now_cn(), start_date)
    trade_date, master = build_etf_master()
    if not master or not trade_date:
        run_err("未获取到 ETF 基础数据或交易日（trade_date=%s），终止（不写盘，保护既有数据）"
                % trade_date)
        write_status(trade_date, old_meta=old_meta)
        return False

    candidates = find_candidates(list(master.keys()))
    etfs = collect_nt_etfs(candidates, master)
    if not etfs:
        run_err("未发现国家队 ETF，终止（不写盘）")
        write_status(trade_date, old_meta=old_meta)
        return False

    price_series = collect_prices(etfs, beg=beg, cap_date=trade_date)
    # 收盘价兜底：本轮未取到的（如接口限流），用已存分片里 ≤trade_date 的最新价，
    # 避免 close/nt_value 变 None（保护首页市值）
    for r in etfs:
        if r.get("close") is None:
            p, _ = load_existing_series(r["code"])
            pts = sorted(d for d in p if d <= trade_date)
            if pts:
                r["close"] = p[pts[-1]]
                r["nt_value"] = (r["nt_amount"] * r["close"]) if r.get("nt_amount") else None
    share_hist = backfill_share_history(etfs, trade_date, start_date)
    holder_periods = None if no_holder_history else build_holder_periods(etfs)

    industries = aggregate_industries(etfs)
    _write_all(trade_date, etfs, industries, price_series, share_hist, holder_periods)
    write_status(trade_date, num_nt_etfs=len(etfs), num_industries=len(industries),
                 rescan=True, old_meta=old_meta)
    log.info("===== 初始化完成，用时 %.1fs，国家队 ETF %d 只、行业 %d 个 =====",
             time.time() - t0, len(etfs), len(industries))
    return True


def newer_report_available(universe, known_report_date):
    """抽查若干国家队 ETF 的最新报告期，若出现比已知更新的报告期则返回 True。"""
    if not known_report_date:
        return True
    from collections import Counter
    sample = [u["code"] for u in universe[:20]]
    log.info("检测半年报/年报是否发布：抽查 %d 只 ETF 的最新报告期 ...", len(sample))
    latest_dates = []
    with ThreadPoolExecutor(max_workers=C.SCAN_WORKERS) as ex:
        for dates in ex.map(S.fetch_report_dates, sample):
            if dates:
                latest_dates.append(dates[0])
    if not latest_dates:
        log.warning("未取到报告期，保守起见不触发全量")
        return False
    # 用众数判断，避免个别 ETF 的特殊披露日造成误触发
    mode_date = Counter(latest_dates).most_common(1)[0][0]
    if mode_date > known_report_date:
        log.info("发现新报告期 %s（原 %s，%d/%d 只 ETF 已更新）→ 触发全量重扫",
                 mode_date, known_report_date,
                 sum(1 for d in latest_dates if d >= mode_date), len(latest_dates))
        return True
    log.info("最新报告期众数仍为 %s，无需重扫持有人", known_report_date)
    return False


def run_daily(no_report_check=False):
    """
    每日增量：复用已有 universe，追加当日份额/收盘价并重新聚合。
    - 无既有数据 → 自动回退初始化；
    - 检测到新报告期 → 自动升级为初始化（重扫持有人、增量补数据）；
    - 幂等：非交易日/重复运行不会产生重复点（按日期去重合并）。
    """
    t0 = time.time()
    run_start("daily")
    log.info("===== 每日增量采集开始 %s =====", now_cn())
    universe = read_json(os.path.join(C.DATA_DIR, "universe.json"))
    old_etfs = read_json(os.path.join(C.DATA_DIR, "etfs.json"))
    old_meta = read_json(os.path.join(C.DATA_DIR, "meta.json"), {})
    if not universe or not old_etfs:
        log.warning("无 universe.json / etfs.json → 回退初始化")
        return run_init()

    if not no_report_check and \
            newer_report_available(universe, (old_meta or {}).get("report_date")):
        log.info("检测到新报告期 → 升级为初始化（增量补数据、重扫持有人）")
        return run_init()

    trade_date, master = build_etf_master()
    if not master or not trade_date:
        run_err("未获取到当日行情或交易日（trade_date=%s）→ 跳过本次（不写盘，保护既有数据）"
                % trade_date)
        write_status(trade_date, old_meta=old_meta)
        return False
    if old_meta.get("trade_date") == trade_date and RUN is not None:
        RUN["stats"]["no_new_trading_day"] = True  # 非交易日/份额未更新（正常）
    etf_map = {r["code"]: r for r in old_etfs}
    for code, r in etf_map.items():
        if code in master:
            r["total_share"] = master[code]["share"]

    etfs = list(etf_map.values())
    # 收盘价：近 60 天增量（东财，对齐份额日）
    beg = (datetime.strptime(trade_date, "%Y-%m-%d").date()
           - timedelta(days=60)).strftime("%Y%m%d")
    new_prices = collect_prices(etfs, beg=beg, cap_date=trade_date)
    # 份额：近 25 天增量（上交所逐日 + 深交所 fund_jjgm，统一走 backfill，自动去重）
    recent_start = (datetime.strptime(trade_date, "%Y-%m-%d").date()
                    - timedelta(days=25)).strftime("%Y-%m-%d")
    new_shares = backfill_share_history(etfs, trade_date, recent_start)
    price_series, share_hist = {}, {}
    for r in etfs:
        old_p, old_s = load_existing_series(r["code"])
        for d, v in new_prices.get(r["code"], []):
            old_p[d] = v                    # 收盘价按日期合并去重
        for d, v in new_shares.get(r["code"], []):
            old_s[d] = v                    # 份额按日期合并去重
        # 收盘价对齐份额日：不保留晚于 trade_date 的点
        price_series[r["code"]] = sorted(([d, v] for d, v in old_p.items() if d <= trade_date),
                                         key=lambda x: x[0])
        share_hist[r["code"]] = sorted(([d, v] for d, v in old_s.items()), key=lambda x: x[0])
        # 回填最新收盘价 / nt_value
        if price_series[r["code"]]:
            r["close"] = price_series[r["code"]][-1][1]
            r["nt_value"] = (r["nt_amount"] * r["close"]) if r["close"] else None

    industries = aggregate_industries(etfs)
    _write_all(trade_date, etfs, industries, price_series, share_hist, holder_periods=None)
    write_status(trade_date, num_nt_etfs=len(etfs), num_industries=len(industries),
                 rescan=False, old_meta=old_meta)
    log.info("===== 每日增量完成，用时 %.1fs =====", time.time() - t0)
    return True


def _write_all(trade_date, etfs, industries, price_series, share_hist,
               holder_periods=None):
    from collections import Counter
    etfs.sort(key=lambda x: -(x["nt_value"] or x["nt_amount"]))

    # 分片写逐 ETF 序列（合并已存 + 本轮），收盘价对齐到份额日 trade_date
    write_series_files(etfs, price_series, share_hist, cap_date=trade_date)
    # 从合并后的分片读回**完整**序列，供行业聚合（增量模式下入参只含新数据，
    # 必须用合并后的全量，否则行业时间序列/打包会缺历史）
    full_price, full_share = {}, {}
    for r in etfs:
        p, s = load_existing_series(r["code"])
        full_price[r["code"]] = sorted(([d, v] for d, v in p.items()), key=lambda x: x[0])
        full_share[r["code"]] = sorted(([d, v] for d, v in s.items()), key=lambda x: x[0])
    # 行业日频总份额 -> 按年分片
    ts = build_industry_timeseries(etfs, full_price, full_share)
    trend_years = write_trends_sharded(ts)
    # 行业内各 ETF 份额打包（行业详情页用）
    build_industry_bundles(etfs, full_share)
    series_years = sorted({y for r in etfs for y in r.get("years", [])})

    rc = Counter(r["report_date"] for r in etfs if r["report_date"])
    pc = Counter(r["prev_report_date"] for r in etfs if r["prev_report_date"])
    report_dates = [rc.most_common(1)[0][0]] if rc else []
    prev_dates = [pc.most_common(1)[0][0]] if pc else []
    total_value = sum(r["nt_value"] or 0 for r in etfs)
    total_amount = sum(r["nt_amount"] for r in etfs)
    meta = {
        "generated_at": now_cn(),
        "generated_at_iso": datetime.now(CN_TZ).isoformat(),
        "trade_date": trade_date,
        "report_date": report_dates[0] if report_dates else None,
        "prev_report_date": prev_dates[0] if prev_dates else None,
        "num_nt_etfs": len(etfs),
        "num_industries": len(industries),
        "total_nt_amount": total_amount,
        "total_nt_value": total_value,
        "series_years": series_years or trend_years,
        "industry_order": INDUSTRY_ORDER,   # 供前端按行业名稳定取色（跨页一致）
        "nt_keywords": C.NT_KEYWORDS,
        "nt_groups": [g for g, _ in C.NT_GROUPS],
    }
    if holder_periods is not None:
        meta["holder_periods"] = holder_periods.get("periods", [])
        write_json(os.path.join(C.HOLDERS_DIR, "periods.json"), holder_periods)
    else:
        # 保留既有报告期序列，meta 沿用其 periods
        prev = read_json(os.path.join(C.HOLDERS_DIR, "periods.json"), {})
        meta["holder_periods"] = prev.get("periods", [])

    write_json(os.path.join(C.DATA_DIR, "meta.json"), meta)
    write_json(os.path.join(C.DATA_DIR, "industries.json"), industries)
    write_json(os.path.join(C.DATA_DIR, "etfs.json"), etfs)
    write_json(os.path.join(C.DATA_DIR, "universe.json"),
               [{"code": r["code"], "name": r["name"], "industry": r["industry"],
                 "exchange": r["exchange"], "years": r.get("years", [])}
                for r in etfs])


# 入口见 init.py（初始化，可指定 --start）与 daily.py（每日增量）。
# collect.py 仅作为共享库，提供 run_init() / run_daily() 及各采集函数。
