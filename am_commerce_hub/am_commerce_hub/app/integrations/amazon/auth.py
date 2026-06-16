"""LWA?Login with Amazon?OAuth2 ???????

SP-API / Ads API ???refresh_token ?? access_token ?????

"""
from __future__ import annotations

import threading
import time

import httpx

from app.integrations.amazon.http import ResilientHttpClient

LWA_TOKEN_URL = "https://api.amazon.com/auth/o2/token"


class LwaTokenManager:
    def __init__(self, client_id: str, client_secret: str, refresh_token: str, *,
                 safety_margin_sec: int = 60,
                 transport: httpx.BaseTransport | None = None,
                 http: ResilientHttpClient | None = None,
                 clock=time.time):
        if not (client_id and client_secret and refresh_token):
            raise ValueError("LWA?????client_id / client_secret / refresh_token???????")
        self.client_id = client_id
        self.client_secret = client_secret
        self.refresh_token = refresh_token
        self.safety_margin = safety_margin_sec
        self._http = http or ResilientHttpClient("https://api.amazon.com", transport=transport)
        self._clock = clock
        self._token: str | None = None
        self._expires_at: float = 0.0
        self._lock = threading.Lock()

    def get_access_token(self) -> str:
        with self._lock:
            if self._token and self._clock() < self._expires_at - self.safety_margin:
                return self._token
            resp = self._http.post(LWA_TOKEN_URL, data={
                "grant_type": "refresh_token",
                "refresh_token": self.refresh_token,
                "client_id": self.client_id,
                "client_secret": self.client_secret,
            }, headers={"content-type": "application/x-www-form-urlencoded"})
            data = resp.json()
            self._token = data["access_token"]
            self._expires_at = self._clock() + float(data.get("expires_in", 3600))
            return self._token

    def invalidate(self) -> None:
        with self._lock:
            self._token = None
            self._expires_at = 0.0
