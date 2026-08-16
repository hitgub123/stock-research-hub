from firestore_store import StockStore


class FakeSnapshot:
    def __init__(self, data):
        self.exists = data is not None
        self._d = data or {}
    def to_dict(self):
        return self._d

class FakeRef:
    def __init__(self, col, ticker):
        self.col = col; self.ticker = ticker
    def get(self):
        return FakeSnapshot(self.col.docs.get(self.ticker))
    def set(self, data, merge=True):
        cur = dict(self.col.docs.get(self.ticker, {}))
        cur.update(data)
        self.col.docs[self.ticker] = cur

class FakeCol:
    def __init__(self):
        self.docs = {}
    def stream(self):
        class _D:
            def __init__(self, id_, data): self.id = id_; self._d = data
            def to_dict(self): return self._d
        return [_D(k, v) for k, v in self.docs.items()]
    def document(self, ticker):
        return FakeRef(self, ticker)

class FakeClient:
    def __init__(self):
        self._col = FakeCol()
    def collection(self, name):
        return self._col

def _store():
    return StockStore(client=FakeClient())

def test_upsert_and_get():
    s = _store()
    s.upsert("AAPL", {"target": 200.0, "source": "skill"})
    d = s.get("AAPL")
    assert d["target"] == 200.0 and d["source"] == "skill"

def test_upsert_merge():
    s = _store()
    s.upsert("MSFT", {"target": 400.0})
    s.upsert("MSFT", {"last_price": 500.0})   # merge 不覆盖已有字段
    d = s.get("MSFT")
    assert d["target"] == 400.0 and d["last_price"] == 500.0

def test_get_missing_returns_none():
    assert _store().get("NOPE") is None

def test_get_all():
    s = _store()
    s.upsert("AAPL", {"target": 200.0})
    s.upsert("MSFT", {"target": 400.0})
    all_ = s.get_all()
    assert set(all_) == {"AAPL", "MSFT"}
    assert all_["AAPL"]["target"] == 200.0
