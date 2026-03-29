from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    database_url: str = "postgresql+asyncpg://postgres:password@db.xxxxx.supabase.co:5432/postgres"
    cors_origins: list[str] = ["http://localhost:5173", "http://localhost:3000"]
    host: str = "0.0.0.0"
    port: int = 8000

    class Config:
        env_prefix = "FGA_FORGE_"
        env_file = ".env"


settings = Settings()
