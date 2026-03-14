from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = "Developer Platform & AI Agents"
    app_version: str = "0.1.0"
    environment: str = "dev"

    model_config = SettingsConfigDict(env_prefix="DEVPLATFORM_")


settings = Settings()