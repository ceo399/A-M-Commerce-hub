"""承認 API。決裁すると後続処理（出荷・出品・入札反映など）まで自動進行。"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import select

from app.api import serializers as ser
from app.api.deps import get_current_user
from app.db.models import (
    APPROVAL_ROLE_MAP,
    ApprovalStatus,
    ApprovalTask,
    ApprovalType,
    Role,
)
from app.db.session import session_scope
from app.services import workflow

router = APIRouter(prefix="/approvals", tags=["approvals"])


class DecideBody(BaseModel):
    approved: bool


@router.get("")
def list_approvals(type: ApprovalType | None = None,
                   status: ApprovalStatus = ApprovalStatus.PENDING,
                   limit: int = Query(100, le=300), offset: int = 0,
                   _=Depends(get_current_user)):
    with session_scope() as s:
        stmt = select(ApprovalTask).where(ApprovalTask.status == status).order_by(ApprovalTask.id.desc())
        if type:
            stmt = stmt.where(ApprovalTask.approval_type == type)
        return ser.paginate(s, stmt, limit, offset, ser.approval_dict)


@router.get("/{task_id}")
def get_approval(task_id: int, _=Depends(get_current_user)):
    with session_scope() as s:
        t = s.get(ApprovalTask, task_id)
        if t is None:
            raise HTTPException(404, "承認タスクが見つかりません")
        return ser.approval_dict(t)


@router.post("/{task_id}/decide")
def decide(task_id: int, body: DecideBody, current=Depends(get_current_user)):
    """承認/否認。対象の承認種別に必要なロール（または管理者）が必要。"""
    with session_scope() as s:
        task = s.get(ApprovalTask, task_id)
        if task is None:
            raise HTTPException(404, "承認タスクが見つかりません")
        if task.status != ApprovalStatus.PENDING:
            raise HTTPException(400, "既に決裁済みのタスクです")
        required = APPROVAL_ROLE_MAP.get(task.approval_type)
        allowed = {Role.ADMIN.value} | ({required.value} if required else set())
        if not (set(current["roles"]) & allowed):
            need = required.value if required else "適切な"
            raise HTTPException(403, f"この承認には {need} ロールが必要です")
        return workflow.apply_decision(s, task_id=task_id, approved=body.approved,
                                       by=current["email"])
