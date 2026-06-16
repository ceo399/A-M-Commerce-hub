"""Amazon??????????httpx MockTransport?????????????

- LWA????: ???????????????
- ??HTTP: 429/5xx/??????????Retry-After???4xx????
- ?????: ?????????????
- Ads???????: ?????????gzip JSON??????
"""
from __future__ import annotations

import gzip
import json
import warnings

warnings.filterwarnings("ignore")

import httpx

from app.config import Settings
from app.integrations.amazon.ads_api import AdsApiClient, AdsReportClient
from app.integrations.amazon.auth import LwaTokenManager
from app.integrations.amazon.http import (
    AmazonApiError, RateLimiter, ResilientHttpClient, TransientError,
)

NOOP = lambda *_: None


# ---- LWA????: ???????? ----
def test_lwa_token_cache_and_refresh():
    calls = {"n": 0}
    def handler(req: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(200, json={"access_token": f"tok-{calls['n']}", "expires_in": 3600})
    t = httpx.MockTransport(handler)
    clock = {"t": 1000.0}
    mgr = LwaTokenManager("cid", "secret", "rt", transport=t, clock=lambda: clock["t"])
    assert mgr.get_access_token() == "tok-1"
    assert mgr.get_access_token() == "tok-1"      # ??????HTTP??????
    assert calls["n"] == 1
    clock["t"] += 4000                              # ?????
    assert mgr.get_access_token() == "tok-2"        # ?????
    assert calls["n"] == 2


# ---- ??HTTP: ????Retry-After?4xx?????????????? ----
def test_retry_on_429_then_success():
    seq = iter([429, 200])
    def handler(req):
        code = next(seq)
        if code == 429:
            return httpx.Response(429, headers={"Retry-After": "0"}, text="slow down")
        return httpx.Response(200, json={"ok": True})
    c = ResilientHttpClient("https://x", transport=httpx.MockTransport(handler), sleep=NOOP)
    assert c.get("/p").json() == {"ok": True}


def test_retry_on_500_then_success():
    seq = iter([500, 200])
    c = ResilientHttpClient("https://x", sleep=NOOP,
        transport=httpx.MockTransport(lambda r: httpx.Response(next(seq), json={"ok": 1})))
    assert c.get("/p").status_code == 200


def test_4xx_raises_without_retry():
    calls = {"n": 0}
    def handler(r):
        calls["n"] += 1
        return httpx.Response(400, text="bad request")
    c = ResilientHttpClient("https://x", transport=httpx.MockTransport(handler), sleep=NOOP)
    try:
        c.get("/p"); assert False, "should raise"
    except AmazonApiError as e:
        assert e.status_code == 400 and calls["n"] == 1   # ????????


def test_network_error_then_success():
    state = {"first": True}
    def handler(r):
        if state["first"]:
            state["first"] = False
            raise httpx.ConnectError("boom")
        return httpx.Response(200, json={"ok": 1})
    c = ResilientHttpClient("https://x", transport=httpx.MockTransport(handler), sleep=NOOP)
    assert c.get("/p").status_code == 200


def test_gives_up_after_max_retries():
    c = ResilientHttpClient("https://x", max_retries=2, sleep=NOOP,
        transport=httpx.MockTransport(lambda r: httpx.Response(503)))
    try:
        c.get("/p"); assert False
    except TransientError:
        pass


# ---- ?????: ???????????????sleep???? ----
def test_rate_limiter_throttles():
    waits = []
    clock = {"t": 0.0}
    rl = RateLimiter(rate_per_sec=1, burst=1, clock=lambda: clock["t"],
                     sleep=lambda s: waits.append(s))
    rl.acquire()   # 1??: ??
    rl.acquire()   # 2??: ???????????
    assert waits and waits[0] > 0


# ---- Ads????: ?????????gzip JSON ?????? ----
def test_ads_report_create_poll_download():
    poll = {"n": 0}
    dl_url = "https://reports-bucket.example.com/rep-1.json.gz"
    payload = gzip.compress(json.dumps(
        [{"keywordId": "KW1", "keyword": "???", "impressions": 100,
          "clicks": 10, "cost": 300.0, "sales": 1500.0, "purchases": 1, "bid": 45.0}]
    ).encode("utf-8"))

    def handler(req: httpx.Request) -> httpx.Response:
        path = req.url.path
        if path == "/auth/o2/token":
            return httpx.Response(200, json={"access_token": "tok", "expires_in": 3600})
        if req.method == "POST" and path == "/reporting/reports":
            return httpx.Response(200, json={"reportId": "rep-1", "status": "PENDING"})
        if path == "/reporting/reports/rep-1":
            poll["n"] += 1
            if poll["n"] < 2:
                return httpx.Response(200, json={"status": "PENDING"})
            return httpx.Response(200, json={"status": "COMPLETED", "url": dl_url})
        if req.url.host == "reports-bucket.example.com":
            return httpx.Response(200, content=payload)
        return httpx.Response(404, text=f"unexpected {req.method} {req.url}")

    transport = httpx.MockTransport(handler)
    s = Settings(ads_api_client_id="cid", ads_api_client_secret="sec",
                 ads_api_refresh_token="rt", ads_profile_id="profile-1")
    ads = AdsApiClient(s, region="fe", transport=transport)
    reports = AdsReportClient(ads, sleep=NOOP, poll_interval=0, download_transport=transport)
    rows = reports.run_report({"name": "t"})
    assert len(rows) == 1 and rows[0]["keywordId"] == "KW1"
    assert poll["n"] == 2   # PENDING?COMPLETED ??2??????


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
    print("OK: Amazon????????")
