from gen_targets import compute_target, gen_targets
from firestore_store import StockStore


def test_compute_min_of_formula():
    # 共识×0.75=150, 200MA=180, 50MA×0.95=171 → min=150
    assert compute_target(200, 200, 180, 180) == 150.0

def test_compute_missing_components():
    assert compute_target(100, None, 90, None) == 90 * 0.95      # 只有 50MA
    assert compute_target(100, 120, None, None) == 90.0          # 只有共识
    assert compute_target(100, None, None, None) is None         # 全缺

def test_compute_consensus_floor():
    # 共识×0.75 是硬地板, 即使均线更高也取它
    assert compute_target(100, 100, 200, 300) == 75.0

def test_gen_targets_updates_only_script():
    class FakeClient:
        def __init__(self): self._col = FakeCol()
        def collection(self, n): return self._col
    class FakeRef:
        def __init__(self, col, t): self.col=col; self.t=t
        def set(self, d, merge=True):
            cur = dict(self.col.docs.get(self.t, {})); cur.update(d); self.col.docs[self.t]=cur
    class FakeCol:
        def __init__(self): self.docs={
            "AAPL": {"source": "script"},
            "MSFT": {"source": "script"},
            "TME": {"source": "skill", "target": 7.0},
        }
        def stream(self):
            class _D:
                def __init__(self, id_, d): self.id=id_; self._d=d
                def to_dict(self): return self._d
            return [_D(k, v) for k, v in self.docs.items()]
        def document(self, t): return FakeRef(self, t)

    def fake_fetch(ticker):
        return {"AAPL": (300, 320, 280, 260), "MSFT": (500, 480, 450, 430)}[ticker]

    store = StockStore(client=FakeClient())
    r = gen_targets(store, fetch=fake_fetch)
    assert r["updated"] == 2 and r["failed"] == []
    assert store.col.docs["AAPL"]["target"] == 240.0   # min(共识×0.75=240, 200MA=260, 50MA×0.95=266) = 240
    assert store.col.docs["AAPL"]["source"] == "script"
    assert store.col.docs["TME"]["target"] == 7.0      # skill 来源未动
    assert "last_price" in store.col.docs["AAPL"]      # 更新了现价
    assert "updated_at" in store.col.docs["AAPL"]
