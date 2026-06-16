"""????????????1??

Amazon Ads API ? ?????????? ? ???????????
 ? ACoS/ROAS????????? ? AI(Claude)????????
 ? ?????????????????? ? AI????????????
 ? ?????????????????/??????
 ? Ads API???????????? ? ????????Attribution??
 ? ??DB??????AI?????????
"""
from __future__ import annotations

from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings
from app.db.models import (
    AdEntity,
    AdEntityType,
    AdMetricSnapshot,
    ApprovalType,
    BidRecommendation,
    BidRecommendationStatus,
)
from app.integrations.registry import Integrations, get_integrations
from app.services import approval_service


def collect_and_recommend(session: Session, report_date: date | None = None,
                          integ: Integrations | None = None) -> list[BidRecommendation]:
    """???????? ? ???? ? AI?? ? ???? ? ??????????"""
    integ = get_integrations(integ)
    report_date = report_date or date.today()

    rows = integ.ads.fetch_daily_report(report_date)
    metrics_for_ai = []
    for row in rows:
        entity = _upsert_entity(session, row)
        snapshot = AdMetricSnapshot(
            ad_entity_id=entity.id, report_date=report_date,
            impressions=row.impressions, clicks=row.clicks,
            spend=row.spend, sales=row.sales, orders=row.orders,
        )
        session.add(snapshot)
        metrics_for_ai.append({
            "amazon_entity_id": row.amazon_entity_id,
            "spend": row.spend, "sales": row.sales,
            "current_bid": float(entity.current_bid) if entity.current_bid else None,
        })
    session.flush()

    # AI?????????ACoS/ROAS???????????????
    ai_recs = integ.ai.analyze_ads(metrics_for_ai, settings.target_acos)

    recs: list[BidRecommendation] = []
    for r in ai_recs:
        entity = session.scalar(
            select(AdEntity).where(AdEntity.amazon_entity_id == r["amazon_entity_id"])
        )
        if entity is None:
            continue
        rec = BidRecommendation(
            ad_entity_id=entity.id,
            current_bid=entity.current_bid,
            recommended_bid=r.get("recommended_bid"),
            action=r["action"],
            rationale=r.get("rationale"),
            measured_acos=r.get("measured_acos"),
        )
        session.add(rec)
        recs.append(rec)
    session.flush()

    if recs:
        approval_service.create_task(
            session, ApprovalType.AD_BID, ref_type="bid_recommendation_batch",
            ref_id=report_date.toordinal(),
            summary=f"{report_date} ????? {len(recs)}? ????",
        )
    return recs


def _upsert_entity(session: Session, row) -> AdEntity:
    entity = session.scalar(
        select(AdEntity).where(AdEntity.amazon_entity_id == row.amazon_entity_id)
    )
    if entity is None:
        entity = AdEntity(
            amazon_entity_id=row.amazon_entity_id,
            entity_type=AdEntityType(row.entity_type),
            name=row.name, current_bid=row.current_bid,
        )
        session.add(entity)
        session.flush()
    return entity


def apply_recommendations(session: Session, rec_ids: list[int] | None = None,
                          report_date: date | None = None,
                          integ: Integrations | None = None) -> int:
    """???????? Ads API ????????????

    rec_ids ??????????????? proposed ???????
    """
    integ = get_integrations(integ)
    stmt = select(BidRecommendation).where(
        BidRecommendation.status == BidRecommendationStatus.APPROVED
    )
    if rec_ids:
        stmt = stmt.where(BidRecommendation.id.in_(rec_ids))
    applied = 0
    for rec in session.scalars(stmt):
        entity = session.get(AdEntity, rec.ad_entity_id)
        if rec.action == "pause":
            integ.ads.update_bid(entity.amazon_entity_id, None)
        elif rec.action in ("increase", "decrease") and rec.recommended_bid is not None:
            integ.ads.update_bid(entity.amazon_entity_id, float(rec.recommended_bid))
            entity.current_bid = rec.recommended_bid
        rec.status = BidRecommendationStatus.APPLIED
        applied += 1

    # ???????? Attribution ???????????????
    integ.attribution.fetch_attribution(report_date or date.today())
    return applied


def approve_all(session: Session, accept: bool = True) -> int:
    """????????????proposed ???? approved/rejected ??"""
    new_status = (BidRecommendationStatus.APPROVED if accept
                  else BidRecommendationStatus.REJECTED)
    recs = session.scalars(
        select(BidRecommendation).where(
            BidRecommendation.status == BidRecommendationStatus.PROPOSED)
    )
    count = 0
    for rec in recs:
        rec.status = new_status
        count += 1
    session.flush()  # ???????????SELECT???????autoflush=False????
    return count

def generate_ai_suggestions(session: Session, report_date: date) -> dict:
    """Claude API ??g???? 4 ?v?f???????"""
    return {"suggestions": []}

def generate_ai_suggestions(session: Session, report_date: date) -> dict:
    """Claude API ??g???? 4 ?v?f???????
    - ??????
    - ?VASIN?i??
    - ?}?[?P?b?g?j?[?Y???
    - ???[?J?[???l????
    """
    import anthropic
    integ = get_integrations()
    
    # ???g???N?X???W
    metrics = session.scalars(
        select(AdMetricSnapshot).where(AdMetricSnapshot.report_date == report_date)
    )
    
    # Claude API ???o??
    client = anthropic.Anthropic(api_key=settings.anthropic_api_key)
    response = client.messages.create(
        model="claude-opus-4-6",
        max_tokens=2000,
        messages=[{
            "role": "user",
            "content": f"???????g???N?X???????A4?v?f??????????????????: {metrics}"
        }]
    )
    
    return {
        "report_date": report_date.isoformat(),
        "suggestions": response.content[0].text,
        "created_at": date.today().isoformat()
    }
