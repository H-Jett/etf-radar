# -*- coding: utf-8 -*-
"""个股国家队持仓 —— 初始化/全量入口。

用法:
    python stock_init.py                    # 从 config.STOCK_START_DATE 起
    python stock_init.py --start 2020-01-01 # 指定起始报告期年份
"""
import os
import sys
import argparse
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import collect_stock as CS  # noqa: E402


def main():
    log = CS.setup_logging()
    ap = argparse.ArgumentParser(description="个股国家队持仓 初始化/全量")
    ap.add_argument("--start", default=None, help="起始日 YYYY-MM-DD（默认 config.STOCK_START_DATE）")
    args = ap.parse_args()
    if args.start:
        try:
            datetime.strptime(args.start, "%Y-%m-%d")
        except ValueError:
            log.error("--start 格式应为 YYYY-MM-DD"); sys.exit(2)
    try:
        ok = CS.run_init(start_date=args.start)
        sys.exit(0 if ok else 1)
    except KeyboardInterrupt:
        log.warning("用户中断"); sys.exit(130)


if __name__ == "__main__":
    main()
