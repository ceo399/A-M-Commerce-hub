"""ジョブの単発実行CLI。

クラウドのスケジューラ（cron / EventBridge等）や手動から1回だけ叩く用途。
  python -m scripts.run_jobs --job collect_ads
  python -m scripts.run_jobs --job all
  python -m scripts.run_jobs --list
"""
from __future__ import annotations

import argparse
import json

from app.config import settings
from app.db.session import init_db
from app.services import jobs


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--job", choices=list(jobs.JOBS) + ["all"], help="実行するジョブ名")
    p.add_argument("--list", action="store_true", help="ジョブ一覧を表示")
    args = p.parse_args()

    if args.list or not args.job:
        print("利用可能なジョブ:")
        for name in jobs.JOBS:
            print(f"  - {name}")
        return

    if settings.auto_create_tables:
        init_db()  # 本番(Alembic運用)では schema は既に存在

    if args.job == "all":
        out = [fn() for fn in jobs.JOBS.values()]
    else:
        out = jobs.JOBS[args.job]()
    print(json.dumps(out, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
