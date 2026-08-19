# monitor.py — 每日监控: 读 Firestore → yfinance 拉价 → 现价<目标价且7天未推 → Discord
# 与 gen_targets 解耦: 只读 Firestore 的目标价, 不管怎么来的。
import datetime
import json
import os

from firestore_store import StockStore

COOLDOWN_DAYS = 7
OPEN_BEFORE_MIN = 30  # 开盘前 30 分开始推


def is_us_dst(day):
    """3月第2周日 ~ 11月第1周日 为 EDT(UTC-4), 其余 EST(UTC-5)"""
    mar = datetime.date(day.year, 3, 1)
    second_sun_mar = mar + datetime.timedelta(days=(13 - mar.weekday()) % 7)
    nov = datetime.date(day.year, 11, 1)
    first_sun_nov = nov + datetime.timedelta(days=(6 - nov.weekday()) % 7)
    return second_sun_mar <= day < first_sun_nov


def us_open_jst(day):
    """美股开盘 JST: EDT=22:30, EST=23:30"""
    hour = 22 if is_us_dst(day) else 23
    return datetime.datetime(day.year, day.month, day.day, hour, 30)


def in_push_window(now):
    """now: JST datetime. 推送窗口=[开盘-30分, 当日23:59]. 周末不推."""
    if now.weekday() >= 5:
        return False
    start = us_open_jst(now.date()) - datetime.timedelta(minutes=OPEN_BEFORE_MIN)
    end = now.replace(hour=23, minute=59, second=59, microsecond=0)
    return start <= now <= end


def _cooled(last_notified, now):
    """距上次推送 >= 7 天才能再推"""
    if not last_notified:
        return True
    try:
        last = datetime.datetime.fromisoformat(last_notified)
        return (now - last).days >= COOLDOWN_DAYS
    except Exception:
        return True


def default_fetch(ticker):
    """yfinance 拉现价。可被测试替换。"""
    import yfinance as yf
    return float(yf.Ticker(ticker).fast_info.last_price)


def _webhook():
    if "DISCORD_WEBHOOK" in os.environ and os.environ["DISCORD_WEBHOOK"]:
        return os.environ["DISCORD_WEBHOOK"]
    from google.auth import default
    from google.cloud import secretmanager
    _, project = default()
    client = secretmanager.SecretManagerServiceClient()
    name = f"projects/{project or 'stock-alert'}/secrets/stock-discord-webhook/versions/latest"
    return client.access_secret_version(name=name).payload.data.decode("utf-8")


def notify_alerts(webhook, triggered, send=None):
    """一条 Discord 消息列出所有触发股票"""
    if send is None:
        import requests
        def _s(url, title, desc):
            try:
                return requests.post(url, json={"content": f"**{title}**\n{desc}"}, timeout=10).status_code in (200, 204)
            except Exception:
                return False
        send = _s
    lines = [f"{t} 现价{price:.2f} < 目标{target:.2f}({source})" for t, price, target, source in triggered]
    return send(webhook, f"🐂 {len(triggered)} 只触及买入区", "\n".join(lines))


def run_monitor(store, webhook, now, fetch=None, send=None):
    """窗口内: 拉价 → 现价<目标价且冷却过 → 推送. 返回统计."""
    if fetch is None:
        fetch = default_fetch
    if not in_push_window(now):
        return {"checked": 0, "triggered": 0, "pushed": 0, "window": False}
    triggered, checked = [], 0
    for ticker, data in store.get_all().items():
        target = data.get("target")
        if not target:
            continue
        checked += 1
        try:
            price = fetch(ticker)
        except Exception:
            continue
        store.upsert(ticker, {"last_price": price})
        if price is not None and price < target and _cooled(data.get("last_notified"), now):
            triggered.append((ticker, price, float(target), data.get("source")))
    pushed = 0
    if triggered:
        if notify_alerts(webhook, triggered, send=send):
            pushed = len(triggered)
        for t, *_ in triggered:
            store.upsert(t, {"last_notified": now.isoformat()})
    return {"checked": checked, "triggered": len(triggered), "pushed": pushed, "window": True}


def close_summary(store, webhook, now, fetch=None, send=None, last_summary_key=None):
    """收盘总结(每日一次): 触发清单 + 接近目标(≤目标×1.05)按偏离排序"""
    if fetch is None:
        fetch = default_fetch
    day = now.strftime("%Y-%m-%d")
    if last_summary_key is not None and last_summary_key() == day:
        return {"sent": False, "reason": "already_sent"}
    near, triggered = [], []
    for ticker, data in store.get_all().items():
        target = data.get("target")
        if not target:
            continue
        try:
            price = fetch(ticker)
        except Exception:
            continue
        store.upsert(ticker, {"last_price": price})
        if price is None:
            continue
        if price < target:
            triggered.append((ticker, price, float(target)))
        elif price <= target * 1.05:
            near.append((ticker, price, float(target), price / float(target)))
    near.sort(key=lambda x: x[3])
    if send is None:
        import requests
        def _s(url, title, desc):
            try:
                return requests.post(url, json={"content": f"**{title}**\n{desc}"}, timeout=10).status_code in (200, 204)
            except Exception:
                return False
        send = _s
    lines = [f"触及: {t} {p:.2f} < {target:.2f}" for t, p, target in triggered]
    lines += [f"接近: {t} {p:.2f}({target:.2f}) {r*100:.0f}%" for t, p, target, r in near]
    send(webhook, f"📊 收盘总结 {day}", "\n".join(lines) or "今日无触发/接近")
    if last_summary_key is not None:
        last_summary_key(day)
    return {"sent": True, "triggered": len(triggered), "near": len(near)}


_JST = datetime.timezone(datetime.timedelta(hours=9))


def monitor(request):
    """窗口 job 入口: 现价<目标价且冷却过 → 推 Discord"""
    store = StockStore()
    now = datetime.datetime.now(_JST)
    stat = run_monitor(store, _webhook(), now)
    print(f"monitor {stat}", flush=True)
    return json.dumps(stat), 200


def close(request):
    """收盘 job 入口: 每日一次收盘总结"""
    store = StockStore()
    now = datetime.datetime.now(_JST)
    day = now.strftime("%Y-%m-%d")
    if (store.get("_meta") or {}).get("last_summary_date") == day:
        return json.dumps({"sent": False, "reason": "already"}), 200
    stat = close_summary(store, _webhook(), now)
    store.upsert("_meta", {"last_summary_date": day})
    print(f"close {stat}", flush=True)
    return json.dumps(stat), 200
