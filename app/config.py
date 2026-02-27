from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    environment: str = "dev"
    host: str = "0.0.0.0"
    port: int = 8000

    jwt_secret: str = "change-me"
    jwt_algorithm: str = "HS256"
    jwt_expires_minutes: int = 120

    openai_api_key: str = ""
    openai_model: str = "gpt-4o-mini"
    github_token: str = ""
    github_api_base_url: str = "https://api.github.com"
    github_api_timeout_seconds: int = 25

    mysql_url: str = "mysql+pymysql://impact:impact@localhost:3306/impactdb"
    redis_url: str = "redis://localhost:6379/0"
    rate_limit: str = "20/minute"
    cors_origins: str = "http://localhost:5000,http://127.0.0.1:5000,http://localhost:5173,http://127.0.0.1:5173"


settings = Settings()
