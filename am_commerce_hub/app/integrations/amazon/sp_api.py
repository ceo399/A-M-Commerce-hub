"""SP-API クライアント基盤。

現行のSP-APIは LWA トークン（ヘッダ x-amz-access-token）のみで動作し、
AWS SigV4 署名は不要。リージョンは北米(na)/欧州(eu)/極東(fe)。日本はfe。

ここではリクエストの共通部分（ベースURL・認証ヘッダ・リトライ/レート制御）を提供する。
個々のオペレーション（getPurchaseOrders 等）の path / body / レスポンス整形は、
資格情報取得後に各 live アダプタ側で実装する。
"""
from __future__ import annotations

import httpx

from app.config import Settings
from app.integrations.amazon.auth import LwaTokenManager
from app.integrations.amazon.http import RateLimiter, ResilientHttpClient

REGION_HOSTS = {
    "na": "https://sellingpartnerapi-na.amazon.com",
    "eu": "https://sellingpartnerapi-eu.amazon.com",
    "fe": "https://sellingpartnerapi-fe.amazon.com",  # 日本を含む極東
}

SANDBOX_HOSTS = {
    "na": "https://sandbox.sellingpartnerapi-na.amazon.com",
    "eu": "https://sandbox.sellingpartnerapi-eu.amazon.com",
    "fe": "https://sandbox.sellingpartnerapi-fe.amazon.com",
}


class SpApiClient:
    def __init__(self, settings: Settings, *, region: str = "fe",
                 sandbox: bool | None = None,
                 transport: httpx.BaseTransport | None = None,
                 token_manager: LwaTokenManager | None = None,
                 rate_limiter: RateLimiter | None = None):
        use_sandbox = settings.sp_api_sandbox if sandbox is None else sandbox
        hosts = SANDBOX_HOSTS if use_sandbox else REGION_HOSTS
        base = hosts.get(region, hosts["fe"])
        self.sandbox = use_sandbox
        self.http = ResilientHttpClient(
            base, transport=transport,
            rate_limiter=rate_limiter or RateLimiter(rate_per_sec=5, burst=10),
        )
        self.tokens = token_manager or LwaTokenManager(
            settings.sp_api_client_id or "", settings.sp_api_client_secret or "",
            settings.sp_api_refresh_token or "", transport=transport,
        )

    def _headers(self, extra: dict | None = None) -> dict:
        h = {"x-amz-access-token": self.tokens.get_access_token(),
             "content-type": "application/json"}
        if extra:
            h.update(extra)
        return h

    def get(self, path: str, *, params: dict | None = None, headers: dict | None = None):
        return self.http.get(path, params=params, headers=self._headers(headers))

    def post(self, path: str, *, json: dict | None = None, headers: dict | None = None):
        return self.http.post(path, json=json, headers=self._headers(headers))

    def put(self, path: str, *, json: dict | None = None, headers: dict | None = None):
        return self.http.put(path, json=json, headers=self._headers(headers))

    def close(self) -> None:
        self.http.close()
