from functools import lru_cache
from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file="../.env", extra="ignore")
    app_name: str = "Threads Recruitment Manager"
    app_env: str = "development"
    app_version: str = "0.1.0"
    # Single operator token protecting every /api route except health checks,
    # the Meta webhook and OAuth callbacks. Empty only allows unauthenticated
    # access in development (with a log warning); any other environment then
    # responds 503 until it is configured in .env.
    operator_token: str = ""
    database_url: str = "sqlite+aiosqlite:///./threads.db"
    redis_url: str = "redis://redis:6379/0"
    litellm_base_url: str = "http://litellm:4000"
    litellm_master_key: str = ""
    litellm_api_key: str = ""
    hermes_base_url: str = "http://hermes:8642"
    hermes_api_key: str = ""
    hermes_model: str = "hermes-agent"
    # Hermes is an orchestration runtime in front of the same local model.
    # With a small local model that layer spends the request budget on its own
    # session instead of answering, so the strategist uses the plain LiteLLM
    # route by default. Enable this only with a fast model or a cloud
    # provider that can afford the extra turn.
    hermes_for_strategist: bool = False
    # The Hermes service is optional and off by default. When it is off the
    # health report must not degrade the stack.
    hermes_enabled: bool = False
    frontend_url: str = "http://localhost:3000"
    threads_mock_mode: bool = True
    ai_mock_mode: bool = True
    # Ordinary agent calls use a compact context in LiteLLM so a 4B model can
    # stay within a 4 GB GPU budget.  The worker has deterministic fallbacks,
    # so one bounded attempt is safer than multiplying a slow timeout.
    # The value sits above the gateway's own request timeout, because the
    # fallback chain (cloud model, then the local model) runs inside a single
    # gateway request.
    ai_timeout_seconds: float = 280.0
    ai_max_tokens: int = 600
    # Exactly one corrective retry (audit D6/I-10): AIService._request retries
    # 502s only — invalid JSON, an empty answer, an unexpected shape — and never
    # retries a timeout (504), so a slow model cannot hang twice as long.
    ai_retries: int = 1
    hermes_timeout_seconds: float = 200.0
    threads_access_token: str = ""
    threads_app_id: str = ""
    threads_app_secret: str = ""
    threads_redirect_uri: str = "http://localhost:8000/api/threads/oauth/callback"
    threads_api_base_url: str = "https://graph.threads.net"
    threads_request_timeout: float = 20.0
    threads_max_retries: int = 2
    # Worker role. The primary worker publishes and collects data; the agent
    # worker runs the slow model-backed agents in its own process so a manual
    # agent run can never delay a scheduled publication.
    worker_role: str = "primary"
    worker_job_kinds: str = ""
    # Meta webhook callback verification token (set in .env and in the Meta
    # App Dashboard). Leave empty to reject webhook traffic.
    threads_webhook_verify_token: str = ""
    # Optional explicit Fernet source key for stored Threads tokens; when empty
    # a key is derived from LITELLM_MASTER_KEY (documented in docs).
    threads_token_key: str = ""
    # Instagram Business Login / TikTok Login Kit. Empty values keep the
    # provider routes disabled in the default local-only deployment.
    instagram_app_id: str = ""
    instagram_app_secret: str = ""
    instagram_redirect_uri: str = "http://localhost:8000/api/social/oauth/instagram/callback"
    instagram_api_base_url: str = "https://graph.instagram.com"
    instagram_api_version: str = "v25.0"
    tiktok_client_key: str = ""
    tiktok_client_secret: str = ""
    tiktok_redirect_uri: str = "http://localhost:8000/api/social/oauth/tiktok/callback"
    tiktok_api_base_url: str = "https://open.tiktokapis.com"
    social_token_key: str = ""
    # ComfyUI is optional and must be explicitly enabled/configured.
    comfyui_base_url: str = "http://host.docker.internal:8188"
    comfyui_mock_mode: bool = True
    local_video_fallback: bool = True
    local_video_duration_seconds: int = 5
    comfyui_workflow_dir: str = "/app/workflows"
    comfyui_image_workflow: str = ""
    comfyui_video_workflow: str = ""
    comfyui_timeout_seconds: int = 1800
    media_output_dir: str = "/app/data/media"
    media_public_base_url: str = ""
    media_max_bytes: int = 100_000_000
    media_disk_quota_bytes: int = 2_000_000_000
    image_checkpoint: str = ""
    wan_text_encoder: str = ""
    social_local_only: bool = True
    allow_manual_token_import: bool = False
    telegram_bot_token: str = ""
    telegram_channel: str = ""
    telegram_poll_interval_seconds: int = 60
    default_language: str = "uk"
    timezone: str = "Europe/Warsaw"
    auto_post: bool = False
    auto_reply: bool = False
    auto_external_comment: bool = False
    # Single source of truth for publication limits. docker-compose.yml and
    # .env.example mirror these defaults; an AppSetting row or an .env value
    # can only lower them, never silently raise the effective limit.
    max_posts_per_hour: int = 4
    max_replies_per_hour: int = 12
    worker_heartbeat_ttl: int = 120
    # Data retention (audit I-7/D13). The weekly cleanup task is a dry run
    # until the owner explicitly enables deletion (after a backup); days may
    # be tuned per class of data. Live content is never pruned here.
    retention_enabled: bool = False
    retention_activity_days: int = 90
    retention_policy_days: int = 180
    retention_jobs_days: int = 30
    retention_agent_runs_days: int = 90
    # TTL (seconds) for read-only analytics aggregates (audit I-8/D7): the
    # dashboard metrics only change when data does. 0 disables the cache.
    analytics_cache_seconds: int = 120
    allowed_origins: list[str] = ["http://localhost:3000"]

    @field_validator("allowed_origins", mode="before")
    @classmethod
    def parse_origins(cls, value):
        if isinstance(value, str) and not value.lstrip().startswith("["):
            return [item.strip() for item in value.split(",") if item.strip()]
        return value


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
