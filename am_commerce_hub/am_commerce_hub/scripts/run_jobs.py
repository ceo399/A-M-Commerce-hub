"""????????CLI?

cron / EventBridge???????1????????
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
    p.add_argument("--job", choices=list(jobs.JOBS) + ["all"], help="????????")
    p.add_argument("--list", action="store_true", help="????????")
    args = p.parse_args()

    if args.list or not args.job:
        print("????????:")
        for name in jobs.JOBS:
            print(f"  - {name}")
        return

    if settings.auto_create_tables:
        init_db()  # ??(Alembic??)?? schema ?????

    if args.job == "all":
        out = [fn() for fn in jobs.JOBS.values()]
    else:
        out = jobs.JOBS[args.job]()
    print(json.dumps(out, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
