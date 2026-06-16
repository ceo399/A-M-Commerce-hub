r"""SP-API ??????????? CLI????????????????????

 SpApiVendorOrders ?????????????????????
getPurchaseOrders ???????????????????????

PowerShell ??:
  $env:INTEGRATION_MODE="sandbox"
  $env:SP_API_CLIENT_ID="amzn1.application-oa2-client...."
  $env:SP_API_CLIENT_SECRET="...."
  $env:SP_API_REFRESH_TOKEN="Atzr|...."
  $env:SP_API_REGION="fe"
  .\.venv\Scripts\python.exe -m scripts.verify_sandbox

  # acknowledge ?????:
  .\.venv\Scripts\python.exe -m scripts.verify_sandbox --ack
"""
from __future__ import annotations

import argparse

from app.config import settings
from app.integrations.amazon.sp_api import SANDBOX_HOSTS
from app.integrations.live_adapters import (
    SpApiFbaInventory,
    SpApiSellerOrders,
    SpApiVendorOrders,
)


def _check_config() -> list[str]:
    problems: list[str] = []
    if settings.integration_mode != "sandbox":
        problems.append(f"INTEGRATION_MODE ? 'sandbox' ??????????: {settings.integration_mode!r}?")
    for name, val in [("SP_API_CLIENT_ID", settings.sp_api_client_id),
                      ("SP_API_CLIENT_SECRET", settings.sp_api_client_secret),
                      ("SP_API_REFRESH_TOKEN", settings.sp_api_refresh_token)]:
        if not val:
            problems.append(f"{name} ??????")
    return problems


def _hint(e: Exception) -> None:
    msg = str(e).lower()
    if any(k in msg for k in ("401", "unauthorized", "access_token", "invalid_grant")):
        print("  ???: LWA?????client_id / secret / refresh_token???????????")
    elif any(k in msg for k in ("403", "forbidden", "unauthorized access", "role")):
        print("  ???: ???? Vendor Orders API ?????????????????????????????")
    elif any(k in msg for k in ("connect", "timeout", "name or service", "getaddrinfo", "ssl")):
        print("  ???: ????????????????????????")


def main() -> int:
    ap = argparse.ArgumentParser(description="SP-API???????????")
    ap.add_argument("--ack", action="store_true", help="??PO??? acknowledge_po ?????")
    ap.add_argument("--seller", action="store_true", help="Seller(3P)?getOrders+FBA???????")
    args = ap.parse_args()

    print("== SP-API ??????????? ==")
    problems = _check_config()
    if problems:
        print("?????:")
        for p in problems:
            print("  -", p)
        print("\n????????????????????????")
        return 2

    host = SANDBOX_HOSTS.get(settings.sp_api_region, SANDBOX_HOSTS["fe"])
    print(f"region = {settings.sp_api_region}")
    print(f"host   = {host}")
    print(f"sandbox?? = {settings.sp_api_sandbox}")

    vendor = SpApiVendorOrders(settings)

    print("\n[1] LWA?????? + getPurchaseOrders ???? ...")
    try:
        pos = vendor.fetch_new_pos()
    except Exception as e:  # noqa: BLE001
        print("  ??:", type(e).__name__, e)
        _hint(e)
        return 1

    print(f"  ??: {len(pos)} ??PO???")
    for po in pos:
        print(f"   - PO {po.amazon_po_number}  ship_to={po.ship_to}  ??{len(po.lines)}?")
        for ln in po.lines:
            print(f"       SKU={ln.sku}  ASIN={ln.asin}  ??={ln.quantity}  ??={ln.unit_price}")

    if args.ack and pos:
        first = pos[0]
        confirmed = {ln.sku: ln.quantity for ln in first.lines if ln.sku}
        print(f"\n[2] acknowledge_po ??: {first.amazon_po_number} -> {confirmed}")
        try:
            vendor.acknowledge_po(first.amazon_po_number, confirmed)
            print("  ??: ???????????????????????")
        except Exception as e:  # noqa: BLE001
            print("  ??:", type(e).__name__, e)
            _hint(e)
            return 1

    if args.seller:
        if not settings.sp_api_seller_refresh_token:
            print("\n[Seller] SP_API_SELLER_REFRESH_TOKEN ???????????????")
        else:
            _sid = settings.sp_api_seller_client_id or settings.sp_api_client_id or ""
            _src = "Seller??" if settings.sp_api_seller_client_id else "Vendor???"
            print(f"\n  (Seller? client_id: ...{_sid[-6:]}  / {_src})")
            print("\n[3] Seller getOrders ???? ...")
            try:
                orders = SpApiSellerOrders(settings).fetch_orders()
                print(f"  ??: {len(orders)} ??3P?????")
                for o in orders:
                    print(f"   - ?? {o.amazon_order_id}  ??={o.order_status}  "
                          f"??={o.fulfillment_channel}  ??{len(o.lines)}?")
            except Exception as e:  # noqa: BLE001
                print("  ??:", type(e).__name__, e)
                _hint(e)
                return 1

            print("\n[4] FBA getInventorySummaries ???? ...")
            try:
                inv = SpApiFbaInventory(settings).fetch_inventory()
                print(f"  ??: {len(inv)} ??FBA????????")
                for r in inv:
                    print(f"   - SKU={r.seller_sku} ASIN={r.asin} ????={r.fulfillable} "
                          f"???={r.inbound} ??={r.reserved} ??={r.unfulfillable}")
            except Exception as e:  # noqa: BLE001
                print("  ??:", type(e).__name__, e)
                _hint(e)
                return 1

    print("\n== ????: getPurchaseOrders ??????????????DTO??????????? ==")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
