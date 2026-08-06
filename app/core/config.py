from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = "Agent Orchestration Platform"
    app_version: str = "0.3.0"
    environment: str = "dev"
    database_url: str = "sqlite:///./dev_platform.db"
    api_key: str | None = None

    model_config = SettingsConfigDict(env_prefix="DEVPLATFORM_")


settings = Settings()
