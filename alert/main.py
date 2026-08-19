# main.py — Cloud Functions 入口汇聚
# 重新导出各模块的 HTTP 入口函数供 GCP Cloud Functions 调用
from monitor import monitor, close
from gen_targets import gen_targets_http

__all__ = ["monitor", "close", "gen_targets_http"]
