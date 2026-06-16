"""カタログ取り込みパーサ＆エンドツーエンド取込のテスト。"""
from __future__ import annotations

import csv
import tempfile
from pathlib import Path

from openpyxl import Workbook
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from app.db.base import Base
from app.db.models import ApprovalType, ListingDraft, Product, ProductStatus
from app.integrations.mock_adapters import (
    MockAds, MockAI, MockAttribution, MockCatalog, MockListings,
    MockOtpDelivery, MockShippingHub, MockVendorOrders,
)
from app.integrations.ports import CatalogStatus
from app.integrations.registry import Integrations
from app.services import approval_service, catalog_service
from app.services.catalog_import import parse_catalog

HEADER = ["SKU", "商品名", "JANコード", "メーカー名", "ブランド名", "供給状況", "在庫数",
          "カテゴリ", "希望小売価格", "仕入原価", "色", "サイズ/寸法", "重量", "素材",
          "対応機種/用途", "特徴1", "特徴2", "特徴3", "特徴4", "特徴5", "画像ファイル名/URL"]

ROWS = [
    # 新製品（供給中・特徴・価格に¥とカンマ・画像2枚）
    ["SKU-NEW-1", "新製品ケーブル", "4500000000001", "Cordial", "Cordial", "供給中", "30",
     "音響 > ケーブル", "¥2,200", "1200", "黒", "3m", "120g", "OFC銅", "マイク",
     "低ノイズ", "金メッキ", "高耐久", "", "", "a.jpg;b.jpg"],
    # 廃盤（生産終了・在庫0）
    ["SKU-OLD-1", "旧型DI", "4500000000002", "Cordial", "Cordial", "生産終了", "0",
     "音響 > DI", "", "", "", "", "", "", "", "", "", "", "", "", ""],
    # 生産終了だが在庫あり → 販売継続(in_stock=True)
    ["SKU-RUNOUT", "終売間際品", "", "Cordial", "", "生産終了", "5",
     "", "", "", "", "", "", "", "", "", "", "", "", "", ""],
    # エラー: SKU空
    ["", "SKUなし", "", "", "", "供給中", "", "", "", "", "", "", "", "", "", "", "", "", "", "", ""],
    # エラー: 商品名空
    ["SKU-NONAME", "", "", "", "", "供給中", "", "", "", "", "", "", "", "", "", "", "", "", "", "", ""],
    # 警告: SKU重複
    ["SKU-NEW-1", "重複行", "", "", "", "供給中", "", "", "", "", "", "", "", "", "", "", "", "", "", "", ""],
]


def _session():
    engine = create_engine("sqlite:///:memory:", future=True)
    import app.db.models  # noqa
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, future=True)()


def _integ(registry=None):
    return Integrations(
        vendor=MockVendorOrders(), catalog=MockCatalog(registry=registry or {}),
        listings=MockListings(), ads=MockAds(), attribution=MockAttribution(),
        shipping_hub=MockShippingHub(), ai=MockAI(), otp=MockOtpDelivery())


def _write_xlsx(p: Path):
    wb = Workbook(); ws = wb.active; ws.title = "商品データ"
    ws.append(HEADER)
    for r in ROWS:
        ws.append(r)
    wb.save(p)


def _write_csv(p: Path):
    with open(p, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f); w.writerow(HEADER)
        for r in ROWS:
            w.writerow(r)


def test_parse_xlsx_and_csv_equivalent():
    with tempfile.TemporaryDirectory() as d:
        xlsx, csvp = Path(d) / "c.xlsx", Path(d) / "c.csv"
        _write_xlsx(xlsx); _write_csv(csvp)
        rx, rc = parse_catalog(xlsx), parse_catalog(csvp)
        # 3行成功（新製品/廃盤/終売間際）、2エラー（SKU空・商品名空）、1警告（重複）
        assert len(rx.rows) == 3, rx.summary()
        assert len(rx.errors) == 2, rx.errors
        assert len(rx.warnings) == 1, rx.warnings
        assert rc.summary() == rx.summary()


def test_value_normalization():
    with tempfile.TemporaryDirectory() as d:
        xlsx = Path(d) / "c.xlsx"; _write_xlsx(xlsx)
        rows = {r["sku"]: r for r in parse_catalog(xlsx).rows}
        new = rows["SKU-NEW-1"]
        assert new["list_price"] == 2200.0          # ¥2,200 → 2200.0
        assert new["spec"]["features"] == ["低ノイズ", "金メッキ", "高耐久"]
        assert new["spec"]["images"] == ["a.jpg", "b.jpg"]
        assert rows["SKU-OLD-1"]["in_stock"] is False     # 生産終了かつ在庫0
        assert rows["SKU-RUNOUT"]["in_stock"] is True      # 生産終了でも在庫あり


def test_end_to_end_import():
    with tempfile.TemporaryDirectory() as d:
        xlsx = Path(d) / "catalog.xlsx"; _write_xlsx(xlsx)
        s = _session()
        # 廃盤対象は「Amazon登録済み」として差分照合させる
        integ = _integ(registry={"SKU-OLD-1": CatalogStatus(
            asin="B0OLD1", is_registered=True, is_active=True)})
        report = catalog_service.import_from_file(s, str(xlsx), integ=integ)
        assert report["imported"]["new_products"] >= 1
        assert report["imported"]["discontinued"] == 1
        # 新製品には出品ドラフトとMD承認タスクが生成される
        drafts = s.scalars(select(ListingDraft)).all()
        assert len(drafts) >= 1
        assert len(approval_service.pending(s, ApprovalType.LISTING)) >= 1
        # 廃盤フラグが立っている
        old = s.scalar(select(Product).where(Product.sku == "SKU-OLD-1"))
        assert old.status == ProductStatus.DISCONTINUED


if __name__ == "__main__":
    test_parse_xlsx_and_csv_equivalent()
    test_value_normalization()
    test_end_to_end_import()
    print("OK: カタログ取込テスト全通過")
