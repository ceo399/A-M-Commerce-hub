"""???????????????????????????"""
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

HEADER = ["SKU", "???", "JAN???", "?????", "?????", "????", "???",
          "????", "??????", "????", "?", "???/??", "??", "??",
          "????/??", "??1", "??2", "??3", "??4", "??5", "???????/URL"]

ROWS = [
    # ??????????????????????2??
    ["SKU-NEW-1", "???????", "4500000000001", "Cordial", "Cordial", "???", "30",
     "?? > ????", "?2,200", "1200", "?", "3m", "120g", "OFC?", "???",
     "????", "????", "???", "", "", "a.jpg;b.jpg"],
    # ??????????0?
    ["SKU-OLD-1", "??DI", "4500000000002", "Cordial", "Cordial", "????", "0",
     "?? > DI", "", "", "", "", "", "", "", "", "", "", "", "", ""],
    # ?????????? ? ????(in_stock=True)
    ["SKU-RUNOUT", "?????", "", "Cordial", "", "????", "5",
     "", "", "", "", "", "", "", "", "", "", "", "", "", ""],
    # ???: SKU?
    ["", "SKU??", "", "", "", "???", "", "", "", "", "", "", "", "", "", "", "", "", "", "", ""],
    # ???: ????
    ["SKU-NONAME", "", "", "", "", "???", "", "", "", "", "", "", "", "", "", "", "", "", "", "", ""],
    # ??: SKU??
    ["SKU-NEW-1", "???", "", "", "", "???", "", "", "", "", "", "", "", "", "", "", "", "", "", "", ""],
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
    wb = Workbook(); ws = wb.active; ws.title = "?????"
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
        # 3???????/??/??????2????SKU????????1??????
        assert len(rx.rows) == 3, rx.summary()
        assert len(rx.errors) == 2, rx.errors
        assert len(rx.warnings) == 1, rx.warnings
        assert rc.summary() == rx.summary()


def test_value_normalization():
    with tempfile.TemporaryDirectory() as d:
        xlsx = Path(d) / "c.xlsx"; _write_xlsx(xlsx)
        rows = {r["sku"]: r for r in parse_catalog(xlsx).rows}
        new = rows["SKU-NEW-1"]
        assert new["list_price"] == 2200.0          # ?2,200 ? 2200.0
        assert new["spec"]["features"] == ["????", "????", "???"]
        assert new["spec"]["images"] == ["a.jpg", "b.jpg"]
        assert rows["SKU-OLD-1"]["in_stock"] is False     # ????????0
        assert rows["SKU-RUNOUT"]["in_stock"] is True      # ??????????


def test_end_to_end_import():
    with tempfile.TemporaryDirectory() as d:
        xlsx = Path(d) / "catalog.xlsx"; _write_xlsx(xlsx)
        s = _session()
        # ??????Amazon???????????????
        integ = _integ(registry={"SKU-OLD-1": CatalogStatus(
            asin="B0OLD1", is_registered=True, is_active=True)})
        report = catalog_service.import_from_file(s, str(xlsx), integ=integ)
        assert report["imported"]["new_products"] >= 1
        assert report["imported"]["discontinued"] == 1
        # ????????????MD???????????
        drafts = s.scalars(select(ListingDraft)).all()
        assert len(drafts) >= 1
        assert len(approval_service.pending(s, ApprovalType.LISTING)) >= 1
        # ???????????
        old = s.scalar(select(Product).where(Product.sku == "SKU-OLD-1"))
        assert old.status == ProductStatus.DISCONTINUED


if __name__ == "__main__":
    test_parse_xlsx_and_csv_equivalent()
    test_value_normalization()
    test_end_to_end_import()
    print("OK: ????????????")
