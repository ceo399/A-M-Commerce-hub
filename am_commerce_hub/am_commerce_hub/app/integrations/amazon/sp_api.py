"""SP-API ?????????

SP-API? LWA ???????? x-amz-access-token????????
AWS SigV4 ??????????????(na)/??(eu)/??(fe)????fe?

URL???????????/????????????
getPurchaseOrders ??? path / body / ?????????
 live ???????????
"""
from __future__ import annotations

import httpx

from app.config import Settings
from app.integrations.amazon.auth import LwaTokenManager
from app.integrations.amazon.http import RateLimiter, ResilientHttpClient

REGION_HOSTS = {
    "na": "https://sellingpartnerapi-na.amazon.com",
    "eu": "https://sellingpartnerapi-eu.amazon.com",
    "fe": "https://sellingpartnerapi-fe.amazon.com",  # ???????
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
