# -*- coding: utf-8 -*-
"""
初始化 / 全量采集入口 —— 首次建库，或需要往前补更久历史时运行。

用法:
    python init.py                      # 从 config.DEFAULT_START_DATE 起（默认 2016-01-01）
    python init.py --start 2018-01-01   # 指定起始日
    python init.py --no-holder-history  # 跳过历史报告期持有人（更快，仅调试用）

特性（容错 / 兼容已有数据）:
    - 幂等：可在已有数据上重复运行；份额按日期增量补缺、收盘价按日期合并、
      分片合并已存，绝不重复或弄乱历史（相同内容不会产生新提交）。
    - 保护：若拉不到全市场基础数据或未发现国家队 ETF，直接终止且**不写盘**，
      不会破坏既有数据。
    - 退出码：成功 0 / 逻辑失败 1 / 参数错误 2 / 中断 130。
"""
import os
import sys
import argparse
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import collect  # noqa: E402


def main():
    log = collect.init_logging()
    ap = argparse.ArgumentParser(description="初始化/全量采集（可指定起始日）")
    ap.add_argument("--start", default=None,
                    help="起始日 YYYY-MM-DD（默认 config.DEFAULT_START_DATE）")
    ap.add_argument("--no-holder-history", action="store_true",
                    help="跳过历史报告期持有人爬取")
    ap.add_argument("--deep-history", action="store_true",
                    help="深度历史回补：扫描全市场历史报告期，补齐已退出但仍上市的历史国家队 ETF")
    args = ap.parse_args()

    if args.deep_history:
        try:
            ok = collect.run_deep_history()
            sys.exit(0 if ok else 1)
        except KeyboardInterrupt:
            log.warning("用户中断"); sys.exit(130)
        except Exception as e:  # noqa
            collect.write_run_error("深度回补异常：%s: %s" % (type(e).__name__, e))
            log.exception("深度回补异常"); sys.exit(1)

    if args.start:
        try:
            datetime.strptime(args.start, "%Y-%m-%d")
        except ValueError:
            log.error("--start 格式应为 YYYY-MM-DD，例如 2016-01-01")
            sys.exit(2)

    try:
        ok = collect.run_init(start_date=args.start,
                              no_holder_history=args.no_holder_history)
        sys.exit(0 if ok else 1)
    except KeyboardInterrupt:
        log.warning("用户中断")
        sys.exit(130)
    except Exception as e:  # noqa
        collect.write_run_error("初始化异常：%s: %s" % (type(e).__name__, e))
        log.exception("初始化异常")
        sys.exit(1)


if __name__ == "__main__":
    main()
