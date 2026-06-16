"""??????: ??????????????????"""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.base import utcnow
from app.db.models import ApprovalStatus, ApprovalTask, ApprovalType


def create_task(session: Session, approval_type: ApprovalType, ref_type: str,
                ref_id: int, summary: str) -> ApprovalTask:
    task = ApprovalTask(
        approval_type=approval_type, ref_type=ref_type, ref_id=ref_id, summary=summary,
    )
    session.add(task)
    session.flush()
    return task


def pending(session: Session, approval_type: ApprovalType | None = None) -> list[ApprovalTask]:
    stmt = select(ApprovalTask).where(ApprovalTask.status == ApprovalStatus.PENDING)
    if approval_type:
        stmt = stmt.where(ApprovalTask.approval_type == approval_type)
    return list(session.scalars(stmt))


def decide(session: Session, task_id: int, approved: bool, by: str = "operator") -> ApprovalTask:
    task = session.get(ApprovalTask, task_id)
    if task is None:
        raise ValueError(f"ApprovalTask {task_id} ????????")
    task.status = ApprovalStatus.APPROVED if approved else ApprovalStatus.REJECTED
    task.decided_by = by
    task.decided_at = utcnow()
    return task
