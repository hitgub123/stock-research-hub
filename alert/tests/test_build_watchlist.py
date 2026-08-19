from build_watchlist import (
    parse_buy_zone, parse_184_csv, load_large_caps, load_additions,
    load_overrides, build, CSV_PATH
)


def test_parse_clean_ranges():
    assert parse_buy_zone("$7-7.5") == (7.5, "ok")
    assert parse_buy_zone("$250-270") == (270, "ok")
    assert parse_buy_zone("108–125") == (125, "ok")
    assert parse_buy_zone("2-3") == (3, "ok")
    assert parse_buy_zone("$44-47") == (47, "ok")


def test_parse_signal():
    assert parse_buy_zone("等信号：宽带净流失<1%")[1] == "signal"
    assert parse_buy_zone("$100以下开始建仓")[1] == "signal"


def test_parse_malformed():
    assert parse_buy_zone("530-$1")[1] == "malformed"
    assert parse_buy_zone("100–$2")[1] == "malformed"


def test_parse_ambiguous():
    assert parse_buy_zone("600-2")[1] == "ambiguous"   # 高低差>3x


def test_parse_date_and_empty():
    assert parse_buy_zone("2026-11")[1] == "date"
    assert parse_buy_zone("")[1] == "empty"


def test_parse_184_real_csv():
    out, review = parse_184_csv(CSV_PATH)
    assert len(out) + len(review) >= 184   # 全部覆盖
    print(f"  干净解析 {len(out)}, 歧义待确认 {len(review)}")
    assert all(v["source"] == "skill" for v in out.values())


def test_build_merges_sources(tmp_path):
    from firestore_store import StockStore
    class FakeClient:
        def __init__(self): self._col = FakeCol()
        def collection(self, n): return self._col
    class FakeRef:
        def __init__(self, col, t): self.col=col; self.t=t
        def set(self, d, merge=True):
            cur = dict(self.col.docs.get(self.t, {})); cur.update(d); self.col.docs[self.t]=cur
    class FakeCol:
        def __init__(self): self.docs={}
        def document(self, t): return FakeRef(self, t)

    add = tmp_path / "add.yaml"
    add.write_text("LDOS: 100.0\nZETA:\n  target: null\n  source: script\n", encoding="utf-8")
    over = tmp_path / "over.yaml"
    over.write_text("AAPL: 150.0\nTME: 5.5\n", encoding="utf-8")

    store = StockStore(client=FakeClient())
    r = build(additions_path=str(add), overrides_path=str(over), store=store)
    assert r["written"] > 200
    assert "LDOS" in store.col.docs and store.col.docs["LDOS"]["target"] == 100.0
    assert store.col.docs["LDOS"]["source"] == "manual"
    assert "ZETA" in store.col.docs and store.col.docs["ZETA"]["source"] == "script"
    # overrides 最高优先级覆盖了 184 的 TME 和 >100B 的 AAPL
    assert store.col.docs["TME"]["target"] == 5.5
    assert store.col.docs["TME"]["source"] == "manual"
    assert store.col.docs["AAPL"]["target"] == 150.0
    assert store.col.docs["AAPL"]["source"] == "manual"
