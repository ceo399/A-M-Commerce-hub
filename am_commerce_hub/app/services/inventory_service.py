"""??????: ????(StockMovement)????????????????

?????????????????????????????????????
? InventoryItem / MovementType ??????????????????????????
???????receive_stock / allocate / ship_allocated????????????
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field

from sqlalchemy import func, select, union_all
from sqlalchemy.orm import Session

from app.db.models import InventoryState, InventorySnapshot, Product, StockMovement

S = InventoryState


@dataclass
class StockBalance:
    """?????????????????????????????"""
    product_id: int
    by_state: dict[str, int] = field(default_factory=dict)
    reorder_point: int = 0

    @property
    def available(self) -> int:
        return self.by_state.get(S.OWN_WAREHOUSE_AVAILABLE.value, 0)

    @property
    def allocated(self) -> int:
        return self.by_state.get(S.OWN_WAREHOUSE_RESERVED.value, 0)

    @property
    def on_hand(self) -> int:
        # ?????????????? + ?????
        return self.available + self.allocated

    def qty(self, state: InventoryState) -> int:
        return self.by_state.get(state.value, 0)


def _signed_rows():
    """to_state ? +quantity?from_state ? -quantity ???????????"""
    to_rows = select(
        StockMovement.product_id.label("pid"),
        StockMovement.to_state.label("state"),
        StockMovement.quantity.label("q"),
    ).where(StockMovement.to_state.is_not(None))
    from_rows = select(
        StockMovement.product_id.label("pid"),
        StockMovement.from_state.label("state"),
        (-StockMovement.quantity).label("q"),
    ).where(StockMovement.from_state.is_not(None))
    return union_all(to_rows, from_rows).subquery()


def _state_key(state) -> str:
    return state.value if hasattr(state, "value") else str(state)


def get_balance(session: Session, product_id: int) -> StockBalance:
    rows = _signed_rows()
    stmt = (
        select(rows.c.state, func.sum(rows.c.q))
        .where(rows.c.pid == product_id)
        .group_by(rows.c.state)
    )
    by_state: dict[str, int] = {}
    for state, total in session.execute(stmt):
        if state is not None:
            by_state[_state_key(state)] = int(total or 0)
    product = session.get(Product, product_id)
    rp = product.reorder_point if product else 0
    return StockBalance(product_id=product_id, by_state=by_state, reorder_point=rp)


def all_balances(session: Session) -> dict[int, StockBalance]:
    """????????????1????????????reorder_point ??????????"""
    rows = _signed_rows()
    stmt = select(rows.c.pid, rows.c.state, func.sum(rows.c.q)).group_by(rows.c.pid, rows.c.state)
    out: dict[int, StockBalance] = {}
    for pid, state, total in session.execute(stmt):
        bal = out.setdefault(pid, StockBalance(product_id=pid))
        if state is not None:
            bal.by_state[_state_key(state)] = int(total or 0)
    return out


# ????????????? get_or_create_item ???????????
def get_or_create_item(session: Session, product_id: int, location: str = "main") -> StockBalance:
    return get_balance(session, product_id)


def _move(session: Session, product_id: int, from_state, to_state, qty: int, reason: str,
          ref_type: str | None = None, ref_id: int | None = None,
          source_system: str = "manual", source_event_id: str | None = None,
          note: str | None = None) -> StockMovement | None:
    if qty <= 0:
        return None
    mv = StockMovement(
        product_id=product_id, from_state=from_state, to_state=to_state,
        quantity=qty, reason=reason, ref_type=ref_type, ref_id=ref_id,
        source_system=source_system, source_event_id=source_event_id or uuid.uuid4().hex,
        note=note,
    )
    session.add(mv)
    session.flush()
    return mv


def receive_stock(session: Session, product_id: int, qty: int, ref_type=None, ref_id=None,
                  source_system: str = "manual", source_event_id: str | None = None) -> None:
    """??: ????????????????"""
    _move(session, product_id, None, S.OWN_WAREHOUSE_AVAILABLE, qty, "inbound",
          ref_type, ref_id, source_system, source_event_id, "??")


def allocate(session: Session, product_id: int, qty: int, ref_type=None, ref_id=None,
             source_system: str = "manual", source_event_id: str | None = None) -> int:
    """????????????available?reserved???????????????"""
    bal = get_balance(session, product_id)
    allocatable = max(0, min(qty, bal.available))
    if allocatable > 0:
        _move(session, product_id, S.OWN_WAREHOUSE_AVAILABLE, S.OWN_WAREHOUSE_RESERVED,
              allocatable, "allocate", ref_type, ref_id, source_system, source_event_id, "??")
    return allocatable


def release(session: Session, product_id: int, qty: int, ref_type=None, ref_id=None,
            source_system: str = "manual", source_event_id: str | None = None) -> int:
    """?????reserved?available??"""
    bal = get_balance(session, product_id)
    releasable = max(0, min(qty, bal.allocated))
    if releasable > 0:
        _move(session, product_id, S.OWN_WAREHOUSE_RESERVED, S.OWN_WAREHOUSE_AVAILABLE,
              releasable, "release", ref_type, ref_id, source_system, source_event_id, "????")
    return releasable


def ship_allocated(session: Session, product_id: int, qty: int, ref_type=None, ref_id=None,
                   source_system: str = "manual", source_event_id: str | None = None) -> None:
    """????????reserved?????????????????????"""
    _move(session, product_id, S.OWN_WAREHOUSE_RESERVED, None, qty, "outbound",
          ref_type, ref_id, source_system, source_event_id, "??")


def adjust(session: Session, product_id: int, delta: int, ref_type: str = "manual",
           ref_id=None, source_system: str = "manual", source_event_id: str | None = None,
           note: str | None = None) -> StockBalance:
    """?????delta>0 ????delta<0 ?????????????"""
    if delta > 0:
        _move(session, product_id, None, S.OWN_WAREHOUSE_AVAILABLE, delta, "adjust",
              ref_type, ref_id, source_system, source_event_id, note or "????(?)")
    elif delta < 0:
        bal = get_balance(session, product_id)
        dec = -delta
        if dec > bal.available:
            raise ValueError("??????????????????")
        _move(session, product_id, S.OWN_WAREHOUSE_AVAILABLE, None, dec, "adjust",
              ref_type, ref_id, source_system, source_event_id, note or "????(?)")
    return get_balance(session, product_id)


# ---------------------------------------------------------------------
# FBA reconcile: InventorySnapshot(????) ? ??(StockMovement????) ????
# ??movement(??=reconcile)??????????????????
# ---------------------------------------------------------------------
_FBA_STATES = (S.FBA_FULFILLABLE, S.FBA_INBOUND, S.FBA_RESERVED, S.FBA_UNFULFILLABLE)


def _latest_fba_snapshots(session: Session) -> dict[tuple[int, str], int]:
    """(product_id, state) ????????????????captured_at?????????????"""
    rows = session.execute(
        select(
            InventorySnapshot.product_id,
            InventorySnapshot.state,
            InventorySnapshot.quantity,
        )
        .where(InventorySnapshot.state.in_(_FBA_STATES))
        .order_by(InventorySnapshot.captured_at.asc(), InventorySnapshot.id.asc())
    ).all()
    latest: dict[tuple[int, str], int] = {}
    for pid, state, qty in rows:
        latest[(pid, _state_key(state))] = int(qty or 0)
    return latest


def reconcile_fba(session: Session, source_system: str = "reconcile") -> list[dict]:
    """FBA?????????????????????????????????????????"""
    latest = _latest_fba_snapshots(session)
    corrections: list[dict] = []
    pids = {pid for (pid, _state) in latest}
    for pid in pids:
        bal = get_balance(session, pid)
        for state in _FBA_STATES:
            key = state.value
            if (pid, key) not in latest:
                continue
            target = latest[(pid, key)]
            current = bal.by_state.get(key, 0)
            delta = target - current
            if delta == 0:
                continue
            if delta > 0:
                _move(session, pid, None, state, delta, "reconcile",
                      source_system=source_system, note="FBA??(?)")
            else:
                _move(session, pid, state, None, -delta, "reconcile",
                      source_system=source_system, note="FBA??(?)")
            corrections.append({"product_id": pid, "state": key,
                                "from": current, "to": target, "delta": delta})
    return corrections
