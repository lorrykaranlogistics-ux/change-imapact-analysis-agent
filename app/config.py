from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    environment: str = "dev"
    host: str = "0.0.0.0"
    port: int = 8000

    jwt_secret: str = "change-me"
    jwt_algorithm: str = "HS256"
    jwt_expires_minutes: int = 120

    gemini_api_key: str = Field(default="", validation_alias="GEMINI_API_KEY")
    gemini_model: str = "gemini-1.5-flash"
    gemini_api_base_url: str = "https://generativelanguage.googleapis.com/v1beta"
    gemini_timeout_seconds: int = 30
    github_token: str = Field(default="", validation_alias="GITHUB_TOKEN")
    github_api_base_url: str = "https://api.github.com"
    github_api_timeout_seconds: int = 25
    github_workflow_file: str = "regression-dispatch.yml"
    github_workflow_ref: str = "master"
    github_workflow_lookup_timeout_seconds: int = 60
    github_workflow_timeout_seconds: int = 420
    github_workflow_poll_seconds: int = 5
    microservices_project_path: str = ""

    mysql_url: str = "mysql+pymysql://impact:impact@localhost:3306/impactdb"
    redis_url: str = "redis://localhost:6379/0"
    rate_limit: str = "20/minute"
    cors_origins: str = "http://localhost:5000,http://127.0.0.1:5000,http://localhost:5173,http://127.0.0.1:5173"


settings = Settings()
