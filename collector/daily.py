# -*- coding: utf-8 -*-
"""
每日增量采集入口 —— 每个交易日收盘后运行（GitHub Actions 每天调用）。

用法:
    python daily.py                    # 追加当日份额/收盘价并重新聚合
    python daily.py --no-report-check  # 跳过新报告期检测（更快）

特性（容错 / 兼容已有数据）:
    - 幂等：非交易日、或同一天重复运行，都不会产生重复数据点
      （份额/收盘价按日期去重合并，同日覆盖）。
    - 自愈：
        * 无既有 universe/etfs → 自动回退初始化 init 流程；
        * 检测到新半年报/年报报告期 → 自动升级为初始化（增量补数据、重扫持有人）。
    - 保护：拉不到当日行情时跳过本次且**不写盘**，不破坏既有数据。
    - 退出码：成功 0 / 逻辑失败 1 / 中断 130。
"""
import os
import sys
import argparse

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import collect  # noqa: E402


def main():
    log = collect.init_logging()
    ap = argparse.ArgumentParser(description="每日增量采集")
    ap.add_argument("--no-report-check", action="store_true",
                    help="跳过新报告期检测")
    args = ap.parse_args()
    try:
        ok = collect.run_daily(no_report_check=args.no_report_check)
        sys.exit(0 if ok else 1)
    except KeyboardInterrupt:
        log.warning("用户中断")
        sys.exit(130)
    except Exception as e:  # noqa
        collect.write_run_error("每日采集异常：%s: %s" % (type(e).__name__, e))
        log.exception("每日采集异常")
        sys.exit(1)


if __name__ == "__main__":
    main()
