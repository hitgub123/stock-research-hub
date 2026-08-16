# build_watchlist.py — 初始化/加股时: 解析 184 CSV + >100B + additions → Firestore
# 用法: python -m build_watchlist  (或 import 后调用 build())
import csv
import re
import sqlite3
import sys
import os

from firestore_store import StockStore

ROOT = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(ROOT)
CSV_PATH = os.path.join(PROJECT_ROOT, "184家_行业结论对照总表_20260815.csv")
DB_PATH = os.path.join(PROJECT_ROOT, "research.db")
ADDITIONS_PATH = os.path.join(ROOT, "watchlist_additions.yaml")
MIN_MCAP_B = 100  # >100B 大盘


def parse_buy_zone(buy):
    """提取买入区高位为触发价. 返回 (high, status).
    status ∈ ok / signal(含文字) / malformed($错位) / date(2026-11) / ambiguous(高低差>3x) / empty"""
    s = (buy or "").strip()
    if not s:
        return None, "empty"
    if re.search(r"[A-Za-z一-鿿%]", s):
        return None, "signal"
    if "$" in s[1:]:
        return None, "malformed"
    nums = [float(x) for x in re.findall(r"[\d.]+", s)]
    if not nums:
        return None, "no_number"
    if nums[0] > 1000 and (len(nums) == 1 or nums[1] < 100):
        return None, "date"
    if len(nums) == 1:
        return nums[0], "ok"
    lo, hi = min(nums), max(nums)
    if hi / lo > 3:
        return None, "ambiguous"
    return hi, "ok"


def parse_184_csv(path=CSV_PATH):
    """解析 184 CSV → ({ticker: {target, source:'skill'}}, review_list[(ticker, 买入区, status)])"""
    out, review = {}, []
    with open(path, encoding="utf-8-sig") as f:
        for r in csv.DictReader(f):
            code = (r.get("代码") or "").strip().upper()
            if not code:
                continue
            high, status = parse_buy_zone(r.get("买入区"))
            if status == "ok":
                out[code] = {"target": high, "source": "skill"}
            else:
                review.append((code, (r.get("买入区") or "").strip(), status))
    return out, review


def load_large_caps(db_path=DB_PATH, min_mcap=MIN_MCAP_B):
    """从 research.db 取 >min_mcap(B) 的 ticker (source=script, 目标价留给 gen_targets)"""
    conn = sqlite3.connect(db_path)
    rows = conn.execute(
        "SELECT ticker FROM companies WHERE market_cap_b > ?", (min_mcap,))
    out = {r[0].upper(): {"target": None, "source": "script"} for r in rows}
    conn.close()
    return out


def load_additions(path=ADDITIONS_PATH):
    """watchlist_additions.yaml → {ticker: {target, source}}. 文件缺失返回 {}."""
    if not os.path.exists(path):
        return {}
    import yaml
    data = yaml.safe_load(open(path, encoding="utf-8")) or {}
    out = {}
    for ticker, spec in data.items():
        t = ticker.upper()
        if isinstance(spec, dict):
            out[t] = {"target": spec.get("target"), "source": spec.get("source", "manual")}
        else:  # 简写: 纯数字 = 目标价
            out[t] = {"target": float(spec), "source": "manual"}
    return out


def build(csv_path=CSV_PATH, db_path=DB_PATH, additions_path=ADDITIONS_PATH,
          store=None, review_out=None):
    """合并三源写入 Firestore. 返回 {写入数, 跳过数, review清单}. store=None 用真 Firestore."""
    if store is None:
        store = StockStore()
    skill, review = parse_184_csv(csv_path)
    caps = load_large_caps(db_path)
    adds = load_additions(additions_path)
    merged = dict(skill)
    merged.update(caps)   # 大盘覆盖(若 184 里有 >100B, 但实际无重叠)
    merged.update(adds)   # 手动覆盖
    n_skip = 0
    for ticker, data in merged.items():
        if data["target"] is None and data["source"] != "manual":
            n_skip += 1  # script 无目标价 → 先写入, gen_targets 周更填
        store.upsert(ticker, data)
    if review_out is not None:
        review_out.extend(review)
    return {"written": len(merged), "skip_target": n_skip, "review": len(review)}


if __name__ == "__main__":
    r = build()
    print(f"写入 {r['written']} 只, script无目标价 {r['skip_target']}, 歧义待确认 {r['review']}")


def write_review(path, review):
    """歧义清单写入 md 供用户逐条确认(高位触发价或标跳过)"""
    lines = ["# 买入区歧义待确认（决定高位触发价，或标「跳过」）\n",
             "| 代码 | 买入区 | 状态 | 确认高位价(或跳过) |\n|---|------|------|------|\n"]
    for code, buy, status in sorted(review):
        lines.append(f"| {code} | {buy} | {status} | |\n")
    open(path, "w", encoding="utf-8").write("".join(lines))
