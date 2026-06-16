"""日次・定期ジョブの本体。

各ジョブは「自分でセッションを開き、冪等で、結果サマリを返す」部品として実装。
常駐スケジューラ（scripts/scheduler.py）からも、単発CLI（scripts/run_jobs.py）からも、
クラウドのcron/イベントからも同じ関数を呼べる。
"""
from __future__ import annotations

from datetime import date, timezone

from sqlalchemy import select

from app.db.base import utcnow
from app.db.models import ApprovalTask, ApprovalType, LoginChallenge
from app.db.session import session_scope
from app.logging_config import get_logger
from app.services import advertising_service, order_service

log = get_logger("amhub.jobs")


def ingest_orders_job() -> dict:
    """Vendor APIから新規POを取り込み、在庫照合・承認起票まで実行。

    ingest_new_pos はPO番号で重複排除するため、頻繁に走っても安全。
    """
    with session_scope() as s:
        pos = order_service.ingest_new_pos(s)
        nums = [p.amazon_po_number for p in pos]
    log.info("job=ingest_orders ingested=%d %s", len(nums), nums)
    return {"job": "ingest_orders", "ingested": nums}


def collect_ads_job(report_date: date | None = None) -> dict:
    """日次の広告レポート取得→AI分析→提案→承認起票。

    同一日の二重実行を防ぐため、その日のAD_BID承認タスク（ref_id=日付の序数）が
    既にあればスキップする。
    """
    report_date = report_date or date.today()
    with session_scope() as s:
        already = s.scalar(select(ApprovalTask.id).where(
            ApprovalTask.approval_type == ApprovalType.AD_BID,
            ApprovalTask.ref_id == report_date.toordinal()))
        if already:
            log.info("job=collect_ads status=skipped reason=already_collected date=%s", report_date)
            return {"job": "collect_ads", "status": "skipped",
                    "report_date": report_date.isoformat()}
        recs = advertising_service.collect_and_recommend(s, report_date=report_date)
        n = len(recs)
    log.info("job=collect_ads status=ok date=%s recommendations=%d", report_date, n)
    return {"job": "collect_ads", "status": "ok",
            "report_date": report_date.isoformat(), "recommendations": n}


def cleanup_expired_challenges_job() -> dict:
    """消費済み・期限切れの二段階認証チャレンジを削除（衛生ジョブ）。"""
    now = utcnow()
    removed = 0
    with session_scope() as s:
        for ch in s.scalars(select(LoginChallenge)).all():
            exp = ch.expires_at
            if exp.tzinfo is None:  # SQLiteはtz未保持のためUTC扱いに正規化
                exp = exp.replace(tzinfo=timezone.utc)
            if ch.consumed or now > exp:
                s.delete(ch)
                removed += 1
    log.info("job=cleanup_expired_challenges removed=%d", removed)
    return {"job": "cleanup_expired_challenges", "removed": removed}


# 名前 → ジョブ関数（CLI・スケジューラ共通の参照元）
JOBS = {
    "ingest_orders": ingest_orders_job,
    "collect_ads": collect_ads_job,
    "cleanup_challenges": cleanup_expired_challenges_job,
}
