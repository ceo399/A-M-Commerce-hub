# A&M Commerce Hub アプリイメージ
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# 依存を先に入れてレイヤキャッシュを効かせる
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# アプリ本体
COPY . .
RUN grep -q 'create_type=False' migrations/versions/b2c3d4e5f6a7_unified_inventory_ledger.py || (echo FIX_MISSING && exit 1)

# エントリポイントへ実行権限を付与し、非rootユーザーで実行
RUN chmod +x docker/entrypoint.sh \
    && useradd -m appuser \
    && chown -R appuser:appuser /app
USER appuser

EXPOSE 8000

# /health を叩いて死活監視（slimにcurlが無いためPythonで実施）
HEALTHCHECK --interval=30s --timeout=5s --start-period=40s --retries=5 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health')"

ENTRYPOINT ["./docker/entrypoint.sh"]
