from pathlib import Path
from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT_DIR = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    gemini_api_key: str = ""
    gemini_model: str = "gemini-3.5-flash-lite"
    default_mile_value_brl: float = 30.0
    headless_browser: bool = True
    scrape_timeout_seconds: int = 25
    cors_origins: list[str] = ["*"]

    model_config = SettingsConfigDict(
        env_file=[str(ROOT_DIR / ".env"), ".env"],
        env_file_encoding="utf-8",
        extra="ignore",
    )


settings = Settings()
