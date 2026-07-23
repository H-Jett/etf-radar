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
import argparse
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


def existing_share_dates():
    """所有 series 分片里已出现过的份额日期集合（用于份额增量补缺）。"""
    dates = set()
    if not os.path.isdir(C.SERIES_DIR):
        return dates
    for code in os.listdir(C.SERIES_DIR):
        d = os.path.join(C.SERIES_DIR, code)
        if not os.path.isdir(d):
            continue
        for fn in os.listdir(d):
            if fn.endswith(".json"):
                for dt, _ in read_json(os.path.join(d, fn), {}).get("shares", []):
                    dates.add(dt)
    return dates


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
    return etfs


# ==================================================================
# 4. 价格采集（回填 close / nt_value，并写时间序列）
# ==================================================================
def collect_prices(etfs):
    log.info("采集 %d 只 ETF 价格时间序列...", len(etfs))

    def one(rec):
        kl = S.fetch_kline(rec["code"], datalen=C.PRICE_DAYS)
        time.sleep(random.uniform(0, C.SCAN_DELAY))
        if not kl:
            return rec["code"], None, []
        close = float(kl[-1]["close"])
        series = [[k["day"], float(k["close"])] for k in kl]
        return rec["code"], close, series

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

    for rec in etfs:
        close = None
        series = price_series.get(rec["code"], [])
        if series:
            close = series[-1][1]
        rec["close"] = close
        rec["nt_value"] = (rec["nt_amount"] * close) if close else None

    return price_series


def write_series_files(etfs, price_series, share_hist):
    """合并已存分片 + 本轮新数据，按年写 series/<code>/<year>.json，并回填 rec['years']。"""
    for rec in etfs:
        code = rec["code"]
        old_p, old_s = load_existing_series(code)
        for dt, v in price_series.get(code, []):
            old_p[dt] = v
        for dt, v in share_hist.get(code, []):
            old_s[dt] = v
        rec["years"] = write_series_sharded(code, old_p, old_s)
    log.info("写出 series 分片：%d 只 ETF", len(etfs))


# ==================================================================
# 5. 份额历史（上交所逐日回补，约 10 年；并发 + 增量补缺）
# ==================================================================
def backfill_share_history(etfs, trade_date, calendar_days):
    sh_codes = {r["code"] for r in etfs if r["exchange"] == "sh"}
    hist = {r["code"]: [] for r in etfs}
    if not sh_codes:
        return hist

    end = datetime.strptime(trade_date, "%Y-%m-%d").date()
    already = existing_share_dates()          # 已有份额日期 → 跳过，增量补缺
    todo = []
    for i in range(calendar_days):
        ds = (end - timedelta(days=i)).strftime("%Y-%m-%d")
        if ds not in already:
            todo.append(ds)
    log.info("回补上交所份额：%d 只 ETF，范围约 %d 天，需补 %d 天（已有 %d 天跳过）...",
             len(sh_codes), calendar_days, len(todo), len(already))

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

    for c in hist:
        hist[c].sort(key=lambda x: x[0])
    # 深交所无逐日接口：仅当前份额单点
    for r in etfs:
        if r["exchange"] == "sz" and r["total_share"]:
            hist[r["code"]].append([trade_date, r["total_share"]])
    log.info("份额回补完成，本轮新增 %d 个交易日", got)
    return hist


# ==================================================================
# 5b. 历史报告期国家队持仓（半年一个点，供持仓比例/份额走势）
# ==================================================================
def build_holder_periods(etfs, master):
    """
    爬每只国家队 ETF 的所有历史报告期十大持有人，按行业聚合成半年序列。
    产出 {periods:[...], industries:{ind:{nt_amount,nt_ratio,report_share,num_etfs}}}。
    行业分类沿用当前分类（行业属性稳定）。
    """
    from collections import defaultdict
    ind_of = {r["code"]: r["industry"] for r in etfs}
    codes = [r["code"] for r in etfs]
    log.info("爬取 %d 只国家队 ETF 的全部历史报告期持有人...", len(codes))

    # code -> {period: (nt_amount, nt_ratio, report_share)}
    per_code = {}

    def one(code):
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
        return code, out

    done = 0
    with ThreadPoolExecutor(max_workers=C.SCAN_WORKERS) as ex:
        futs = {ex.submit(one, c): c for c in codes}
        for fut in as_completed(futs):
            done += 1
            code, out = fut.result()
            per_code[code] = out
            if done % 20 == 0 or done == len(codes):
                log.info("  报告期进度 %d/%d", done, len(codes))

    all_periods = sorted({p for m in per_code.values() for p in m})
    ind_series = {}
    for code, per in per_code.items():
        ind = ind_of.get(code, "其他主题")
        b = ind_series.setdefault(ind, {p: [0.0, 0.0, 0] for p in all_periods})
        for p, (amt, rat, rshare) in per.items():
            b[p][0] += amt            # Σ 国家队份额
            b[p][1] += rat * amt      # 份额加权占比分子
            b[p][2] += 1              # ETF 计数（此处借第3位暂存，稍后重算）
    # 需要 report_share 汇总来算加权占比分母；重扫一遍
    ind_rshare = {}
    for code, per in per_code.items():
        ind = ind_of.get(code, "其他主题")
        b = ind_rshare.setdefault(ind, {p: 0.0 for p in all_periods})
        for p, (amt, rat, rshare) in per.items():
            b[p] += rshare

    industries = {}
    for ind, b in ind_series.items():
        nt_amount, nt_ratio, num = [], [], []
        for p in all_periods:
            amt = b[p][0]
            rshare = ind_rshare[ind][p]
            nt_amount.append(round(amt) if amt else None)
            nt_ratio.append(round(amt / rshare * 100, 2) if rshare else None)
            num.append(b[p][2] or None)
        industries[ind] = {"nt_amount": nt_amount, "nt_ratio": nt_ratio,
                           "num_etfs": num}
    log.info("历史报告期聚合完成：%d 期 × %d 行业", len(all_periods), len(industries))
    return {"periods": all_periods, "industries": industries}


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
    min_pts = max(5, int(0.5 * len(dates)))

    ind_members = defaultdict(list)
    for r in etfs:
        if len(share_map.get(r["code"], {})) >= min_pts:
            ind_members[r["industry"]].append(r)

    out = {}
    for ind, members in ind_members.items():
        ts_share = []
        for d in dates:
            tot = 0.0
            has = False
            for r in members:
                sh = share_map.get(r["code"], {}).get(d)
                if sh is not None:
                    tot += sh; has = True
            ts_share.append(round(tot) if has else None)
        out[ind] = {"total_share": ts_share, "num_etfs": len(members),
                    "codes": [r["code"] for r in members]}
    return {"dates": dates, "industries": out}


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
def run_full(args):
    t0 = time.time()
    log.info("===== 全量采集开始 %s =====", now_cn())
    trade_date, master = build_etf_master()
    if not master:
        log.error("未获取到 ETF 基础数据，终止")
        return

    candidates = find_candidates(list(master.keys()))
    etfs = collect_nt_etfs(candidates, master)
    if not etfs:
        log.error("未发现国家队 ETF，终止")
        return

    price_series = collect_prices(etfs)
    share_hist = backfill_share_history(etfs, trade_date, C.SHARE_BACKFILL_CALENDAR_DAYS) \
        if not args.no_backfill else {r["code"]: [] for r in etfs}
    holder_periods = None if args.no_holder_history else build_holder_periods(etfs, master)

    industries = aggregate_industries(etfs)
    _write_all(trade_date, etfs, industries, price_series, share_hist, holder_periods)
    log.info("===== 全量采集完成，用时 %.1fs，国家队 ETF %d 只、行业 %d 个 =====",
             time.time() - t0, len(etfs), len(industries))


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


def run_daily(args):
    """日更：复用已有 universe，仅刷新份额/价格并重新聚合；发现新报告期则升级全量。"""
    t0 = time.time()
    log.info("===== 日更采集开始 %s =====", now_cn())
    universe = read_json(os.path.join(C.DATA_DIR, "universe.json"))
    old_etfs = read_json(os.path.join(C.DATA_DIR, "etfs.json"))
    old_meta = read_json(os.path.join(C.DATA_DIR, "meta.json"), {})
    if not universe or not old_etfs:
        log.warning("无 universe.json / etfs.json，回退全量")
        return run_full(args)

    # 半年报/年报检测：发布新报告期则自动升级为全量
    if not args.no_report_check and \
            newer_report_available(universe, (old_meta or {}).get("report_date")):
        return run_full(args)

    trade_date, master = build_etf_master()
    etf_map = {r["code"]: r for r in old_etfs}
    for code, r in etf_map.items():
        if code in master:
            r["total_share"] = master[code]["share"]

    etfs = list(etf_map.values())
    price_series = collect_prices(etfs)
    # 从既有分片读回全量份额历史，并追加最新一日
    share_hist = {}
    for r in etfs:
        _, old_s = load_existing_series(r["code"])
        if r["total_share"]:
            old_s[trade_date] = r["total_share"]
        share_hist[r["code"]] = sorted(([d, v] for d, v in old_s.items()),
                                       key=lambda x: x[0])

    industries = aggregate_industries(etfs)
    # 日更不重建历史报告期（半年才变），保留既有 holders/periods.json
    _write_all(trade_date, etfs, industries, price_series, share_hist, holder_periods=None)
    log.info("===== 日更完成，用时 %.1fs =====", time.time() - t0)


def _write_all(trade_date, etfs, industries, price_series, share_hist,
               holder_periods=None):
    from collections import Counter
    etfs.sort(key=lambda x: -(x["nt_value"] or x["nt_amount"]))

    # 分片写逐 ETF 序列（合并已存 + 本轮），回填 rec['years']
    write_series_files(etfs, price_series, share_hist)
    # 行业日频总份额 -> 按年分片
    ts = build_industry_timeseries(etfs, price_series, share_hist)
    trend_years = write_trends_sharded(ts)
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


def main():
    global log
    log = setup_logging()
    ap = argparse.ArgumentParser(description="行业国家队 ETF 数据采集")
    ap.add_argument("--full", action="store_true", help="全量扫描重建 universe")
    ap.add_argument("--no-backfill", action="store_true",
                    help="全量时跳过份额历史回补（更快）")
    ap.add_argument("--no-report-check", action="store_true",
                    help="日更时跳过新报告期检测")
    ap.add_argument("--no-holder-history", action="store_true",
                    help="全量时跳过历史报告期持有人爬取")
    args = ap.parse_args()
    try:
        if args.full:
            run_full(args)
        else:
            run_daily(args)
    except KeyboardInterrupt:
        log.warning("用户中断")


if __name__ == "__main__":
    main()
