# firestore_store.py — Firestore 读写封装(monitor / gen_targets / build_watchlist 共用)
# 解耦核心: 目标价/状态都存 Firestore, 各模块只通过 store 读写, 互不依赖。
COLLECTION = "stocks"


class StockStore:
    def __init__(self, client=None):
        if client is None:
            from google.cloud import firestore
            client = firestore.Client()
        self.col = client.collection(COLLECTION)

    def get_all(self):
        """返回 {ticker: {state...}}"""
        return {d.id: d.to_dict() for d in self.col.stream()}

    def get(self, ticker):
        doc = self.col.document(ticker).get()
        return doc.to_dict() if doc.exists else None

    def upsert(self, ticker, data):
        self.col.document(ticker).set(data, merge=True)
