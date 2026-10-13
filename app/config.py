from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    database_url: str
    gemini_api_key: str = ""
    embedding_dim: int = 768
    min_similarity: float = 0.70
    min_confidence: float = 0.60

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    vision_model: str = "gemini-3.1-flash-lite"

    images_dir: str = "data/images"
    # Price per 1 million tokens, in USD. Copy these from Google's pricing page
    # for the model you use. 0.0 is accurate on the free tier.
    vision_input_price_per_m: float = 0.0
    vision_output_price_per_m: float = 0.0

    daily_budget_usd: float = 1.0
    job_max_attempts: int = 4
    job_backoff_base_seconds: float = 3.0
    job_delay_seconds: float = 1.0
    job_max_consecutive_failures: int = 3


settings = Settings()