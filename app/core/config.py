from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_name: str = "QueueFlow AI"
    env: str = "dev"
    database_url: str = "sqlite:///./queueflow.db"
    secret_key: str = "change-me-in-env"
    jwt_algorithm: str = "HS256"
    access_token_expire_minutes: int = 720  # 12h: covers an owner working day
    upload_dir: str = "data/uploads"  # S4: recorded-video storage (gitignored)
    max_video_mb: int = 50  # S4: MVP upload cap (assumption, matches service)


settings = Settings()
