"""?????????: ?????????????????????

: python -m tests.test_jobs
"""
from __future__ import annotations

import time
import warnings
from datetime import date, timedelta

warnings.filterwarnings("ignore")

from sqlalchemy import select

from app.db.base import utcnow
from app.db.models import ApprovalType, LoginChallenge, TwoFactorMethod
from app.db.session import init_db, session_scope
from app.services import jobs


def _fresh_db():
    # ??????DB??????????????DB????
    import os
    from app.config import settings
    from app.db.session import engine
    engine.dispose()  # ???????????????????????????????
    if settings.database_url.startswith("sqlite") and "///" in settings.database_url:
        path = settings.database_url.split("///", 1)[1]
        if os.path.exists(path):
            os.remove(path)
    init_db()


def test_collect_ads_is_idempotent_per_day():
    _fresh_db()
    r1 = jobs.collect_ads_job(report_date=date(2026, 6, 1))
    r2 = jobs.collect_ads_job(report_date=date(2026, 6, 1))   # ?????
    assert r1["status"] == "ok" and r1["recommendations"] > 0, r1
    assert r2["status"] == "skipped", r2
    # ?????????
    r3 = jobs.collect_ads_job(report_date=date(2026, 6, 2))
    assert r3["status"] == "ok", r3


def test_ingest_orders_runs():
    _fresh_db()
    out = jobs.ingest_orders_job()
    assert out["job"] == "ingest_orders" and isinstance(out["ingested"], list)


def test_cleanup_removes_expired_and_consumed():
    _fresh_db()
    with session_scope() as s:
        s.add(LoginChallenge(challenge_uid="expired", user_id=1, code_hash="x",
                             method=TwoFactorMethod.EMAIL,
                             expires_at=utcnow() - timedelta(minutes=10)))
        s.add(LoginChallenge(challenge_uid="consumed", user_id=1, code_hash="x",
                             method=TwoFactorMethod.EMAIL,
                             expires_at=utcnow() + timedelta(minutes=10), consumed=True))
        s.add(LoginChallenge(challenge_uid="valid", user_id=1, code_hash="x",
                             method=TwoFactorMethod.EMAIL,
                             expires_at=utcnow() + timedelta(minutes=10)))
    out = jobs.cleanup_expired_challenges_job()
    assert out["removed"] == 2, out
    with session_scope() as s:
        remaining = [c.challenge_uid for c in s.scalars(select(LoginChallenge)).all()]
    assert remaining == ["valid"], remaining


def test_scheduler_actually_fires():
    from apscheduler.schedulers.background import BackgroundScheduler
    from apscheduler.triggers.interval import IntervalTrigger
    hits = {"n": 0}
    sch = BackgroundScheduler()
    sch.add_job(lambda: hits.__setitem__("n", hits["n"] + 1), IntervalTrigger(seconds=1))
    sch.start()
    time.sleep(2.3)
    sch.shutdown()
    assert hits["n"] >= 1, hits


if __name__ == "__main__":
    test_collect_ads_is_idempotent_per_day()
    test_ingest_orders_runs()
    test_cleanup_removes_expired_and_consumed()
    test_scheduler_actually_fires()
    print("OK: ???/?????????????")
