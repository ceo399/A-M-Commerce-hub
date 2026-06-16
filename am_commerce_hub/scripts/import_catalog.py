"""メーカーカタログ（CSV/Excel）を取り込む CLI。

実行例:
  python -m scripts.import_catalog --file メーカーカタログ_記入済み.xlsx
  python -m scripts.import_catalog --file catalog.csv --sheet 商品データ

テンプレート「メーカーカタログ_取込テンプレート.xlsx」の列定義に対応。
解析エラー行はスキップし、取り込めた行だけ登録する。
"""
from __future__ import annotations

import argparse
import json

from app.db.session import init_db, session_scope
from app.services import catalog_service


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--file", required=True, help="CSV または .xlsx のパス")
    p.add_argument("--sheet", default=None, help="xlsxの対象シート名（既定: 商品データ）")
    args = p.parse_args()

    init_db()
    with session_scope() as s:
        report = catalog_service.import_from_file(s, args.file, sheet=args.sheet)

    print(json.dumps(report, ensure_ascii=False, indent=2))
    pr = report["parse"]
    print(f"\n取込完了: 全{pr['total']}行 / 成功{pr['ok']} / "
          f"エラー{pr['errors']} / 警告{pr['warnings']}")
    print(f"  → 新製品(ドラフト生成){report['imported']['new_products']}件 / "
          f"廃盤{report['imported']['discontinued']}件")


if __name__ == "__main__":
    main()
