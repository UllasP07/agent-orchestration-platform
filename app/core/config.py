from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = "Agent Orchestration Platform"
    app_version: str = "0.4.0"
    environment: str = "dev"
    database_url: str = "sqlite:///./dev_platform.db"
    api_key: str | None = None
    worker_enabled: bool = True
    worker_poll_interval_seconds: float = 0.25
    worker_lease_timeout_seconds: float = 30.0
    worker_heartbeat_interval_seconds: float = 1.0
    worker_recovery_interval_seconds: float = 5.0
    synchronous_wait_timeout_seconds: float = 60.0
    default_step_timeout_seconds: float = 30.0
    default_step_max_attempts: int = 3
    retry_base_delay_seconds: float = 0.1

    model_config = SettingsConfigDict(env_prefix="DEVPLATFORM_")


settings = Settings()
