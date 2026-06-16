"""API統合テスト: 認証〜全業務〜承認連動を一気通貫で検証。

実行: python -m tests.test_api （TestClientを使用。サーバ起動不要）
"""
from __future__ import annotations

import csv
import io
import warnings

warnings.filterwarnings("ignore")

from fastapi.testclient import TestClient

from app.api.main import app
from app.db.session import session_scope
from app.integrations.registry import get_integrations
from app.services import auth_service


def _run():
    with TestClient(app) as c:
        with session_scope() as s:
            auth_service.create_user(s, email="admin@x.com", password="AdminPass!23",
                                     roles=[], is_admin=True)
        otp = get_integrations().otp

        def login(e, p):
            r = c.post("/auth/login", json={"email": e, "password": p}).json()
            v = c.post("/auth/verify",
                       json={"challenge_id": r["challenge_id"], "code": otp.last_code}).json()
            return {"Authorization": f"Bearer {v['access_token']}"}

        admin = login("admin@x.com", "AdminPass!23")
        for em, role in [("logi", "logistics"), ("buy", "purchasing"),
                         ("md", "merchandising"), ("mkt", "marketing")]:
            assert c.post("/auth/users", headers=admin,
                          json={"email": f"{em}@x.com", "password": "Pass!2345",
                                "roles": [role]}).status_code == 201
        H = {r: login(f"{r}@x.com", "Pass!2345") for r in ["logi", "buy", "md", "mkt"]}

        # カタログ取込（アップロード）
        head = ["SKU", "商品名", "供給状況", "在庫数", "希望小売価格", "特徴1", "画像ファイル名/URL"]
        rows = [["SKU-NEW-A", "新製品A", "供給中", "", "¥1,980", "軽量", "a.jpg"],
                ["SKU-OLD-B", "旧製品B", "生産終了", "0", "", "", ""]]
        buf = io.StringIO(); w = csv.writer(buf); w.writerow(head); [w.writerow(r) for r in rows]
        imp = c.post("/catalog/import", headers=H["md"],
                     files={"file": ("c.csv", buf.getvalue().encode("utf-8-sig"), "text/csv")}).json()
        assert (imp["imported"]["new_products"], imp["imported"]["discontinued"]) == (1, 1)

        # 出品承認: 物流は不可、MDは可→出品
        tid = c.get("/approvals", headers=H["md"], params={"type": "listing"}).json()["items"][0]["id"]
        assert c.post(f"/approvals/{tid}/decide", headers=H["logi"], json={"approved": True}).status_code == 403
        assert c.post(f"/approvals/{tid}/decide", headers=H["md"], json={"approved": True}).json()["action"]["published_asin"]

        # 受注→在庫照合（即納6/不足4）
        c.post("/products", headers=admin, json={"sku": "SKU-DEMO-001", "name": "デモ"})
        pid = c.get("/products", headers=admin, params={"q": "SKU-DEMO-001"}).json()["items"][0]["id"]
        c.post(f"/inventory/{pid}/receive", headers=H["logi"], json={"quantity": 6})
        line = c.post("/orders/ingest", headers=H["logi"]).json()["ingested"][0]["lines"][0]
        assert (line["qty_confirmed"], line["qty_backordered"]) == (6, 4)

        # 発注承認→入荷
        sp = c.get("/approvals", headers=H["buy"], params={"type": "supplier_po"}).json()["items"][0]
        c.post(f"/approvals/{sp['id']}/decide", headers=H["buy"], json={"approved": True})
        spo = c.get("/supplier-pos", headers=H["buy"]).json()["items"][0]["id"]
        assert c.post(f"/supplier-pos/{spo}/receive", headers=H["buy"]).json()["status"] == "received"

        # 出荷承認→ASN/Invoice
        ship = c.get("/approvals", headers=H["logi"], params={"type": "daily_shipment"}).json()["items"][0]
        assert c.post(f"/approvals/{ship['id']}/decide", headers=H["logi"],
                      json={"approved": True}).json()["action"]["po_status"] == "invoiced"

        # 広告 収集→承認→反映
        c.post("/ads/collect", headers=H["mkt"])
        adt = c.get("/approvals", headers=H["mkt"], params={"type": "ad_bid"}).json()["items"][0]
        ar = c.post(f"/approvals/{adt['id']}/decide", headers=H["mkt"], json={"approved": True}).json()
        assert ar["action"]["approved_recommendations"] > 0 and ar["action"]["applied"] > 0

        # 認証・ページネーション
        assert c.get("/products").status_code == 401
        assert set(c.get("/products", headers=admin, params={"limit": 1}).json()) == {
            "items", "total", "limit", "offset"}


def test_full_api_flow():
    _run()


if __name__ == "__main__":
    _run()
    print("OK: API統合テスト全通過")
