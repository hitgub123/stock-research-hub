# gen_targets.py — 每周六: 重算 source=script 的目标价 → Firestore
# 与 monitor 解耦: monitor 只读 Firestore, 不知道目标价怎么算的。公式改动只改这里。
import datetime

from firestore_store import StockStore

CONSENSUS_DISCOUNT = 0.75
MA50_DISCOUNT = 0.95


def compute_target(price, consensus, ma50, ma200):
    """目标价 = min(共识×0.75, 200MA, 50MA×0.95)。缺的用其余；全缺返回 None。"""
    cands = []
    if consensus:
        cands.append(consensus * CONSENSUS_DISCOUNT)
    if ma200:
        cands.append(ma200)
    if ma50:
        cands.append(ma50 * MA50_DISCOUNT)
    return min(cands) if cands else None


def fetch_series(ticker):
    """yfinance 拉 (price, consensus, ma50, ma200)。可被测试替换。"""
    import yfinance as yf
    t = yf.Ticker(ticker)
    price = t.fast_info.last_price
    consensus = (t.analyst_price_targets or {}).get("mean")
    hist = t.history(period="1y")
    closes = hist["Close"]
    ma50 = float(closes.rolling(50).mean().iloc[-1]) if len(closes) >= 50 else None
    ma200 = float(closes.rolling(200).mean().iloc[-1]) if len(closes) >= 200 else None
    return price, consensus, ma50, ma200


def _now():
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def gen_targets(store, fetch=fetch_series):
    """重算所有 source=script 的股票目标价. 返回 {updated, failed}"""
    updated, failed = 0, []
    for ticker, data in store.get_all().items():
        if data.get("source") != "script":
            continue
        try:
            price, consensus, ma50, ma200 = fetch(ticker)
            target = compute_target(price, consensus, ma50, ma200)
            if target is None:
                failed.append(ticker)
                continue
            store.upsert(ticker, {
                "target": target, "source": "script",
                "last_price": price, "updated_at": _now(),
            })
            updated += 1
        except Exception:
            failed.append(ticker)
    return {"updated": updated, "failed": failed}
