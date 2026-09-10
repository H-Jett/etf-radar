# -*- coding: utf-8 -*-
"""
每日增量采集入口 —— 每个交易日收盘后运行（GitHub Actions 每天调用）。

用法:
    python daily.py                    # 追加当日份额/收盘价并重新聚合
    python daily.py --no-report-check  # 跳过新报告期检测（更快）
    python daily.py --strict-source    # 源不可达且当天一次都没成功 → 以失败退出(告警)

特性（容错 / 兼容已有数据）:
    - 幂等：非交易日、或同一天重复运行，都不会产生重复数据点
      （份额/收盘价按日期去重合并，同日覆盖）。
    - 自愈：
        * 无既有 universe/etfs → 自动回退初始化 init 流程；
        * 检测到新半年报/年报报告期 → 自动升级为初始化（增量补数据、重扫持有人）。
    - 保护：拉不到当日行情时跳过本次且**不写盘**，不破坏既有数据。
    - 静音：交易所接口整体不可达属于常态抖动（CI 出口被随机反爬），按"跳过"
      正常退出，不当故障报警；当天最后一个定时槽位加 --strict-source，
      只有"当天 4 次全挂"才真失败发邮件。
    - 限时：整轮受 config.RUN_BUDGET_SEC 时间预算约束，超预算的阶段带 warning
      收工，避免被 CI 的 job 硬超时杀掉（那样连状态都落不下来）。
    - 退出码：成功/本次跳过 0 / 逻辑失败 1 / 中断 130。
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
    ap.add_argument("--strict-source", action="store_true",
                    help="源不可达且当天尚无一次成功采集时以失败退出（用于当天最后一个定时槽位告警）")
    args = ap.parse_args()
    try:
        collect.start_budget()
        ok = collect.run_daily(no_report_check=args.no_report_check)
        if ok == collect.SKIP:
            if args.strict_source and not collect.succeeded_today():
                log.error("数据源整体不可达，且今天尚无一次成功采集 → 以失败退出（触发告警）")
                sys.exit(1)
            log.warning("数据源整体不可达 → 本次跳过，正常退出（当天其余槽位会重试）")
            sys.exit(0)
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
