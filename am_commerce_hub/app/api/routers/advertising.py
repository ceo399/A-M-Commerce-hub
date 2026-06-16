"""広告運用 API。"""
from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select

from app.api import serializers as ser
from app.api.deps import get_current_user, require_roles
from app.db.models import AdEntity, BidRecommendation, BidRecommendationStatus, Role
from app.db.session import session_scope
from app.services import advertising_service

router = APIRouter(prefix="/ads", tags=["advertising"])


@router.post("/collect")
def collect(report_date: date | None = None, _=Depends(require_roles(Role.MARKETING))):
    """日次レポート取得 → 指標保存 → AI分析 → 提案生成 → 一括承認タスク起票。"""
    with session_scope() as s:
        recs = advertising_service.collect_and_recommend(s, report_date=report_date or date.today())
        return {"report_date": (report_date or date.today()).isoformat(),
                "recommendations": [ser.bid_rec_dict(r) for r in recs]}


@router.get("/recommendations")
def list_recommendations(status: BidRecommendationStatus | None = None,
                         limit: int = Query(100, le=300), offset: int = 0,
                         _=Depends(get_current_user)):
    with session_scope() as s:
        stmt = select(BidRecommendation).order_by(BidRecommendation.id.desc())
        if status:
            stmt = stmt.where(BidRecommendation.status == status)
        return ser.paginate(s, stmt, limit, offset, ser.bid_rec_dict)


@router.get("/entities")
def list_entities(limit: int = Query(100, le=300), offset: int = 0,
                  _=Depends(get_current_user)):
    with session_scope() as s:
        stmt = select(AdEntity).order_by(AdEntity.id.desc())
        return ser.paginate(s, stmt, limit, offset, ser.ad_entity_dict)

@router.post("/suggestions/generate")
def generate_suggestions(report_date: date | None = None,
                        _=Depends(require_roles(Role.MARKETING))):
    """Claude API �� 4 �v�f�̒�Ă𐶐�"""
    with session_scope() as s:
        suggestions = advertising_service.generate_ai_suggestions(
            s, report_date=report_date or date.today()
        )
        return suggestions
