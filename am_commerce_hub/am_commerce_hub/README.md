# A&M Commerce Hub

商品管理・在庫管理・受注管理・入出荷管理・広告運用管理・商品情報の自動作成を
一体化したカスタムシステムの基盤。添付の3フロー図をそのまま実装している。

## 設計の核：ポート＆アダプタ（Amazon APIを後付けできる構造）

Amazonの各API（SP-API / Vendor / Ads / Catalog / Listings）と外部連携（AISハブ、AI）は、
すべて「ポート（抽象インターフェース）」の裏に隔離してある。

```
サービス層（業務ロジック）── 依存 ──▶ ポート（抽象IF）◀── 実装 ── モックアダプタ（今）
                                                      ◀── 実装 ── 本番アダプタ（API取得後）
```

- **今**：`INTEGRATION_MODE=mock`（既定）。全フローがエンドツーエンドで動く。
- **API取得後**：`app/integrations/live_adapters.py` を実装し、`registry._build_live()` で
  組み立て、`INTEGRATION_MODE=live` にするだけ。**サービス層・DB・APIは無改修**。

これにより「Amazon API以外は今すぐ着手」が成立する。商品・在庫・受注・発注・承認の
ロジックとデータモデルは本番品質で先行構築でき、Amazon接続部だけ後から差し込める。

## ディレクトリ構成

```
app/
  config.py                 設定（資格情報は任意。未設定ならモック）
  db/
    base.py / session.py    SQLAlchemy基盤
    models.py               全ドメインモデル（3フロー統合）
  integrations/
    ports.py                抽象IF（差し替え点）
    mock_adapters.py        モック実装（現状）
    live_adapters.py        本番実装スケルトン（API取得後に中身を実装）
    registry.py             モック/本番の切替ファクトリ
  services/
    inventory_service.py    在庫（入庫・引当・出庫＋台帳）
    order_service.py        フロー2：受注→照合→出荷→請求
    purchasing_service.py   フロー2：在庫不足→メーカー発注→入荷
    catalog_service.py      フロー3：カタログ照合→AI生成→出品
    advertising_service.py  フロー1：広告収集→AI分析→承認→反映
    approval_service.py     横断：人手承認ゲート
  api/main.py               FastAPI（運用バックエンド）
scripts/run_demo.py         3フロー通し実行デモ
tests/test_core.py          在庫不変条件のテスト
```

## セットアップと実行

```bash
pip install -r requirements.txt

# 3フローを通しでデモ実行（Amazon資格情報なしで動く）
python -m scripts.run_demo

# テスト
python -m tests.test_core

# API起動（ http://localhost:8000/docs ）
uvicorn app.api.main:app --reload
```

## フローとコードの対応

| フロー図 | 実装 |
|---|---|
| ① 広告運用 | `advertising_service.collect_and_recommend` → `approve_all` → `apply_recommendations` |
| ② 受注→出荷→請求 | `order_service.ingest_new_pos`（照合・PO Ack・承認起票）→ `confirm_shipment`（CSV/ASN/Invoice） |
| ②の在庫不足分岐 | `purchasing_service.create_supplier_po` → 承認 → `receive_goods` |
| ③ カタログ→出品 | `catalog_service.sync_catalog`（照合・AI生成・承認起票）→ `publish_draft`（Listings出品） |

人手の承認ゲート（日次出荷承認・発注承認・MD出品承認・広告一括承認）は
`ApprovalTask` で統一管理し、`/approvals` から確認・決裁できる。

## AI（Claude）連携

`MockAI` がルールベースのスタブ。`ANTHROPIC_API_KEY` 設定後、同じ `AIPort` を実装した
本番クラスへ差し替えれば、出品文生成・広告分析が実モデルに切り替わる。
広告分析の目標値（ACoS 20% / 日予算 ¥11,000）は `config.py` で既存運用に合わせて初期化済み。

## API取得後の差し込み手順（要約）

1. SP-API / Ads API の資格情報を取得し `.env` に設定
2. `live_adapters.py` の各 `NotImplementedError` を本物の呼び出しに置換
3. `registry._build_live()` を有効化
4. `INTEGRATION_MODE=live` で起動

## 本番フロントエンド（実装済み・実API接続）

`app/web/index.html` — FastAPI が同一オリジンで配信する運用ダッシュボード本体（実APIに接続）。
プロトタイプのデザインを引き継ぎ、ライト基調を既定に、右上ボタンでライト／ダーク切替（設定はブラウザに保存）。

機能:
- ログイン（2段階認証）→ JWT 取得 → ロールに応じて操作を出し分け（承認ボタンのロック等）。
- ダッシュボード（承認待ち・在庫アラート・広告提案・平均ACoS を実データ集計）。
- 承認キュー（一覧・種別フィルタ・承認/否認 → 後続処理を実行）。
- 在庫（SKU/商品名/状態・発注点割れのハイライト・検索）。
- カタログ・出品（出品ドラフト一覧 ＋ CSV/Excel 取込アップロード）。
- 受注（PO一覧・状態）／広告提案（入札増減・停止案・一括承認で Ads へ反映）。

起動（必ずサーバ経由で開くこと。file:// で直接開くと fetch が動かない）:
```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
Remove-Item .\am_commerce_hub.db -ErrorAction SilentlyContinue
.\.venv\Scripts\python.exe -m scripts.dev_seed      # 動作確認用のアカウント・サンプルを投入
$env:DEV_ECHO_OTP="1"                                # ログイン時に2FAコードを自動表示（開発のみ）
.\.venv\Scripts\python.exe -m uvicorn app.api.main:app --reload
# ブラウザで http://localhost:8000/ を開く（/app/ へ自動リダイレクト）
```
ログイン（パスワードは全て `DevPass!234`）: `admin@am-teams.com`（管理者）/ `logi@am-teams.com`（物流）/
`buy@am-teams.com`（購買）/ `md@am-teams.com`（MD）/ `mkt@am-teams.com`（マーケ）。
アカウントを変えると、実際の権限による操作の出し分けを確認できる。

> プロトタイプ（モックデータ版）は `dashboard_prototype.html` として別途残してある（デザイン確認用）。

## ダッシュボードUI（プロトタイプ）

`dashboard_prototype.html` — 中核画面の高忠実度プロトタイプ（単一HTML・モックデータ・バックエンド不要）。
ブラウザで開くだけで動作する。実物を作る前に画面構成・情報設計・操作フローをすり合わせるための叩き台。

- 画面: ダッシュボード（KPI/承認待ち/アクティビティ）、承認キュー、在庫、出品ドラフト、受注、広告提案。
- 右上の「表示ロール」を切り替えると、権限による操作の出し分け（承認ボタンのロック等）を体感できる。
- デザインは A&M GROUP のトークン（ダーク基調・ゴールド #C9A84C・ブルーグレー #a0b4d0・Noto Sans JP・ヘアライン罫線）に準拠。
- データはすべてモック。次段階で本APIへ配線する。

## Amazon 実接続の足場（実装済み・資格情報待ち）

横断的に難しい部分は実装・検証済みで、残りは各オペレーションの個別実装のみ。
`app/integrations/amazon/` に集約:

- **LWAトークン管理**（`auth.py`）: refresh_tokenからaccess_tokenを取得し、安全マージン付きで
  キャッシュ・自動更新。SP-API / Ads API 共通。
- **堅牢HTTPクライアント**（`http.py`）: 429/5xx/ネットワークエラーを指数バックオフ＋ジッタで再試行、
  `Retry-After` を尊重、トークンバケットで事前スロットリング、429以外の4xxは即エラー。
- **SP-APIクライアント**（`sp_api.py`）: リージョン別ホスト（日本=fe）＋ `x-amz-access-token` ヘッダ。
  現行SP-APIはLWAのみで動作しAWS SigV4署名は不要。
- **Ads APIクライアント＋レポート枠組み**（`ads_api.py`）: 必要ヘッダ（Bearer / ClientId / Scope）を付与。
  レポートを「作成→状態ポーリング→gzip JSONダウンロード」する非同期処理 `run_report()` を提供。

`INTEGRATION_MODE=live` で本番アダプタ（`live_adapters.py`）が選択され、上記クライアントが組み上がる。
各オペレーション（getPurchaseOrders / putListingsItem / 入札更新 等）の path・body・レスポンス整形は
`NotImplementedError` で差し込み箇所を明示してあり、**資格情報と実APIの契約が揃い次第そこだけ埋める**。
Ads の `fetch_daily_report` はレポート枠組みに配線済みで、レポート定義(spec)と列名のみ調整する。

設定（`.env`）: `SP_API_*` / `ADS_API_*` の資格情報、`SP_API_REGION` / `ADS_API_REGION`（既定 fe）。

検証: `tests/test_amazon_scaffold.py` が httpx の MockTransport で実APIを模擬し、トークンの
キャッシュ/更新、各種再試行、Retry-After、レート制御、レポートの作成→ポーリング→DLを確認（実資格情報不要）。

## 日次処理の自動化（実装済み）

ジョブ本体（`app/services/jobs.py`）は「自分でセッションを開き・冪等・結果を返す」部品として実装し、
3通りの起動方法から同じ関数を呼べる。

ジョブ:
- **ingest_orders** — Vendor APIから新規POを取込・在庫照合・承認起票（PO番号で重複排除＝頻繁実行も安全）
- **collect_ads** — 日次の広告レポート取得→AI分析→提案→承認起票（同日二重実行はスキップ）
- **cleanup_challenges** — 期限切れ・消費済みの二段階認証コードを削除

起動方法:
```bash
# 1) 常駐スケジューラ（cronで定期実行。Dockerでは scheduler サービスとして放置で稼働）
python -m scripts.scheduler

# 2) 単発実行（クラウドのcron/EventBridge や手動から1回だけ）
python -m scripts.run_jobs --job collect_ads
python -m scripts.run_jobs --job all
python -m scripts.run_jobs --list
```

スケジュール（cron）と timezone は `.env` で変更可能。既定:
`ORDERS_INGEST_CRON=0 * * * *`（毎時）、`ADS_COLLECT_CRON=0 6 * * *`（毎日6:00）、
`CLEANUP_CRON=30 3 * * *`（毎日3:30）、`SCHEDULER_TIMEZONE=Asia/Tokyo`。

Docker構成では `scheduler` サービスが app の起動（＝スキーマ作成）後に立ち上がり、放置で定期実行する。
**スケジューラは必ず1インスタンスだけ**動かすこと（複数だと二重実行になる）。
クラウドのマネージドscheduler（EventBridge等）を使う場合は、常駐サービスの代わりに
`run_jobs.py` を定時起動する構成にできる。

## Docker でクラウドへ（実装済み）

アプリと PostgreSQL をまとめて起動できる。Docker / Docker Compose があれば1コマンド。

```bash
cp .env.example .env          # JWT_SECRET などを実値に（POSTGRES_PASSWORD等も）
docker compose up --build     # db(PostgreSQL16) + app を起動
# → http://localhost:8000/docs / health: http://localhost:8000/health
docker compose down           # 停止（DBを消すなら -v）
```

起動の流れ（`docker/entrypoint.sh`）: DBの起動を待機 → `alembic upgrade head` でスキーマ作成 →
uvicorn 起動。コンテナでは `AUTO_CREATE_TABLES=false` とし、スキーマはAlembicが管理する。

構成:
- **db**: `postgres:16`。データは名前付きボリューム `pgdata` に永続化。`pg_isready` でヘルスチェックし、
  app は db が健全になってから起動（`depends_on: condition: service_healthy`）。
- **app**: 本リポジトリの `Dockerfile`（`python:3.12-slim`、非rootユーザー、`/health` 死活監視）。
  DBへは service 名 `db` で接続。

**クラウドへの載せ方の例**:
- VM（EC2等）に Docker を入れ、`docker compose up -d` で常駐。
- マネージドな PaaS（Render / Railway / Fly / ECS 等）に `Dockerfile` をデプロイし、
  DBはマネージドPostgreSQLを用意して `DATABASE_URL` を環境変数で渡す（その場合 compose の db は不要）。
- いずれも秘密情報（`JWT_SECRET`・Amazon資格情報・`ANTHROPIC_API_KEY`）は各環境の
  シークレット機構で注入し、イメージには焼き込まない（`.dockerignore` で `.env` を除外済み）。

> 注: スケール（複数レプリカ）時はマイグレーションの二重実行を避けるため、
> 移行は1回だけ実行し各appは `RUN_MIGRATIONS=false` で起動する運用が安全。

## 本番化の足場（実装済み）

クラウド配置・PostgreSQL移行の前提となる土台を整備済み。

**設定（.env）**: `.env.example` をコピーして `.env` を作成すると、起動時に自動で読み込む
（`python-dotenv`）。秘密情報（`JWT_SECRET`、Amazon資格情報、`ANTHROPIC_API_KEY`）はここで管理し、
`.gitignore` で `.env` を除外済み。

**DBマイグレーション（Alembic）**: スキーマを版数管理。SQLiteでもPostgreSQLでも同じ手順。
```bash
alembic upgrade head          # 最新スキーマへ
alembic revision --autogenerate -m "説明"   # モデル変更後、差分マイグレーション生成
alembic downgrade -1          # 1つ戻す
```
URLは `settings.database_url`（.env）から取得。SQLiteのALTER制約はバッチモードで吸収。
本番では `AUTO_CREATE_TABLES=false` にして Alembic を唯一のスキーマ管理者にする
（開発は既定 `true` で `create_all` による自動作成のまま手軽に動かせる）。

**PostgreSQLへ移行する場合**:
```bash
pip install "psycopg[binary]>=3.1"
# .env で:  DATABASE_URL=postgresql+psycopg://user:pass@host:5432/amhub
#           AUTO_CREATE_TABLES=false
alembic upgrade head
```

**ログ**: `app/logging_config.py` で1行1イベントの構造化ログ。ログイン成功・承認決裁・取込・
未捕捉例外を記録（監査の起点）。`LOG_LEVEL` で調整。

**エラーハンドリング**: 未捕捉例外は500のJSON `{"detail": ...}` に統一し、内部詳細はログにのみ残す。

## 動作確認の方法（モックのまま確認可能）

前提は Python 環境のみ（`pip install -r requirements.txt`）。Amazon資格情報は不要。
手軽な順に3通り。

**1. 全フローを自動テストで確認（最速・APIの知識不要）**
```bash
python -m tests.test_core            # 在庫・引当の不変条件
python -m tests.test_auth            # 認証・二段階認証・権限
python -m tests.test_catalog_import  # カタログ取込（CSV/Excel）
python -m tests.test_api             # 認証〜全業務〜承認連動を一気通貫
```

**2. コンソールで3フローを通し実行（見て分かる）**
```bash
python -m scripts.run_demo
# カタログ取込→出品、受注→在庫照合→出荷→請求、広告→AI提案→反映 を順に出力
```

**3. ブラウザのGUIで対話的に確認（FastAPI標準の /docs）**
```bash
python -m scripts.dev_seed                              # 管理者+各ロール+サンプル投入
DEV_ECHO_OTP=1 uvicorn app.api.main:app --reload        # 開発用にOTPを応答へ表示
# → http://localhost:8000/docs を開く
```
`/docs` での操作:
1. `POST /auth/login` に `md@am-teams.com` / `DevPass!234`（dev_seedが表示）→ 応答の `dev_code` を控える
2. `POST /auth/verify` に `challenge_id` と `dev_code` → `access_token` を取得
3. 右上「Authorize」にトークンを貼る → 以降すべてのAPIを実行可能
4. 例: `POST /catalog/import` に「メーカーカタログ_記入済みサンプル.xlsx」をアップロード
   → `GET /catalog/drafts` で出品ドラフトを確認 → `POST /approvals/{id}/decide` で承認（出品実行）

> 専用ダッシュボードUIは未着手（設計待ち）だが、`/docs` が全エンドポイントを叩ける
> 対話的GUIになっており、モックのまま業務の流れを一通り確認できる。
> `DEV_ECHO_OTP` はモック時のみ有効で、本番(live)では絶対にコードを出力しない。

## バックエンドAPI（実装済み・業務一通りをカバー）

ドメイン別ルーターで構成。すべて要認証（`Authorization: Bearer <token>`）で、変更系は対応ロールが必要。
一覧系は `?limit=&offset=` のページネーション（`{items,total,limit,offset}`）と絞り込みに対応。

| 機能 | エンドポイント | 権限 |
|---|---|---|
| 認証 | `POST /auth/login` → `POST /auth/verify`、`GET /auth/me`、`POST /auth/change-password` | 全員 |
| アカウント発行 | `POST /auth/users` | admin |
| 商品マスタ | `GET /products`（検索q/状態）、`GET /products/{id}`、`POST /products`、`PATCH /products/{id}` | 参照=全員 / 変更=MD |
| 在庫 | `GET /inventory`（low_stock）、`GET /inventory/{pid}`、`GET /inventory/{pid}/movements`、`POST .../receive`、`POST .../adjust` | 参照=全員 / 変更=物流 |
| カタログ取込 | `POST /catalog/import`（CSV/Excelアップロード） | MD |
| 出品ドラフト | `GET /catalog/drafts`、`GET /catalog/drafts/{id}`、`PATCH /catalog/drafts/{id}`（承認前の手直し） | 参照=全員 / 編集=MD |
| 受注 | `GET /orders`、`GET /orders/{id}`、`POST /orders/ingest` | 参照=全員 / 取込=物流 |
| メーカー発注 | `GET /supplier-pos`、`GET /supplier-pos/{id}`、`POST /supplier-pos/{id}/receive` | 参照=全員 / 入荷=購買or物流 |
| 広告 | `POST /ads/collect`、`GET /ads/recommendations`、`GET /ads/entities` | 収集=マーケ / 参照=全員 |
| 承認 | `GET /approvals`、`GET /approvals/{id}`、`POST /approvals/{id}/decide` | 種別に応じたロール |

**承認の決裁が後続処理を駆動**します（`app/services/workflow.py`）。`POST /approvals/{id}/decide` で承認すると、
出荷承認→CSV/ASN/Invoice、発注承認→発注確定、出品承認→Listings出品、広告承認→Ads反映、までを自動実行します。

検証は `tests/test_api.py`（TestClientで認証〜全業務〜承認連動を一気通貫）。

## カタログ取り込み（実装済み）

メーカー提供のCSV/Excelを、テンプレート「メーカーカタログ_取込テンプレート.xlsx」の
列定義に従って取り込む。依存は標準ライブラリと openpyxl のみ（pandas不要）。

```bash
python -m scripts.import_catalog --file メーカーカタログ_記入済み.xlsx
# CSV(UTF-8 BOM / Shift_JISも自動判定) も可。xlsxは既定で「商品データ」シートを読む。
```

処理の流れ（`app/services/catalog_import.py` → `catalog_service.import_from_file`）:

- 日本語ヘッダを内部フィールドへ正規化。列の順番は自由、未知の列はスペックとして保全。
- `供給状況`＝生産終了かつ`在庫数`0 を**廃盤シグナル**として登録状況より優先（廃盤品に出品ドラフトは作らない）。それ以外の未登録品は**新製品**としてAI出品ドラフトを生成。
- `特徴1〜5`→スペックの訴求点、`画像ファイル名/URL`→`;`区切りで配列化、`¥`・カンマ・`円`付き価格も数値化。
- SKU空・商品名空は**エラー行としてスキップ**、SKU重複は**警告**して最初の行を採用。エラー/警告は行番号つきで報告。

検証は `tests/test_catalog_import.py`（CSV/Excelの一致、値の正規化、廃盤/新製品の分岐、エンドツーエンド取込）。

## 認証・アカウント・権限（実装済み）

- **管理者がアカウントを発行**する方式。最初の管理者は `python -m scripts.create_admin ...` で作成し、
  以降は管理者が `POST /auth/users` で発行する（自由登録なし）。
- ログインは **メール＋パスワード → 二段階認証（メール or SMS のワンタイムコード）** の2段階。
  `POST /auth/login`（コード送信）→ `POST /auth/verify`（コード検証）でアクセストークン(JWT)を取得。
- パスワードは bcrypt でハッシュ化。トークンは有効期限つきJWT。
- **ロール権限（RBAC）**：`admin / logistics / purchasing / merchandising / marketing`。
  各承認は対応ロール（またはadmin）のみ実行可能（`物流→出荷承認`、`購買→発注承認`、
  `MD→出品承認`、`マーケ→広告承認`）。`decided_by` には承認したユーザーのメールが記録される。
- **二段階認証コードの配信もポート化**（`OtpDeliveryPort`）。現在はモックがコンソール出力、
  本番は Twilio(SMS) / SES(メール) を実装して差し替える。

注: SQLiteはタイムゾーンを保持しないため、有効期限比較はUTC正規化して扱っている
（PostgreSQL移行後も安全）。

## 今後のロードマップ

- **データ移行**：SQLite → PostgreSQL、Alembicマイグレーション導入
- **非同期化**：日次バッチ（広告レポート取得・PO取込）をスケジューラ（cron/Celery）化
- **ダッシュボードUI**：承認キュー・在庫・広告提案の画面（FastAPIバックエンドに対し別途構築）
- **監査・権限**：承認者ロール、操作ログ、SP-APIレート制御とリトライ
- **CSV/Excel取込**：メーカーカタログの実ファイルパーサ（pandas/openpyxl）
