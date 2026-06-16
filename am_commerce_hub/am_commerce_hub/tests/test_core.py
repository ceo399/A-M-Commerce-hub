"""??????????????????????: python -m pytest -q"""
from __future__ import annotations

from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker

from app.db.base import Base
from app.db.models import InventoryState, Product, StockMovement
from app.services import inventory_service


def _session():
    engine = create_engine("sqlite:///:memory:", future=True)
    import app.db.models  # noqa
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, future=True)()


def test_allocation_never_exceeds_available():
    s = _session()
    p = Product(sku="T-1", name="???")
    s.add(p); s.flush()
    inventory_service.receive_stock(s, p.id, 5)
    allocated = inventory_service.allocate(s, p.id, 10)   # 5????????
    assert allocated == 5
    bal = inventory_service.get_balance(s, p.id)
    assert bal.available == 0 and bal.on_hand == 5 and bal.allocated == 5


def test_ship_reduces_on_hand_and_allocated():
    s = _session()
    p = Product(sku="T-2", name="???2")
    s.add(p); s.flush()
    inventory_service.receive_stock(s, p.id, 8)
    inventory_service.allocate(s, p.id, 3)
    inventory_service.ship_allocated(s, p.id, 3)
    bal = inventory_service.get_balance(s, p.id)
    assert bal.on_hand == 5 and bal.allocated == 0 and bal.available == 5


def test_ledger_is_append_only_and_derives_balance():
    """????????????????????????????????????"""
    s = _session()
    p = Product(sku="T-3", name="???3")
    s.add(p); s.flush()
    inventory_service.receive_stock(s, p.id, 10)
    inventory_service.allocate(s, p.id, 4)
    inventory_service.release(s, p.id, 1)        # 1??????
    inventory_service.ship_allocated(s, p.id, 3)
    # ??+??+??+?? = 4?????????
    count = s.scalar(select(func.count()).select_from(StockMovement))
    assert count == 4
    bal = inventory_service.get_balance(s, p.id)
    # ??10 ? ??4 ? ??1(=available7,reserved3) ? ??3(reserved0)
    assert bal.available == 7 and bal.allocated == 0 and bal.on_hand == 7
    assert bal.qty(InventoryState.OWN_WAREHOUSE_AVAILABLE) == 7


def test_negative_adjust_cannot_go_below_reserved():
    s = _session()
    p = Product(sku="T-4", name="???4")
    s.add(p); s.flush()
    inventory_service.receive_stock(s, p.id, 5)
    inventory_service.allocate(s, p.id, 4)       # available=1, reserved=4
    try:
        inventory_service.adjust(s, p.id, -2)    # available(1)???? ? ??
        assert False, "ValueError ????????"
    except ValueError:
        pass
    bal = inventory_service.get_balance(s, p.id)
    assert bal.available == 1 and bal.allocated == 4


if __name__ == "__main__":
    test_allocation_never_exceeds_available()
    test_ship_reduces_on_hand_and_allocated()
    test_ledger_is_append_only_and_derives_balance()
    test_negative_adjust_cannot_go_below_reserved()
    print("OK: ??????")
