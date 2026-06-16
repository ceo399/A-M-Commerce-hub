#!/bin/sh
# 起動時: (任意で)DB待機→マイグレーション→uvicorn起動。
# RUN_MIGRATIONS=false で移行をスキップ（複数レプリカ運用時など）。
set -e

if [ "${RUN_MIGRATIONS:-true}" = "true" ]; then
  echo "[entrypoint] waiting for database..."
  python - <<'PY'
import os, sys, time
from sqlalchemy import create_engine, text
url = os.getenv("DATABASE_URL", "")
if not url.startswith("postgres"):
    sys.exit(0)  # SQLite等は待機不要
for i in range(30):
    try:
        with create_engine(url).connect() as c:
            c.execute(text("SELECT 1"))
        print("[entrypoint] database is ready")
        sys.exit(0)
    except Exception as ex:
        print(f"[entrypoint] db not ready ({i+1}/30): {ex}")
        time.sleep(2)
print("[entrypoint] database wait timed out")
sys.exit(1)
PY
  echo "[entrypoint] running migrations (alembic upgrade head)..."
  alembic upgrade head
fi

echo "[entrypoint] starting uvicorn..."
exec uvicorn app.api.main:app --host 0.0.0.0 --port 8000 --workers "${WEB_CONCURRENCY:-1}"
