# -*- coding: utf-8 -*-
"""个股国家队持仓 —— 每日增量入口（只补最近报告期；季度更新，无既有数据自动回退全量）。

用法:
    python stock_daily.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import collect_stock as CS  # noqa: E402


def main():
    log = CS.setup_logging()
    try:
        ok = CS.run_daily()
        sys.exit(0 if ok else 1)
    except KeyboardInterrupt:
        log.warning("用户中断"); sys.exit(130)


if __name__ == "__main__":
    main()
