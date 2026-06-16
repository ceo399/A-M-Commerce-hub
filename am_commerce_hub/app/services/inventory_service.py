"""在庫サービス: 単一台帳(StockMovement)に基づく入庫・引当・出荷・調整。

現在在庫は台帳の集計から導出する（現在値テーブルを持たない＝単一の真実）。
旧 InventoryItem / MovementType は廃し、ロケーション×所有状態のステートで表現する。
公開する動詞（receive_stock / allocate / ship_allocated）は従来と同じ引数互換。
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
    """ある商品の在庫残高。ステート別の数量から各指標を導出する。"""
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
        # 自社倉庫の物理在庫（引当可能 + 引当済み）
        return self.available + self.allocated

    def qty(self, state: InventoryState) -> int:
        return self.by_state.get(state.value, 0)


def _signed_rows():
    """to_state を +quantity、from_state を -quantity とした符号付き行集合。"""
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
    """全商品のステート別残高を1クエリで取得（一覧用）。reorder_point は呼び出し側で付与。"""
    rows = _signed_rows()
    stmt = select(rows.c.pid, rows.c.state, func.sum(rows.c.q)).group_by(rows.c.pid, rows.c.state)
    out: dict[int, StockBalance] = {}
    for pid, state, total in session.execute(stmt):
        bal = out.setdefault(pid, StockBalance(product_id=pid))
        if state is not None:
            bal.by_state[_state_key(state)] = int(total or 0)
    return out


# 互換エイリアス（旧コードが get_or_create_item を呼んでも残高を返す）
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
    """入庫: 自社倉庫の引当可能在庫を増やす。"""
    _move(session, product_id, None, S.OWN_WAREHOUSE_AVAILABLE, qty, "inbound",
          ref_type, ref_id, source_system, source_event_id, "入庫")


def allocate(session: Session, product_id: int, qty: int, ref_type=None, ref_id=None,
             source_system: str = "manual", source_event_id: str | None = None) -> int:
    """引当可能数だけ引き当て（available→reserved）、実際に引き当てた数を返す。"""
    bal = get_balance(session, product_id)
    allocatable = max(0, min(qty, bal.available))
    if allocatable > 0:
        _move(session, product_id, S.OWN_WAREHOUSE_AVAILABLE, S.OWN_WAREHOUSE_RESERVED,
              allocatable, "allocate", ref_type, ref_id, source_system, source_event_id, "引当")
    return allocatable


def release(session: Session, product_id: int, qty: int, ref_type=None, ref_id=None,
            source_system: str = "manual", source_event_id: str | None = None) -> int:
    """引当解除（reserved→available）。"""
    bal = get_balance(session, product_id)
    releasable = max(0, min(qty, bal.allocated))
    if releasable > 0:
        _move(session, product_id, S.OWN_WAREHOUSE_RESERVED, S.OWN_WAREHOUSE_AVAILABLE,
              releasable, "release", ref_type, ref_id, source_system, source_event_id, "引当解除")
    return releasable


def ship_allocated(session: Session, product_id: int, qty: int, ref_type=None, ref_id=None,
                   source_system: str = "manual", source_event_id: str | None = None) -> None:
    """引当済みを出荷（reserved→流出）。物理在庫と引当を同時に減算する。"""
    _move(session, product_id, S.OWN_WAREHOUSE_RESERVED, None, qty, "outbound",
          ref_type, ref_id, source_system, source_event_id, "出荷")


def adjust(session: Session, product_id: int, delta: int, ref_type: str = "manual",
           ref_id=None, source_system: str = "manual", source_event_id: str | None = None,
           note: str | None = None) -> StockBalance:
    """棚卸調整。delta>0 は入庫、delta<0 は引当可能在庫からの減算。"""
    if delta > 0:
        _move(session, product_id, None, S.OWN_WAREHOUSE_AVAILABLE, delta, "adjust",
              ref_type, ref_id, source_system, source_event_id, note or "棚卸調整(増)")
    elif delta < 0:
        bal = get_balance(session, product_id)
        dec = -delta
        if dec > bal.available:
            raise ValueError("引当済み数量を下回る調整はできません")
        _move(session, product_id, S.OWN_WAREHOUSE_AVAILABLE, None, dec, "adjust",
              ref_type, ref_id, source_system, source_event_id, note or "棚卸調整(減)")
    return get_balance(session, product_id)


# ---------------------------------------------------------------------
# FBA reconcile: InventorySnapshot(絶対在庫) と 台帳(StockMovement導出残高) の差分を
# 補正movement(理由=reconcile)で台帳側に反映し、実数へ収束させる。
# ---------------------------------------------------------------------
_FBA_STATES = (S.FBA_FULFILLABLE, S.FBA_INBOUND, S.FBA_RESERVED, S.FBA_UNFULFILLABLE)


def _latest_fba_snapshots(session: Session) -> dict[tuple[int, str], int]:
    """(product_id, state) ごとの最新スナップショット数量。captured_at昇順で上書き＝最新が残る。"""
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
    """FBA各ステートの台帳残高を最新スナップショットへ一致させる。実施した補正の一覧を返す。"""
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
                      source_system=source_system, note="FBA突合(増)")
            else:
                _move(session, pid, state, None, -delta, "reconcile",
                      source_system=source_system, note="FBA突合(減)")
            corrections.append({"product_id": pid, "state": key,
                                "from": current, "to": target, "delta": delta})
    return corrections
