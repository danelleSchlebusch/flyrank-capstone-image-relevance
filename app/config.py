from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    database_url: str
    gemini_api_key: str = ""
    embedding_dim: int = 768
    min_similarity: float = 0.70
    min_confidence: float = 0.60
    vision_model: str = "gemini-3.1-flash-lite"

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")


settings = Settings()