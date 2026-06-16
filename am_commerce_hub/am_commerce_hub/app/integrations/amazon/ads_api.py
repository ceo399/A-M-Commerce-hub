"""Amazon Ads API ?????????????????????????

Ads API????:
  Authorization: Bearer <LWA????>
  Amazon-Advertising-API-ClientId: <client_id>
  Amazon-Advertising-API-Scope: <profile_id>

(v3)????: ??(POST)?reportId???????????(GET)?
COMPLETED????? url(????)??gzip JSON??????????????
 run_report() ?????????????????(spec)?
v3??????
"""
from __future__ import annotations

import gzip
import json
import time
from typing import Callable

import httpx

from app.config import Settings
from app.integrations.amazon.auth import LwaTokenManager
from app.integrations.amazon.http import AmazonApiError, RateLimiter, ResilientHttpClient

ADS_HOSTS = {
    "na": "https://advertising-api.amazon.com",
    "eu": "https://advertising-api-eu.amazon.com",
    "fe": "https://advertising-api-fe.amazon.com",  # ???????
}

# ????????v3??????API?????????
_DONE = {"COMPLETED", "SUCCESS"}
_FAILED = {"FAILURE", "CANCELLED", "FAILED"}


class AdsApiClient:
    def __init__(self, settings: Settings, *, region: str = "fe",
                 transport: httpx.BaseTransport | None = None,
                 token_manager: LwaTokenManager | None = None,
                 rate_limiter: RateLimiter | None = None):
        base = ADS_HOSTS.get(region, ADS_HOSTS["fe"])
        self.http = ResilientHttpClient(
            base, transport=transport,
            rate_limiter=rate_limiter or RateLimiter(rate_per_sec=2, burst=5),
        )
        self.client_id = settings.ads_api_client_id or ""
        self.profile_id = settings.ads_profile_id or ""
        self.tokens = token_manager or LwaTokenManager(
            settings.ads_api_client_id or "", settings.ads_api_client_secret or "",
            settings.ads_api_refresh_token or "", transport=transport,
        )

    def _headers(self, extra: dict | None = None) -> dict:
        h = {
            "Authorization": f"Bearer {self.tokens.get_access_token()}",
            "Amazon-Advertising-API-ClientId": self.client_id,
            "Amazon-Advertising-API-Scope": self.profile_id,
            "content-type": "application/json",
        }
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


class AdsReportClient:
    """????????????????????????"""
    def __init__(self, ads: AdsApiClient, *,
                 reports_path: str = "/reporting/reports",
                 poll_interval: float = 5.0, poll_timeout: float = 900.0,
                 sleep: Callable[[float], None] = time.sleep,
                 clock: Callable[[], float] = time.monotonic,
                 download_transport: httpx.BaseTransport | None = None):
        self.ads = ads
        self.reports_path = reports_path
        self.poll_interval = poll_interval
        self.poll_timeout = poll_timeout
        self._sleep = sleep
        self._clock = clock
        self._download_transport = download_transport

    def create_report(self, spec: dict) -> str:
        """???????reportId ????"""
        resp = self.ads.post(self.reports_path, json=spec)
        data = resp.json()
        report_id = data.get("reportId") or data.get("reportingId") or data.get("id")
        if not report_id:
            raise AmazonApiError(resp.status_code, "reportId ??????????", resp.text)
        return report_id

    def get_report(self, report_id: str) -> dict:
        return self.ads.get(f"{self.reports_path}/{report_id}").json()

    def wait_for_report(self, report_id: str) -> str:
        """COMPLETED???????????????URL????"""
        deadline = self._clock() + self.poll_timeout
        while True:
            data = self.get_report(report_id)
            status = (data.get("status") or "").upper()
            if status in _DONE:
                url = data.get("url") or data.get("location")
                if not url:
                    raise AmazonApiError(200, "???????????URL???", json.dumps(data))
                return url
            if status in _FAILED:
                raise AmazonApiError(200, f"?????????: status={status}", json.dumps(data))
            if self._clock() >= deadline:
                raise TimeoutError(f"????????????? (report_id={report_id})")
            self._sleep(self.poll_interval)

    def download_report(self, url: str) -> list[dict]:
        """????URL??gzip JSON????????????"""
        # ??????URL???????S3????????transport??????
        if self._download_transport is not None:
            client = httpx.Client(transport=self._download_transport, timeout=60)
            try:
                resp = client.get(url)
            finally:
                client.close()
        else:
            resp = httpx.get(url, timeout=60)
        resp.raise_for_status()
        raw = resp.content
        try:
            raw = gzip.decompress(raw)
        except (OSError, gzip.BadGzipFile):
            pass  # ????????????
        text = raw.decode("utf-8")
        parsed = json.loads(text)
        return parsed if isinstance(parsed, list) else parsed.get("rows", parsed)

    def run_report(self, spec: dict) -> list[dict]:
        """?????????????????????"""
        report_id = self.create_report(spec)
        url = self.wait_for_report(report_id)
        return self.download_report(url)
