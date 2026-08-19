# firestore_store.py — Firestore 读写封装(monitor / gen_targets / build_watchlist 共用)
# 解耦核心: 目标价/状态都存 Firestore, 各模块只通过 store 读写, 互不依赖。
COLLECTION = "stocks"


def _clean_id(ticker):
    """规范化 doc id (如 BRK/B -> BRK-B) 避免 Firestore 将 / 视作子集合路径"""
    return str(ticker).strip().upper().replace("/", "-")


class StockStore:
    def __init__(self, client=None, project=None):
        if client is None:
            from google.cloud import firestore
            try:
                client = firestore.Client(project=project)
            except Exception:
                import subprocess
                from google.oauth2.credentials import Credentials
                token = subprocess.check_output(['gcloud', 'auth', 'print-access-token']).decode('utf-8').strip()
                creds = Credentials(token)
                client = firestore.Client(project=project or 'stock-alert-hub', credentials=creds)
        self.col = client.collection(COLLECTION)

    def get_all(self):
        """返回 {ticker: {state...}}"""
        return {d.id: d.to_dict() for d in self.col.stream()}

    def get(self, ticker):
        doc = self.col.document(_clean_id(ticker)).get()
        return doc.to_dict() if doc.exists else None

    def upsert(self, ticker, data):
        self.col.document(_clean_id(ticker)).set(data, merge=True)
