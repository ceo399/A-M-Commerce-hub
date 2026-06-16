"""??????????????????????HTTP???????

Amazon??API?SP-API / Ads API????????????????:
- 429 / 5xx / ?????????????????????????
- Retry-After ?????????????
- ?????????????????????????
- 4xx?429????????????????

??????????httpx? transport ? sleep ???????
"""
from __future__ import annotations

import random
import threading
import time
from typing import Callable

import httpx


class AmazonApiError(Exception):
    """API???????????????????4xx????"""
    def __init__(self, status_code: int, message: str, body: str | None = None):
        super().__init__(f"[{status_code}] {message}")
        self.status_code = status_code
        self.body = body


class TransientError(Exception):
    """??????????????????????429/5xx/????????"""


class RateLimiter:
    """?????????rate_per_sec ????burst ????????"""
    def __init__(self, rate_per_sec: float, burst: int | None = None,
                 clock: Callable[[], float] = time.monotonic,
                 sleep: Callable[[float], None] = time.sleep):
        self.rate = float(rate_per_sec)
        self.capacity = float(burst if burst is not None else max(1, int(rate_per_sec)))
        self._tokens = self.capacity
        self._last = clock()
        self._clock = clock
        self._sleep = sleep
        self._lock = threading.Lock()

    def acquire(self) -> None:
        with self._lock:
            now = self._clock()
            self._tokens = min(self.capacity, self._tokens + (now - self._last) * self.rate)
            self._last = now
            if self._tokens < 1.0:
                wait = (1.0 - self._tokens) / self.rate
                self._sleep(wait)
                self._tokens = 0.0
                self._last = self._clock()
            else:
                self._tokens -= 1.0


class ResilientHttpClient:
    def __init__(self, base_url: str = "", *, timeout: float = 30.0, max_retries: int = 5,
                 backoff_base: float = 0.5, backoff_max: float = 30.0,
                 rate_limiter: RateLimiter | None = None,
                 default_headers: dict | None = None,
                 transport: httpx.BaseTransport | None = None,
                 sleep: Callable[[float], None] = time.sleep):
        self._client = httpx.Client(base_url=base_url, timeout=timeout,
                                    headers=default_headers or {}, transport=transport)
        self.max_retries = max_retries
        self.backoff_base = backoff_base
        self.backoff_max = backoff_max
        self.rate_limiter = rate_limiter
        self._sleep = sleep

    def _backoff(self, attempt: int, retry_after: float | None) -> float:
        if retry_after is not None:
            return retry_after
        cap = min(self.backoff_max, self.backoff_base * (2 ** attempt))
        return cap * (0.5 + random.random() * 0.5)  # ???????

    @staticmethod
    def _retry_after(resp: httpx.Response) -> float | None:
        val = resp.headers.get("Retry-After")
        if not val:
            return None
        try:
            return float(val)
        except ValueError:
            return None

    def request(self, method: str, path: str, *, headers: dict | None = None, **kw) -> httpx.Response:
        last_exc: Exception | None = None
        for attempt in range(self.max_retries + 1):
            if self.rate_limiter:
                self.rate_limiter.acquire()
            try:
                resp = self._client.request(method, path, headers=headers, **kw)
            except httpx.TransportError as e:  # ??/???????
                last_exc = e
                if attempt < self.max_retries:
                    self._sleep(self._backoff(attempt, None))
                    continue
                raise TransientError(f"network error after {attempt+1} tries: {e}") from e

            if resp.status_code == 429 or resp.status_code >= 500:
                if attempt < self.max_retries:
                    self._sleep(self._backoff(attempt, self._retry_after(resp)))
                    continue
                raise TransientError(f"giving up after {attempt+1} tries (status={resp.status_code})")

            if resp.status_code >= 400:  # 429???4xx???????
                raise AmazonApiError(resp.status_code, resp.reason_phrase, resp.text)

            return resp
        # ???????
        raise TransientError(str(last_exc) if last_exc else "unknown error")

    def get(self, path: str, **kw) -> httpx.Response:
        return self.request("GET", path, **kw)

    def post(self, path: str, **kw) -> httpx.Response:
        return self.request("POST", path, **kw)

    def put(self, path: str, **kw) -> httpx.Response:
        return self.request("PUT", path, **kw)

    def close(self) -> None:
        self._client.close()
