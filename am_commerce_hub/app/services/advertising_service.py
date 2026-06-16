"""広告運用サービス（フロー1）。

Amazon Ads API → 日次レポート自動取得 → 広告データ収集・前処理
 → ACoS/ROAS目標値との乖離抽出 → AI(Claude)による分析と推論
 → キーワードごとの入札増減・停止案算出 → AIダッシュボードへ提案一覧
 → マーケ責任者の確認・一括承認（否認/手動修正可）
 → Ads API経由で入札額・予算を更新 → 外部トラフィックAttribution計測
 → 自社DBへ運用履歴・AIの成功率データ保存
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
    """日次レポート取得 → 指標保存 → AI分析 → 提案生成 → 一括承認タスク起票。"""
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

    # AIによる分析・推論（ACoS/ROAS目標との乖離をふまえた入札案）
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
            summary=f"{report_date} の入札提案 {len(recs)}件 一括承認",
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
    """承認された提案を Ads API へ反映。反映件数を返す。

    rec_ids 指定でその提案のみ、未指定なら proposed の全件を対象。
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

    # 外部トラフィック Attribution 計測（運用履歴に付随して取得）
    integ.attribution.fetch_attribution(report_date or date.today())
    return applied


def approve_all(session: Session, accept: bool = True) -> int:
    """マーケ責任者の一括承認。proposed を一括で approved/rejected に。"""
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
    session.flush()  # 同一セッション内の後続SELECTに反映させる（autoflush=Falseのため）
    return count
