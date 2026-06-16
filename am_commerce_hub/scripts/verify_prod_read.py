r"""SP-API 本番(production) 読み取り専用 疎通検証 CLI。

ロール承認が揃った段階で、本番エンドポイントに対して **読み取りのみ** を実行し、
実データで Vendor PO / 3P注文 / FBA在庫 のマッピングを確認する。書き込み(ack/asn/invoice等)は一切行わない。

実行（PowerShell 例）:
  $env:INTEGRATION_MODE="live"          # ← liveで本番ホストになる
  $env:SP_API_REGION="fe"
  # Vendor(1P)
  $env:SP_API_CLIENT_ID="amzn1.application-oa2-client...."
  $env:SP_API_CLIENT_SECRET="...."
  $env:SP_API_REFRESH_TOKEN="Atzr|...."
  # Seller(3P) ※別アプリならこちら。共有なら未設定でVendor分を流用
  $env:SP_API_SELLER_CLIENT_ID="...."
  $env:SP_API_SELLER_CLIENT_SECRET="...."
  $env:SP_API_SELLER_REFRESH_TOKEN="Atzr|...."
  .\.venv\Scripts\python.exe -m scripts.verify_prod_read

注意:
- INTEGRATION_MODE=live のときのみ本番ホストへ接続する（sandboxでは実行しない）。
- 注文の購入者個人情報(PII)は Restricted Data Token(RDT) が必要で、本スクリプトは要求しない。
  そのため getOrders はヘッダ情報のみ取得できる（買い手氏名・住所等は対象外）。
"""
from __future__ import annotations

import argparse

from app.config import settings
from app.integrations.live_adapters import (
    SpApiFbaInventory,
    SpApiSellerOrders,
    SpApiVendorOrders,
)


def _check_config() -> list[str]:
    problems: list[str] = []
    if settings.integration_mode != "live":
        problems.append(
            f"INTEGRATION_MODE が 'live' ではありません（現在: {settings.integration_mode!r}）。"
            "本番読み取り検証は live のときのみ実行します。"
        )
    for name, val in [("SP_API_CLIENT_ID", settings.sp_api_client_id),
                      ("SP_API_CLIENT_SECRET", settings.sp_api_client_secret),
                      ("SP_API_REFRESH_TOKEN", settings.sp_api_refresh_token)]:
        if not val:
            problems.append(f"{name}(Vendor) が未設定です")
    return problems


def _hint(e: Exception) -> None:
    msg = str(e).lower()
    if "403" in msg or "forbidden" in msg or "unauthorized" in msg:
        print("  ヒント: ロール未付与/未承認の可能性。Developer Centralで対象APIのロールを確認してください。")
    elif "invalid_grant" in msg or "401" in msg:
        print("  ヒント: refresh_token と client_id/secret の組み合わせを確認してください。")


def main() -> int:
    ap = argparse.ArgumentParser(description="SP-API 本番 読み取り専用 検証")
    ap.add_argument("--seller", action="store_true", help="3P(getOrders)とFBA在庫も検証する")
    args = ap.parse_args()

    print("== SP-API 本番(production) 読み取り専用 検証 ==")
    print("※ 書き込みは一切行いません（GETのみ）")
    print(f"region = {settings.sp_api_region}")
    problems = _check_config()
    if problems:
        print("設定エラー:")
        for p in problems:
            print(f"  - {p}")
        return 2

    print("\n[1] Vendor getPurchaseOrders（本番・読み取り）...")
    try:
        pos = SpApiVendorOrders(settings).fetch_new_pos()
        print(f"  成功: {len(pos)} 件のPOを取得")
        for po in pos[:5]:
            print(f"   - PO {po.amazon_po_number}  明細{len(po.lines)}件")
    except Exception as e:  # noqa: BLE001
        print("  失敗:", type(e).__name__, e)
        _hint(e)
        return 1

    if args.seller:
        if not settings.sp_api_seller_refresh_token:
            print("\n[Seller] SP_API_SELLER_REFRESH_TOKEN が未設定のためスキップします。")
        else:
            print("\n[2] Seller getOrders（本番・読み取り）...")
            try:
                orders = SpApiSellerOrders(settings).fetch_orders()
                print(f"  成功: {len(orders)} 件の3P注文を取得")
                for o in orders[:5]:
                    print(f"   - 注文 {o.amazon_order_id}  状態={o.order_status}  経路={o.fulfillment_channel}")
            except Exception as e:  # noqa: BLE001
                print("  失敗:", type(e).__name__, e)
                _hint(e)
                return 1

            print("\n[3] FBA getInventorySummaries（本番・読み取り）...")
            try:
                inv = SpApiFbaInventory(settings).fetch_inventory()
                print(f"  成功: {len(inv)} 件のFBA在庫サマリを取得")
                for r in inv[:5]:
                    print(f"   - SKU={r.seller_sku} 販売可能={r.fulfillable} 入庫中={r.inbound} "
                          f"引当={r.reserved} 不良={r.unfulfillable}")
            except Exception as e:  # noqa: BLE001
                print("  失敗:", type(e).__name__, e)
                _hint(e)
                return 1

    print("\n== 本番読み取り検証 完了 ==")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
