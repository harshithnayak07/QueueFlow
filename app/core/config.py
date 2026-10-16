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
    max_image_mb: int = 10  # queue-image upload cap (matches upload UI)
    # CORS origins for browser API calls. Production MUST set this to the
    # exact deployed origin(s); dev keeps "*" for file:// iteration.
    cors_origins: str = "http://127.0.0.1:8000,http://localhost:8000"
    # Google Sign-In client ID. Public identifier (also embedded in login
    # page JS), safe to commit. Override via GOOGLE_CLIENT_ID env if needed.
    google_client_id: str = (
        "650223765036-q2s9vdbg607mett9ld348plp46s36p54.apps.googleusercontent.com"
    )


settings = Settings()
