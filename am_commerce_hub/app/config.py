"""アプリケーション設定。

環境変数で上書き可能。Amazon系の資格情報は未取得でも動くように
すべて任意（未設定ならモックアダプタが選択される）。
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field

try:
    from dotenv import load_dotenv
    load_dotenv()  # プロジェクト直下の .env を読み込む（存在しなければ無視）
except ImportError:
    pass


def _env_bool(key: str, default: bool) -> bool:
    val = os.getenv(key)
    if val is None:
        return default
    return val.strip().lower() in {"1", "true", "yes", "on"}


@dataclass
class Settings:
    # --- データベース ---
    database_url: str = os.getenv("DATABASE_URL", "sqlite:///./am_commerce_hub.db")
    # 起動時に create_all でテーブルを自動作成するか（開発はTrue / 本番はAlembicに任せFalseでもOK）
    auto_create_tables: bool = _env_bool("AUTO_CREATE_TABLES", True)

    # --- ログ ---
    log_level: str = os.getenv("LOG_LEVEL", "INFO")

    # --- スケジューラ（日次処理の自動化） ---
    scheduler_timezone: str = os.getenv("SCHEDULER_TIMEZONE", "Asia/Tokyo")
    orders_ingest_cron: str = os.getenv("ORDERS_INGEST_CRON", "0 * * * *")   # 毎時0分: 受注取込
    ads_collect_cron: str = os.getenv("ADS_COLLECT_CRON", "0 6 * * *")       # 毎日6:00: 広告収集
    cleanup_cron: str = os.getenv("CLEANUP_CRON", "30 3 * * *")              # 毎日3:30: 期限切れ整理

    # --- 統合モード ---
    # "mock"    : すべての外部APIをモックで動かす
    # "sandbox" : SP-APIサンドボックス（静的モック応答）に実接続。資格情報は必要
    # "live"    : 本番の資格情報・本番エンドポイント
    integration_mode: str = os.getenv("INTEGRATION_MODE", "mock")

    # --- Amazon 資格情報（取得後にここを埋める） ---
    sp_api_refresh_token: str | None = os.getenv("SP_API_REFRESH_TOKEN")
    sp_api_seller_refresh_token: str | None = os.getenv("SP_API_SELLER_REFRESH_TOKEN")
    sp_api_client_id: str | None = os.getenv("SP_API_CLIENT_ID")
    sp_api_client_secret: str | None = os.getenv("SP_API_CLIENT_SECRET")
    # Seller(3P)が独立アプリの場合の専用資格情報。未設定なら上の共有分にフォールバック。
    sp_api_seller_client_id: str | None = os.getenv("SP_API_SELLER_CLIENT_ID")
    sp_api_seller_client_secret: str | None = os.getenv("SP_API_SELLER_CLIENT_SECRET")
    ads_api_client_id: str | None = os.getenv("ADS_API_CLIENT_ID")
    ads_api_client_secret: str | None = os.getenv("ADS_API_CLIENT_SECRET")
    ads_api_refresh_token: str | None = os.getenv("ADS_API_REFRESH_TOKEN")
    ads_profile_id: str | None = os.getenv("ADS_PROFILE_ID")
    # リージョン: na(北米) / eu(欧州) / fe(極東＝日本)。既定は日本向けに fe。
    sp_api_region: str = os.getenv("SP_API_REGION", "fe")
    ads_api_region: str = os.getenv("ADS_API_REGION", "fe")

    # --- AI（Claude） ---
    anthropic_api_key: str | None = os.getenv("ANTHROPIC_API_KEY")
    ai_model: str = os.getenv("AI_MODEL", "claude-3-5-sonnet-latest")

    # --- 広告運用の既定値（既存運用に合わせた初期値） ---
    target_acos: float = float(os.getenv("TARGET_ACOS", "0.20"))   # 目標ACoS 20%
    daily_budget_jpy: int = int(os.getenv("DAILY_BUDGET_JPY", "11000"))

    # --- 連携先（出荷ハブ等） ---
    ais_sftp_host: str | None = os.getenv("AIS_SFTP_HOST")

    # --- 認証 / 二段階認証 ---
    # 本番では必ず環境変数で十分長いランダム値を設定すること
    jwt_secret: str = os.getenv("JWT_SECRET", "dev-only-change-me-please-32+chars-secret")
    jwt_algorithm: str = "HS256"
    access_token_minutes: int = int(os.getenv("ACCESS_TOKEN_MINUTES", "60"))
    otp_expire_minutes: int = int(os.getenv("OTP_EXPIRE_MINUTES", "5"))
    otp_max_attempts: int = int(os.getenv("OTP_MAX_ATTEMPTS", "5"))

    # --- 開発確認用 ---
    # 動作確認を容易にするため、モック時に限りOTPコードをログイン応答へ含める。
    # 本番(live)では無効。値が有効でも use_mock() でない限り出力しない。
    dev_echo_otp: bool = _env_bool("DEV_ECHO_OTP", False)

    def use_mock(self) -> bool:
        return self.integration_mode == "mock"

    @property
    def sp_api_sandbox(self) -> bool:
        """SP-APIのサンドボックス（静的モック）エンドポイントを使うか。"""
        return self.integration_mode == "sandbox"


settings = Settings()
