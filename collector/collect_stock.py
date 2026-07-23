# -*- coding: utf-8 -*-
"""
个股国家队持仓采集（镜像 ETF 思路，季度口径）。

数据全部来自东方财富 datacenter（十大流通股东，含 HOLDER_MARKET_CAP 市值直给；
所属行业 BOARD_TYPE='行业' 主级+多级标签）。无价格抓取、无 push2his 限流风险。

产物 docs/data/stock/：
  meta.json / industries.json / stocks.json / universe.json
  holders/periods.json          各行业 持仓市值/占比 报告期(季度)序列（点位准确）
  holders/stock/<code>.json     每股分期持仓（永久留存 → 点位准确聚合）

入口见 stock_init.py（全量，可 --start）与 stock_daily.py（增量：只补最新报告期）。
"""
import os
import sys
import time
import logging
from datetime import datetime, timedelta, timezone
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import config as C
import sources as S
from config import is_nt_holder_stock, normalize_nt_group_stock
from industry import INDUSTRY_ORDER  # 复用行业展示顺序（个股主行业多为其中或东财行业名）
from collect import write_json, read_json  # 纯 IO 复用

CN_TZ = timezone(timedelta(hours=8))
log = logging.getLogger("collect_stock")


def setup_logging():
    os.makedirs(C.LOG_DIR, exist_ok=True)
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%H:%M:%S",
        handlers=[logging.StreamHandler(sys.stdout),
                  logging.FileHandler(os.path.join(C.LOG_DIR, "collect_stock.log"),
                                      encoding="utf-8")])
    global log
    log = logging.getLogger("collect_stock")
    return log


def now_cn():
    return datetime.now(CN_TZ).strftime("%Y-%m-%d %H:%M:%S")


def quarter_ends(start_date):
    """从 start_date 到今天(北京)的标准季度末列表（升序）。"""
    start = datetime.strptime(start_date, "%Y-%m-%d").date()
    today = datetime.now(CN_TZ).date()
    ends = []
    for y in range(start.year, today.year + 1):
        for md in ("03-31", "06-30", "09-30", "12-31"):
            d = datetime.strptime("%d-%s" % (y, md), "%Y-%m-%d").date()
            if start <= d <= today:
                ends.append(d.strftime("%Y-%m-%d"))
    return ends


# ------------------------------------------------------------------
# 行业分类缓存
# ------------------------------------------------------------------
def load_industry_cache():
    return read_json(C.STOCK_INDUSTRY_CACHE, {}) or {}


def ensure_industries(codes, cache):
    """为缺失的代码抓行业(datacenter),写回缓存。返回 cache。"""
    missing = [c for c in codes if c not in cache]
    if not missing:
        return cache
    log.info("抓取 %d 只个股行业分类...", len(missing))
    done = 0
    with ThreadPoolExecutor(max_workers=C.SCAN_WORKERS) as ex:
        futs = {ex.submit(S.fetch_stock_industry, c): c for c in missing}
        for fut in futs:
            code = futs[fut]
            try:
                primary, inds = fut.result()
            except Exception:  # noqa
                primary, inds = None, []
            cache[code] = {"primary": primary or "其他", "industries": inds or []}
            done += 1
            if done % 100 == 0 or done == len(missing):
                log.info("  行业进度 %d/%d", done, len(missing))
    write_json(C.STOCK_INDUSTRY_CACHE, cache)
    return cache


# ------------------------------------------------------------------
# 每股分期持仓 持久化 / 读取
# ------------------------------------------------------------------
def persist_stock(code, name, primary, industries, periods):
    """periods: {date: {"mv","num","ratio","holders":[...]}}。永久留存。"""
    write_json(os.path.join(C.STOCK_HOLDERS_STK_DIR, "%s.json" % code), {
        "code": code, "name": name, "industry": primary, "industries": industries,
        "periods": periods,
    }, quiet=True)


def load_all_stock_records():
    out = {}
    if not os.path.isdir(C.STOCK_HOLDERS_STK_DIR):
        return out
    for fn in os.listdir(C.STOCK_HOLDERS_STK_DIR):
        if fn.endswith(".json"):
            rec = read_json(os.path.join(C.STOCK_HOLDERS_STK_DIR, fn), {})
            if rec.get("code"):
                out[rec["code"]] = rec
    return out


def collect_top10(end_date):
    """拉某期全市场十大流通股东(不筛国家队)，按股票分组、每股按占比降序取前 10。
    返回 {code: [{holder,num,ratio,mv,is_nt,group}...]}。用于个股详情展示完整十大流通股东。"""
    recs = S.fetch_stock_holders_period(end_date, lambda n: True)
    by = defaultdict(list)
    for r in recs:
        by[r["code"]].append(r)
    out = {}
    for code, rs in by.items():
        rs.sort(key=lambda x: -x["ratio"])
        rows = []
        for i, r in enumerate(rs[:10], 1):
            nt = is_nt_holder_stock(r["holder"])
            rows.append({"rank": i, "holder": r["holder"], "num": r["num"],
                         "ratio": r["ratio"], "mv": r["mv"], "is_nt": nt,
                         "group": normalize_nt_group_stock(r["holder"]) if nt else ""})
        out[code] = rows
    log.info("  十大流通股东：覆盖 %d 只个股(报告期 %s)", len(out), end_date)
    return out


def _aggregate_period(recs):
    """把一只股某期的国家队持有人记录聚合成 {mv,num,ratio,holders[]}。"""
    holders = []
    mv = num = ratio = 0.0
    for r in recs:
        holders.append({
            "holder": r["holder"], "group": normalize_nt_group_stock(r["holder"]),
            "num": r["num"], "ratio": r["ratio"], "mv": r["mv"], "change": r.get("change"),
        })
        mv += r["mv"]; num += r["num"]; ratio += r["ratio"]
    holders.sort(key=lambda x: -x["mv"])
    return {"mv": round(mv), "num": round(num), "ratio": round(ratio, 4), "holders": holders}


# ------------------------------------------------------------------
# 采集 + 持久化：拉给定报告期，合并进各股历史
# ------------------------------------------------------------------
def collect_periods(periods):
    """逐期拉全市场十大流通股东、筛国家队，合并进已持久化的每股历史。返回涉及的 code 集合。"""
    existing = load_all_stock_records()
    # code -> {name, periods:{date:{...}}}
    store = {c: {"name": r.get("name", ""), "industries": r.get("industries", []),
                 "primary": r.get("industry"), "periods": dict(r.get("periods", {}))}
             for c, r in existing.items()}
    touched = set()
    for end in periods:
        recs = S.fetch_stock_holders_period(end, is_nt_holder_stock)
        by_code = defaultdict(list)
        for r in recs:
            by_code[r["code"]].append(r)
        log.info("  报告期 %s：国家队记录 %d 条、个股 %d 只", end, len(recs), len(by_code))
        for code, rs in by_code.items():
            e = store.setdefault(code, {"name": rs[0]["name"], "industries": [],
                                        "primary": None, "periods": {}})
            e["name"] = rs[0]["name"] or e["name"]
            e["periods"][end] = _aggregate_period(rs)
            touched.add(code)
    return store, touched


# ------------------------------------------------------------------
# 聚合 + 写盘
# ------------------------------------------------------------------
MIN_PERIOD_STOCKS = 200   # 报告期"成熟"阈值：季报陆续披露，未达标视为未披露完整，不作为最新快照


def _valid_periods(store, periods):
    """各报告期国家队个股计数 ≥ 阈值才算成熟(新季度披露未完成的稀疏期排除)。"""
    from collections import Counter
    pc = Counter()
    for e in store.values():
        for d in e["periods"]:
            pc[d] += 1
    return [p for p in periods if pc.get(p, 0) >= MIN_PERIOD_STOCKS]


def _write_all(store, cache, periods, fetch_top10=True):
    valid = _valid_periods(store, periods)
    if not valid:
        log.error("无成熟报告期(均未披露完整)，终止")
        return
    latest = valid[-1]
    prev_period = valid[-2] if len(valid) >= 2 else None
    valid_set = set(valid)
    log.info("成熟报告期 %d 个，最新快照期 = %s（上一期 = %s）", len(valid), latest, prev_period)
    # 补行业(所有出现过的股都要,供点位聚合)
    cache = ensure_industries(list(store.keys()), cache)
    # 完整十大流通股东（最新期，含非国家队；供个股详情展示）
    top10 = collect_top10(latest) if fetch_top10 else {}

    # 持久化每股 + 组装 stocks/universe
    stocks = []
    universe = []
    for code, e in store.items():
        ind = cache.get(code, {})
        primary = ind.get("primary") or e.get("primary") or "其他"
        industries = ind.get("industries") or e.get("industries") or [primary]
        persist_stock(code, e["name"], primary, industries, e["periods"])
        if latest not in e["periods"]:
            continue  # 最新期不在 → 已退出，仅历史保留，不进当前快照/universe
        cur = e["periods"][latest]
        pdates = sorted(d for d in e["periods"] if d < latest)
        prev = e["periods"][pdates[-1]] if pdates else None
        stocks.append({
            "code": code, "name": e["name"], "industry": primary, "industries": industries,
            "mv": cur["mv"], "num": cur["num"], "ratio": cur["ratio"],
            "mv_prev": prev["mv"] if prev else None,
            "report_date": latest, "prev_report_date": pdates[-1] if pdates else None,
            "holders": cur["holders"], "top10": top10.get(code, []),
        })
        universe.append({"code": code, "name": e["name"], "industry": primary,
                         "industries": industries})
    stocks.sort(key=lambda x: -x["mv"])

    # 行业快照聚合（按主行业）
    by_ind = {}
    for s in stocks:
        b = by_ind.setdefault(s["industry"], {"industry": s["industry"], "num_stocks": 0,
                                              "mv": 0.0, "mv_prev": 0.0, "_rw": 0.0,
                                              "groups": {}, "_gd": {}, "stocks": [],
                                              "new_entries": 0})
        b["num_stocks"] += 1
        b["mv"] += s["mv"]
        b["mv_prev"] += s["mv_prev"] or 0.0
        b["_rw"] += s["ratio"] * s["mv"]
        if s["mv_prev"] is None:
            b["new_entries"] += 1
        for h in s["holders"]:
            g = h["group"] or "其他"
            b["groups"][g] = b["groups"].get(g, 0.0) + h["mv"]
            cell = b["_gd"].setdefault(g, [0.0, 0.0, 0.0])  # [num, mv, mv_prev]
            cell[0] += h["num"]; cell[1] += h["mv"]
        # 上一报告期同机构市值(供"较上期")：读该股持久化的 prev_period 持有人
        if prev_period:
            pp = store.get(s["code"], {}).get("periods", {}).get(prev_period)
            if pp:
                for h in pp.get("holders", []):
                    g = h.get("group") or "其他"
                    b["_gd"].setdefault(g, [0.0, 0.0, 0.0])[2] += h.get("mv", 0)
        b["stocks"].append({"code": s["code"], "name": s["name"], "mv": s["mv"],
                            "ratio": s["ratio"], "is_new": s["mv_prev"] is None})
    industries = []
    for ind, b in by_ind.items():
        b["ratio"] = round(b["_rw"] / b["mv"], 2) if b["mv"] else 0.0
        b.pop("_rw", None)
        b["mv_change"] = b["mv"] - b["mv_prev"]
        b["mv_change_pct"] = round(b["mv_change"] / b["mv_prev"], 4) if b["mv_prev"] else None
        b["stocks"].sort(key=lambda x: -x["mv"])
        b["groups"] = dict(sorted(b["groups"].items(), key=lambda kv: -kv[1]))
        # group_detail：本行业各国家队机构 持股数/市值/上期市值/占本行业国家队市值比 + 较上期
        gd = []
        for g, (num, mv, mvp) in b.pop("_gd").items():
            gd.append({"group": g, "num": round(num), "mv": round(mv),
                       "mv_prev": round(mvp) if prev_period else None,
                       "ratio": round(mv / b["mv"] * 100, 2) if b["mv"] else 0.0,
                       "mv_change": round(mv - mvp) if prev_period else None})
        gd.sort(key=lambda x: -x["mv"])
        b["group_detail"] = gd
        b["prev_report_date"] = prev_period
        b["order"] = INDUSTRY_ORDER.index(ind) if ind in INDUSTRY_ORDER else 999
        industries.append(b)
    industries.sort(key=lambda x: -x["mv"])

    # 全局"国家队资金"统计：各资金(机构组) 总持仓市值/占比/覆盖个股数/较上期
    gstats = {}
    for s in stocks:
        seen_g = set()
        for h in s["holders"]:
            g = h["group"] or "其他"
            c = gstats.setdefault(g, {"group": g, "mv": 0.0, "num": 0.0,
                                      "mv_prev": 0.0, "num_stocks": 0})
            c["mv"] += h["mv"]; c["num"] += h["num"]
            if g not in seen_g:
                c["num_stocks"] += 1; seen_g.add(g)
    if prev_period:
        for code, e in store.items():
            pp = e["periods"].get(prev_period)
            if not pp:
                continue
            for h in pp.get("holders", []):
                g = h.get("group") or "其他"
                if g in gstats:
                    gstats[g]["mv_prev"] += h.get("mv", 0)
    total_mv0 = sum(s["mv"] for s in stocks) or 1
    nt_group_stats = []
    for g, c in gstats.items():
        mv = round(c["mv"])
        nt_group_stats.append({
            "group": g, "mv": mv, "num": round(c["num"]),
            "num_stocks": c["num_stocks"],
            "ratio": round(c["mv"] / total_mv0 * 100, 2),
            "mv_prev": round(c["mv_prev"]) if prev_period else None,
            "mv_change": round(c["mv"] - c["mv_prev"]) if prev_period else None,
        })
    nt_group_stats.sort(key=lambda x: -x["mv"])

    # 报告期序列（点位准确：读全部持久化记录，每期按当期实际持有的股 + 其主行业聚合）
    hp = _build_holder_periods(cache, valid_set)

    # 写盘
    total_mv = sum(s["mv"] for s in stocks)
    inst_groups = sorted({normalize_nt_group_stock(h["holder"])
                          for s in stocks for h in s["holders"]} - {""})
    meta = {
        "generated_at": now_cn(),
        "report_date": latest,
        "prev_report_date": prev_period,
        "num_stocks": len(stocks),
        "num_industries": len(industries),
        "total_mv": total_mv,
        "periods": hp["periods"],
        "industry_order": INDUSTRY_ORDER,
        "nt_groups": inst_groups,
        "nt_group_stats": nt_group_stats,
        "nt_keywords": C.STOCK_NT_KEYWORDS,
    }
    write_json(os.path.join(C.STOCK_DATA_DIR, "meta.json"), meta)
    write_json(os.path.join(C.STOCK_DATA_DIR, "industries.json"), industries)
    write_json(os.path.join(C.STOCK_DATA_DIR, "stocks.json"), stocks)
    write_json(os.path.join(C.STOCK_DATA_DIR, "universe.json"), universe)
    write_json(os.path.join(C.STOCK_HOLDERS_DIR, "periods.json"), hp)
    log.info("写盘完成：个股 %d 只、行业 %d 个、报告期 %d、总市值 %.0f 亿",
             len(stocks), len(industries), len(hp["periods"]), total_mv / 1e8)


def _build_holder_periods(cache, valid_set=None):
    """从全部持久化每股记录做点位准确聚合：各行业(主) 每报告期 持仓市值 + 市值加权占比。
    valid_set 给定时只保留成熟报告期(排除季报未披露完整的稀疏期)。"""
    agg = defaultdict(lambda: defaultdict(lambda: [0.0, 0.0, 0]))  # ind->period->[mv, rw, cnt]
    all_periods = set()
    recs = load_all_stock_records()
    for code, rec in recs.items():
        ind = (cache.get(code, {}).get("primary") or rec.get("industry") or "其他")
        for d, per in rec.get("periods", {}).items():
            if valid_set is not None and d not in valid_set:
                continue
            all_periods.add(d)
            cell = agg[ind][d]
            cell[0] += per.get("mv", 0)
            cell[1] += per.get("ratio", 0) * per.get("mv", 0)
            cell[2] += 1
    periods = sorted(all_periods)
    industries = {}
    for ind, pmap in agg.items():
        mv, ratio, num = [], [], []
        for p in periods:
            if p in pmap:
                m, rw, cnt = pmap[p]
                mv.append(round(m) if m else None)
                ratio.append(round(rw / m, 2) if m else None)
                num.append(cnt or None)
            else:
                mv.append(None); ratio.append(None); num.append(None)
        industries[ind] = {"mv": mv, "ratio": ratio, "num_stocks": num}
    return {"periods": periods, "industries": industries}


# ------------------------------------------------------------------
# 主流程
# ------------------------------------------------------------------
def run_init(start_date=None):
    t0 = time.time()
    start_date = start_date or C.STOCK_START_DATE
    log.info("===== 个股全量采集开始 %s（起始 %s）=====", now_cn(), start_date)
    periods = quarter_ends(start_date)
    if not periods:
        log.error("无有效报告期，终止")
        return False
    store, touched = collect_periods(periods)
    if not store:
        log.error("未抓到任何国家队个股，终止（不写盘）")
        return False
    _write_all(store, load_industry_cache(), periods)
    log.info("===== 个股全量完成，用时 %.0fs =====", time.time() - t0)
    return True


def run_daily():
    """增量：只补最近两个报告期（捕捉新季报），合并进历史后重聚合。"""
    t0 = time.time()
    log.info("===== 个股增量采集开始 %s =====", now_cn())
    all_periods = quarter_ends(C.STOCK_START_DATE)
    if not all_periods:
        return False
    recent = all_periods[-2:]                    # 最近两期
    if not os.path.isdir(C.STOCK_HOLDERS_STK_DIR):
        log.info("无既有个股数据 → 回退全量")
        return run_init()
    store, touched = collect_periods(recent)
    _write_all(store, load_industry_cache(), all_periods)
    log.info("===== 个股增量完成，用时 %.0fs =====", time.time() - t0)
    return True
