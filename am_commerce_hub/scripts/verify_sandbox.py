r"""SP-API サンドボックス疎通検証 CLI（実資格情報・実エンドポイントで実行）。

アプリ本体の SpApiVendorOrders をそのまま使い、実サンドボックスへ接続して
getPurchaseOrders を呼び、レスポンスのマッピング結果を表示する。

実行（PowerShell 例）:
  $env:INTEGRATION_MODE="sandbox"
  $env:SP_API_CLIENT_ID="amzn1.application-oa2-client...."
  $env:SP_API_CLIENT_SECRET="...."
  $env:SP_API_REFRESH_TOKEN="Atzr|...."
  $env:SP_API_REGION="fe"
  .\.venv\Scripts\python.exe -m scripts.verify_sandbox

  # acknowledge も試す場合:
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
        problems.append(f"INTEGRATION_MODE が 'sandbox' ではありません（現在: {settings.integration_mode!r}）")
    for name, val in [("SP_API_CLIENT_ID", settings.sp_api_client_id),
                      ("SP_API_CLIENT_SECRET", settings.sp_api_client_secret),
                      ("SP_API_REFRESH_TOKEN", settings.sp_api_refresh_token)]:
        if not val:
            problems.append(f"{name} が未設定です")
    return problems


def _hint(e: Exception) -> None:
    msg = str(e).lower()
    if any(k in msg for k in ("401", "unauthorized", "access_token", "invalid_grant")):
        print("  ヒント: LWA資格情報（client_id / secret / refresh_token）を確認してください。")
    elif any(k in msg for k in ("403", "forbidden", "unauthorized access", "role")):
        print("  ヒント: アプリに Vendor Orders API のロール付与／サンドボックス許可があるか確認してください。")
    elif any(k in msg for k in ("connect", "timeout", "name or service", "getaddrinfo", "ssl")):
        print("  ヒント: ネットワーク到達性とホスト名を確認してください。")


def main() -> int:
    ap = argparse.ArgumentParser(description="SP-APIサンドボックス疎通検証")
    ap.add_argument("--ack", action="store_true", help="先頭POに対し acknowledge_po も試行する")
    ap.add_argument("--seller", action="store_true", help="Seller(3P)のgetOrders+FBA在庫も検証する")
    args = ap.parse_args()

    print("== SP-API サンドボックス疎通検証 ==")
    problems = _check_config()
    if problems:
        print("設定エラー:")
        for p in problems:
            print("  -", p)
        print("\n上記を環境変数で設定してから再実行してください。")
        return 2

    host = SANDBOX_HOSTS.get(settings.sp_api_region, SANDBOX_HOSTS["fe"])
    print(f"region = {settings.sp_api_region}")
    print(f"host   = {host}")
    print(f"sandbox判定 = {settings.sp_api_sandbox}")

    vendor = SpApiVendorOrders(settings)

    print("\n[1] LWAトークン取得 + getPurchaseOrders 呼び出し ...")
    try:
        pos = vendor.fetch_new_pos()
    except Exception as e:  # noqa: BLE001
        print("  失敗:", type(e).__name__, e)
        _hint(e)
        return 1

    print(f"  成功: {len(pos)} 件のPOを取得")
    for po in pos:
        print(f"   - PO {po.amazon_po_number}  ship_to={po.ship_to}  明細{len(po.lines)}件")
        for ln in po.lines:
            print(f"       SKU={ln.sku}  ASIN={ln.asin}  数量={ln.quantity}  単価={ln.unit_price}")

    if args.ack and pos:
        first = pos[0]
        confirmed = {ln.sku: ln.quantity for ln in first.lines if ln.sku}
        print(f"\n[2] acknowledge_po 試行: {first.amazon_po_number} -> {confirmed}")
        try:
            vendor.acknowledge_po(first.amazon_po_number, confirmed)
            print("  成功: 受領応答を送信（サンドボックスのため実害なし）")
        except Exception as e:  # noqa: BLE001
            print("  失敗:", type(e).__name__, e)
            _hint(e)
            return 1

    if args.seller:
        if not settings.sp_api_seller_refresh_token:
            print("\n[Seller] SP_API_SELLER_REFRESH_TOKEN が未設定のためスキップします。")
        else:
            _sid = settings.sp_api_seller_client_id or settings.sp_api_client_id or ""
            _src = "Seller専用" if settings.sp_api_seller_client_id else "Vendorと共有"
            print(f"\n  (Seller用 client_id: ...{_sid[-6:]}  / {_src})")
            print("\n[3] Seller getOrders 呼び出し ...")
            try:
                orders = SpApiSellerOrders(settings).fetch_orders()
                print(f"  成功: {len(orders)} 件の3P注文を取得")
                for o in orders:
                    print(f"   - 注文 {o.amazon_order_id}  状態={o.order_status}  "
                          f"経路={o.fulfillment_channel}  明細{len(o.lines)}件")
            except Exception as e:  # noqa: BLE001
                print("  失敗:", type(e).__name__, e)
                _hint(e)
                return 1

            print("\n[4] FBA getInventorySummaries 呼び出し ...")
            try:
                inv = SpApiFbaInventory(settings).fetch_inventory()
                print(f"  成功: {len(inv)} 件のFBA在庫サマリを取得")
                for r in inv:
                    print(f"   - SKU={r.seller_sku} ASIN={r.asin} 販売可能={r.fulfillable} "
                          f"入庫中={r.inbound} 引当={r.reserved} 不良={r.unfulfillable}")
            except Exception as e:  # noqa: BLE001
                print("  失敗:", type(e).__name__, e)
                _hint(e)
                return 1

    print("\n== 検証完了: getPurchaseOrders が実サンドボックスで動作し、DTOへマッピングできました ==")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
