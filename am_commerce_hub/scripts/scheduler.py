"""?????????cron????????????????

??: python -m scripts.scheduler
Docker?????????1?????????????????
?????????????????????????1?????????
"""
from __future__ import annotations

from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.cron import CronTrigger

from app.config import settings
from app.db.session import init_db
from app.logging_config import get_logger
from app.services import jobs

log = get_logger("amhub.scheduler")


def _timezone():
    try:
        from zoneinfo import ZoneInfo
        return ZoneInfo(settings.scheduler_timezone)
    except Exception:
        log.warning("timezone %s ????????(UTC)????????", settings.scheduler_timezone)
        return None


def main() -> None:
    if settings.auto_create_tables:
        init_db()  # ?????????(Alembic??)?? schema ?????

    tz = _timezone()
    sched = BlockingScheduler(timezone=tz) if tz else BlockingScheduler()

    schedule = [
        ("ingest_orders", jobs.ingest_orders_job, settings.orders_ingest_cron),
        ("collect_ads", jobs.collect_ads_job, settings.ads_collect_cron),
        ("cleanup_challenges", jobs.cleanup_expired_challenges_job, settings.cleanup_cron),
    ]
    for job_id, func, cron in schedule:
        sched.add_job(func, CronTrigger.from_crontab(cron, timezone=tz),
                      id=job_id, max_instances=1, coalesce=True, misfire_grace_time=3600)
        log.info("scheduled job=%s cron='%s' tz=%s", job_id, cron, settings.scheduler_timezone)

    log.info("scheduler started (mode=%s)", settings.integration_mode)
    try:
        sched.start()
    except (KeyboardInterrupt, SystemExit):
        log.info("scheduler stopping")


if __name__ == "__main__":
    main()
