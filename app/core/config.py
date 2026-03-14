from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = "Developer Platform & AI Agents"
    app_version: str = "0.2.0"
    environment: str = "dev"
    database_url: str = "sqlite:///./dev_platform.db"

    model_config = SettingsConfigDict(env_prefix="DEVPLATFORM_")


settings = Settings()