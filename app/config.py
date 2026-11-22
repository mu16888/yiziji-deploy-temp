"""Application configuration, sourced from environment variables.

Field names map case-insensitively to the uppercase environment variables
listed in the plan's Global Constraints (e.g. ``database_url`` -> DATABASE_URL).
"""

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # Database / security
    database_url: str = "sqlite:///./app.db"
    secret_key: str = "dev-secret-change-me"

    # Adapter selection
    payment_gateway: str = "mock"
    generation_adapter: str = "mock"

    # WeChat Pay (fake defaults are fine for local/test runs)
    wechat_app_id: str = "wx-fake-app-id"
    wechat_mch_id: str = "fake-mch-id"
    wechat_api_v3_key: str = "fake-api-v3-key"
    wechat_notify_url: str = ""

    # Credits
    credit_expire_days: int = 730
    renew_mode: str = "all_active"
    refund_mode: str = "unused_only"

    # Affiliate / withdrawal
    commission_observation_days: int = 15
    min_withdraw_fen: int = 10000
    self_referral_check: str = "off"

    # Compliance / sharing
    aigc_label: bool = True
    audit_retention_days: int = 190
    share_link_ttl_days: int = 7

    # Auth / generation / worker
    verify_code_dev: str = "123456"
    worker_poll_sec: int = 5

    # DeepSeek LLM (self-owned generation; replaceable backend)
    deepseek_api_key: str = ""
    deepseek_base_url: str = "https://api.deepseek.com"
    deepseek_model: str = "deepseek-chat"


settings = Settings()
