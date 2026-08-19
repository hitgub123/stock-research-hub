import datetime
from monitor import in_push_window, is_us_dst, us_open_jst, run_monitor, close_summary, format_source_label
from firestore_store import StockStore


def dt(y, mo, d, h, mi=0):
    return datetime.datetime(y, mo, d, h, mi)


class FakeClient:
    def __init__(self, docs):
        self._col = FakeCol(docs)

    def collection(self, n):
        return self._col


class FakeRef:
    def __init__(self, col, t):
        self.col = col
        self.t = t

    def get(self):
        d = self.col.docs.get(self.t)
        class S:
            exists = d is not None
            def to_dict(self): return d or {}
        return S()

    def set(self, d, merge=True):
        cur = dict(self.col.docs.get(self.t, {}))
        cur.update(d)
        self.col.docs[self.t] = cur


class FakeCol:
    def __init__(self, docs):
        self.docs = dict(docs)

    def stream(self):
        class _D:
            def __init__(self, id_, d):
                self.id = id_
                self._d = d
            def to_dict(self):
                return self._d
        return [_D(k, v) for k, v in self.docs.items()]

    def document(self, t):
        return FakeRef(self, t)


def make_store(docs=None):
    return StockStore(client=FakeClient(docs or {}))


def test_format_source_label():
    assert "双报告" in format_source_label("skill")
    assert "脚本计算" in format_source_label("script")
    assert "研报复核" in format_source_label("manual")


def test_is_us_dst():
    assert is_us_dst(datetime.date(2026, 7, 1)) is True
    assert is_us_dst(datetime.date(2026, 1, 1)) is False
    assert is_us_dst(datetime.date(2026, 3, 15)) is True   # 2026-03-08 之后
    assert is_us_dst(datetime.date(2026, 11, 2)) is False  # 2026-11-01 结束


def test_us_open_jst_summer_winter():
    assert us_open_jst(datetime.date(2026, 7, 1)) == dt(2026, 7, 1, 22, 30)
    assert us_open_jst(datetime.date(2026, 1, 1)) == dt(2026, 1, 1, 23, 30)


def test_push_window_summer():
    # 夏季 2026-07-15(周三) 开盘 22:30 → 窗口 22:00-23:59
    assert in_push_window(dt(2026, 7, 15, 22, 0)) is True
    assert in_push_window(dt(2026, 7, 15, 21, 59)) is False
    assert in_push_window(dt(2026, 7, 15, 23, 59)) is True


def test_push_window_winter_and_weekend():
    assert in_push_window(dt(2026, 1, 15, 23, 0)) is True   # 冬季开盘 23:30, 23:00 进窗口
    assert in_push_window(dt(2026, 1, 15, 22, 30)) is False
    assert in_push_window(dt(2026, 7, 18, 22, 30)) is False  # 周六


def test_run_triggers_and_cooldown():
    store = make_store({"AAPL": {"target": 200.0, "source": "script"}, "TME": {"target": 10.0, "source": "skill"}})
    sent = []
    r = run_monitor(store, "hook", dt(2026, 7, 15, 22, 30),
                    fetch=lambda t: 180.0 if t == "AAPL" else 8.0,
                    send=lambda *a, **k: sent.append(a[2]) or True)
    assert r["triggered"] == 2 and r["pushed"] == 2
    assert sent and "AAPL" in sent[0] and "TME" in sent[0]
    assert "脚本计算" in sent[0]
    assert "双报告" in sent[0]
    assert "last_notified" in store.get("AAPL")

    # 冷却: 3 天后再跑 → 不推
    r2 = run_monitor(store, "hook", dt(2026, 7, 18, 22, 30),
                     fetch=lambda t: 170.0 if t == "AAPL" else 7.0, send=lambda *a, **k: sent.append(a[2]) or True)
    assert r2["triggered"] == 0 and len(sent) == 1

    # 8 天后 → 再推
    r3 = run_monitor(store, "hook", dt(2026, 7, 23, 22, 30),
                     fetch=lambda t: 150.0 if t == "AAPL" else 8.0, send=lambda *a, **k: sent.append(a[2]) or True)
    assert r3["triggered"] == 2


def test_run_above_target_no_push():
    store = make_store({"AAPL": {"target": 200.0}})
    sent = []
    run_monitor(store, "hook", dt(2026, 7, 15, 22, 30),
                fetch=lambda t: 250.0, send=lambda *a, **k: sent.append(1) or True)
    assert sent == []


def test_run_no_target_skipped():
    store = make_store({"NOPE": {"target": None, "source": "none"}})
    sent = []
    r = run_monitor(store, "hook", dt(2026, 7, 15, 22, 30),
                    fetch=lambda t: 1.0, send=lambda *a, **k: sent.append(1) or True)
    assert r["checked"] == 0 and sent == []


def test_run_outside_window():
    store = make_store({"AAPL": {"target": 200.0}})
    r = run_monitor(store, "hook", dt(2026, 7, 15, 21, 0),
                    fetch=lambda t: 100.0, send=lambda *a, **k: None)
    assert r["window"] is False and r["checked"] == 0


def test_close_summary():
    store = make_store({"AAPL": {"target": 200.0, "source": "script"}, "MSFT": {"target": 400.0, "source": "skill"}})
    sent = []
    r = close_summary(store, "hook", dt(2026, 7, 15, 5, 0),
                      fetch=lambda t: {"AAPL": 180.0, "MSFT": 410.0}[t],
                      send=lambda *a, **k: sent.append(a[2]) or True)
    assert r["sent"] and r["triggered"] == 1 and r["near"] == 1
    assert "AAPL" in sent[0] and "MSFT" in sent[0]
    assert "脚本计算" in sent[0]
    assert "双报告" in sent[0]
