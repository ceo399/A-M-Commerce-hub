"""??????? API????????????????????"""
from __future__ import annotations

import tempfile
from pathlib import Path

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from pydantic import BaseModel
from sqlalchemy import select

from app.api import serializers as ser
from app.api.deps import get_current_user, require_roles
from app.db.models import ListingDraft, ListingDraftStatus, Role
from app.db.session import session_scope
from app.services import catalog_service

router = APIRouter(prefix="/catalog", tags=["catalog"])


@router.post("/import")
async def import_catalog(file: UploadFile = File(...), sheet: str | None = None,
                         _=Depends(require_roles(Role.MERCHANDISING))):
    """????????(CSV/Excel)??????????????"""
    suffix = Path(file.filename or "").suffix.lower()
    if suffix not in (".csv", ".tsv", ".txt", ".xlsx", ".xlsm"):
        raise HTTPException(400, "CSV ??? Excel(.xlsx) ?????????????")
    data = await file.read()
    # Windows????????NamedTemporaryFile???????????????????
    # ???????????????????????????
    tmp = tempfile.NamedTemporaryFile(suffix=suffix, delete=False)
    try:
        tmp.write(data)
        tmp.close()
        with session_scope() as s:
            return catalog_service.import_from_file(s, tmp.name, sheet=sheet)
    finally:
        Path(tmp.name).unlink(missing_ok=True)


class DraftPatch(BaseModel):
    title: str | None = None
    bullet_points: list[str] | None = None
    description: str | None = None


@router.get("/drafts")
def list_drafts(status: ListingDraftStatus | None = None,
                limit: int = Query(50, le=200), offset: int = 0,
                _=Depends(get_current_user)):
    with session_scope() as s:
        stmt = select(ListingDraft).order_by(ListingDraft.id.desc())
        if status:
            stmt = stmt.where(ListingDraft.status == status)
        return ser.paginate(s, stmt, limit, offset, ser.listing_draft_dict)


@router.get("/drafts/{draft_id}")
def get_draft(draft_id: int, _=Depends(get_current_user)):
    with session_scope() as s:
        d = s.get(ListingDraft, draft_id)
        if d is None:
            raise HTTPException(404, "??????????????")
        return ser.listing_draft_dict(d)


@router.patch("/drafts/{draft_id}")
def edit_draft(draft_id: int, body: DraftPatch, _=Depends(require_roles(Role.MERCHANDISING))):
    """????????????????????????"""
    with session_scope() as s:
        d = s.get(ListingDraft, draft_id)
        if d is None:
            raise HTTPException(404, "??????????????")
        if d.status not in (ListingDraftStatus.PENDING_APPROVAL, ListingDraftStatus.GENERATING):
            raise HTTPException(400, "???????????????????")
        for k, v in body.model_dump(exclude_unset=True).items():
            setattr(d, k, v)
        s.flush()
        return ser.listing_draft_dict(d)
